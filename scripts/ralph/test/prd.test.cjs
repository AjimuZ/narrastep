'use strict';
// Tests of the task rules in prd.cjs: selection, attempts, validation, reports and progress log.

const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { test } = require('node:test');

const prdModule = require('../prd.cjs');

const {
  appendProgress,
  brief,
  classifyRun,
  commitMessageProblems,
  finalCommitMessage,
  hasRoadmapEvidence,
  nextStep,
  progressEntries,
  recordBlocked,
  recordFailure,
  recordPassed,
  unblock,
  validatePrd,
  validateRoadmapCoverage,
} = prdModule;

function story(id, priority, extra = {}) {
  return {
    id,
    title: `Title ${id}`,
    description: `Description ${id}`,
    acceptanceCriteria: ['It works'],
    priority,
    passes: false,
    dependsOn: [],
    areas: [],
    notes: '',
    ...extra,
  };
}

const prdOf = (...userStories) => ({ branchName: 'main', userStories });

test('picks the highest priority task whose dependencies passed', () => {
  const prd = prdOf(story('B', 2), story('A', 1, { passes: true }), story('C', 3, { dependsOn: ['B'] }));
  assert.deepEqual(nextStep(prd), { state: 'run', id: 'B' });
});

test('skips a blocked task and its dependents, and continues with an independent one', () => {
  const prd = prdOf(story('A', 1, { blocked: true }), story('B', 2, { dependsOn: ['A'] }), story('C', 3));
  assert.deepEqual(nextStep(prd), { state: 'run', id: 'C' });
});

test('is stuck when only blocked tasks and their dependents remain', () => {
  const prd = prdOf(story('A', 1, { blocked: true }), story('B', 2, { dependsOn: ['A'] }));
  assert.deepEqual(nextStep(prd), { state: 'stuck' });
});

test('never runs an owner task, and a task that needs one waits for it', () => {
  const prd = prdOf(story('O', 1, { owner: true }), story('B', 2, { dependsOn: ['O'] }));
  assert.deepEqual(nextStep(prd), { state: 'stuck' });
});

test('completion ignores open owner tasks but no other open task', () => {
  assert.deepEqual(nextStep(prdOf(story('O', 1, { owner: true }), story('A', 2, { passes: true }))), { state: 'complete' });
  assert.notDeepEqual(nextStep(prdOf(story('A', 1), story('B', 2, { passes: true }))), { state: 'complete' });
});

test('a task is blocked at the second failed attempt, not the first', () => {
  const prd = prdOf(story('A', 1));
  assert.equal(recordFailure(prd, 'A', 'first').blocked, undefined);
  const second = recordFailure(prd, 'A', 'second');
  assert.equal(second.attempts, 2);
  assert.equal(second.blocked, true);
  assert.match(second.blockedReason, /second/);
  assert.equal(second.passes, false);
});

test('a block leaves the task open, and unblock gives it fresh attempts', () => {
  const prd = prdOf(story('A', 1, { attempts: 1 }));
  recordBlocked(prd, 'A', 'needs the owner');
  assert.deepEqual(nextStep(prd), { state: 'stuck' });
  unblock(prd, 'A');
  assert.deepEqual(nextStep(prd), { state: 'run', id: 'A' });
  assert.equal(prd.userStories[0].attempts, 0);
});

test('a failure reason is cut to a bounded size, keeping its end', () => {
  const prd = prdOf(story('A', 1));
  const stored = recordFailure(prd, 'A', `${'x'.repeat(10000)}the end`);
  assert.ok(stored.lastFailure.length <= 4000);
  assert.ok(stored.lastFailure.endsWith('the end'));
});

test('recordPassed clears the failure state', () => {
  const prd = prdOf(story('A', 1, { attempts: 1, lastFailure: 'old' }));
  const stored = recordPassed(prd, 'A', '2026-10-30T10:00:00Z');
  assert.equal(stored.passes, true);
  assert.equal(stored.lastFailure, '');
  assert.equal(stored.verifiedAt, '2026-10-30T10:00:00Z');
});

test('validation reports duplicates, unknown and later dependencies, and missing fields', () => {
  assert.deepEqual(validatePrd(prdOf(story('A', 1), story('B', 2, { dependsOn: ['A'] }))), []);
  assert.match(validatePrd(prdOf(story('A', 1), story('A', 2))).join(), /duplicate id/);
  assert.match(validatePrd(prdOf(story('A', 1), story('B', 1))).join(), /priority 1 is used twice/);
  assert.match(validatePrd(prdOf(story('A', 1, { dependsOn: ['Z'] }))).join(), /unknown dependency/);
  assert.match(validatePrd(prdOf(story('A', 1, { dependsOn: ['B'] }), story('B', 2))).join(), /does not come earlier/);
  assert.match(validatePrd(prdOf(story('A', 1, { areas: ['nope'] }))).join(), /unknown area/);
  assert.match(validatePrd(prdOf(story('A', 1, { acceptanceCriteria: [] }))).join(), /no acceptance criteria/);
  assert.match(validatePrd({ branchName: 'main', userStories: [] }).join(), /empty/);
});

test('a dependency cycle is refused', () => {
  const prd = prdOf(story('A', 1, { dependsOn: ['B'] }), story('B', 2, { dependsOn: ['A'] }));
  assert.match(validatePrd(prd).join(), /does not come earlier/);
});

test('every open roadmap item needs a task that closes it', () => {
  const roadmap = '- [x] P01.1 Done\n- [ ] P02.1 Open\n- [ ] P02.2 Also open\n';
  const good = prdOf(story('A', 1, { roadmapItems: ['P02.1'], closes: ['P02.1'] }), story('B', 2, { closes: ['P02.2'] }));
  assert.deepEqual(validateRoadmapCoverage(good, roadmap), []);
  const uncovered = validateRoadmapCoverage(prdOf(story('A', 1, { closes: ['P02.1'] })), roadmap);
  assert.match(uncovered.join(), /P02\.2 is open and no task covers it/);
  const notClosed = validateRoadmapCoverage(prdOf(story('A', 1, { roadmapItems: ['P02.1', 'P02.2'], closes: ['P02.1'] })), roadmap);
  assert.match(notClosed.join(), /P02\.2 is open and no task closes it/);
  const unknown = validateRoadmapCoverage(prdOf(story('A', 1, { closes: ['P02.1', 'P02.2', 'P99.1'] })), roadmap);
  assert.match(unknown.join(), /P99\.1 does not exist/);
  const already = validateRoadmapCoverage(prdOf(story('A', 1, { closes: ['P01.1', 'P02.1', 'P02.2'] })), roadmap);
  assert.match(already.join(), /P01\.1, which is already checked/);
});

test('commit messages follow the project rules', () => {
  assert.deepEqual(commitMessageProblems('Show storage per project\n\nBody with the reason.'), []);
  assert.match(commitMessageProblems('').join(), /empty/);
  assert.match(commitMessageProblems('Add a thing.').join(), /period/);
  assert.match(commitMessageProblems('Added a thing').join(), /imperative/);
  assert.match(commitMessageProblems(`Add ${'x'.repeat(70)}`).join(), /characters/);
  assert.match(commitMessageProblems('Add a thing\nBody').join(), /blank line/);
  assert.match(commitMessageProblems('Add a thing\n\nCo-Authored-By: someone').join(), /reference/);
  assert.match(commitMessageProblems('Add a thing written with Claude').join(), /reference/);
  assert.match(commitMessageProblems('Add a thing\n\nThe AI wrote it').join(), /reference/);
  // Ordinary product words stay allowed.
  assert.deepEqual(commitMessageProblems('Show installed models and providers'), []);
});

test('the commit message always carries one task trailer', () => {
  assert.equal(finalCommitMessage({ commitMessage: 'Add a thing' }, 'P08.6'), 'Add a thing\n\nTask: P08.6\n');
  assert.equal(finalCommitMessage({ commitMessage: 'Add a thing\n\nTask: P08.6' }, 'P08.6'), 'Add a thing\n\nTask: P08.6\n');
});

test('roadmap evidence must be dated and belong to the item', () => {
  const inline = '- [x] P01.1 Item\n  Evidence (2026-10-29): it passed.\n- [x] P01.2 Other\n\nExit: done\n';
  assert.equal(hasRoadmapEvidence(inline, 'P01.1'), true);
  assert.equal(hasRoadmapEvidence(inline, 'P01.2'), false);
  const atSectionEnd = '- [x] P09.1 Item\n  Exit: x\n\nP09.1 evidence (2026-10-29): it passed.\n';
  assert.equal(hasRoadmapEvidence(atSectionEnd, 'P09.1'), true);
  const progressOnly = '- [x] P01.1 Item\n  Progress (2026-10-29, not yet checked): partly.\n';
  assert.equal(hasRoadmapEvidence(progressOnly, 'P01.1'), false);
  assert.equal(hasRoadmapEvidence('- [x] P01.1 Item\n  Evidence: no date.\n', 'P01.1'), false);
});

test('the brief holds the task, its failure, patterns and recent progress only', () => {
  const prd = prdOf(
    story('A', 1, { passes: true }),
    story('B', 2, { dependsOn: ['A'], attempts: 1, lastFailure: 'lint failed here', closes: ['P01.1'], notes: 'Look at X' }),
    story('C', 3, { description: 'NOT THIS TASK' }),
  );
  const entries = Array.from({ length: 6 }, (_, index) => `## [2026-10-0${index + 1}] - T${index}\nentry ${index}\n---\n`).join('\n');
  const text = brief(prd, 'B', `# Log\n\n## Codebase Patterns\n- Pattern one\n---\n\n${entries}`);
  assert.match(text, /# Your task: B - Title B/);
  assert.match(text, /A \(passed\)/);
  assert.match(text, /Attempt 2 of 2/);
  assert.match(text, /lint failed here/);
  assert.match(text, /Pattern one/);
  assert.match(text, /Look at X/);
  assert.match(text, /entry 5/);
  assert.doesNotMatch(text, /entry 2/, 'old sessions are not repeated');
  assert.doesNotMatch(text, /NOT THIS TASK/);
});

test('progress keeps a bounded log, moves the rest to the history and skips known learnings', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'ralph-progress-'));
  const paths = { progress: path.join(dir, 'progress.txt'), history: path.join(dir, 'archive', 'history.txt') };
  fs.writeFileSync(paths.progress, '# Log\n\n## Codebase Patterns\n- Known fact\n---\n');
  for (let index = 0; index < 15; index += 1) {
    appendProgress(paths, `## [2026-10-30] - T${index}\ndetail ${index}`, index === 0 ? ['New fact', 'Known fact'] : []);
  }
  const text = fs.readFileSync(paths.progress, 'utf8');
  assert.equal(progressEntries(text).length, 12);
  assert.match(text, /detail 14/);
  assert.doesNotMatch(text, /detail 2\n/);
  assert.match(fs.readFileSync(paths.history, 'utf8'), /detail 2\n/);
  assert.equal(text.match(/Known fact/g).length, 1);
  assert.equal(text.match(/New fact/g).length, 1);
  assert.ok(text.indexOf('New fact') < text.indexOf('T3'), 'patterns stay above the log');
  fs.rmSync(dir, { recursive: true, force: true });
});

test('a usage limit is told apart from a failed session and a normal end', () => {
  assert.equal(classifyRun({ result: 'Done', is_error: false }, 0), 'ok');
  assert.equal(classifyRun({ result: 'You have reached your usage limit', is_error: true }, 1), 'limit');
  assert.equal(classifyRun({ result: 'API Error: 529 overloaded', is_error: true }, 1), 'limit');
  assert.equal(classifyRun({ missing: true }, 1, 'Claude usage limit reached'), 'limit');
  assert.equal(classifyRun({ result: 'Invalid API key', is_error: true }, 1), 'error');
  assert.equal(classifyRun(null, 0), 'error');
});

test('text quoted by a session or the end of a killed one is not taken for a limit', () => {
  assert.equal(classifyRun({ result: 'Added rate limit handling', is_error: false }, 0), 'ok');
  assert.equal(classifyRun({ result: 'Crashed', is_error: true }, 1, 'the code says rate limit'), 'error');
  assert.equal(classifyRun({ missing: true }, 124, 'rate limit in the last output'), 'error');
});

test('every task runs the base areas and its own, and the real stack at every fifth', () => {
  const prd = prdOf(story('A', 1, { areas: ['stack', 'docs'] }), story('B', 2));
  assert.deepEqual(prdModule.verificationAreas(prd, 'B', ['docs', 'web']), ['docs', 'web']);
  assert.deepEqual(prdModule.verificationAreas(prd, 'A', ['docs', 'web']), ['docs', 'web', 'stack']);
  const four = prdOf(...['A', 'B', 'C', 'D'].map((id, index) => story(id, index + 1, { passes: true })), story('E', 5));
  assert.ok(prdModule.verificationAreas(four, 'E', ['docs']).includes('stack'));
  assert.ok(!prdModule.verificationAreas(four, 'E', ['docs'], 0).includes('stack'));
});

test('a Compose configuration may not reach the host', () => {
  const { composeProblems } = prdModule;
  const allowed = { type: 'bind', source: 'C:\\project\\data\\models-import', target: '/import', read_only: true };
  assert.deepEqual(composeProblems({ services: { api: { volumes: [{ type: 'volume', source: 'x' }, allowed] } } }), []);
  const problems = (service) => composeProblems({ services: { api: service } }).join();
  assert.match(problems({ volumes: [{ ...allowed, read_only: false }] }), /must be read-only/);
  assert.match(problems({ volumes: [{ type: 'bind', source: '/var/run/docker.sock' }] }), /Docker socket/);
  assert.match(problems({ volumes: [{ type: 'bind', source: '/home/owner' }] }), /host folder/);
  assert.match(problems({ privileged: true }), /privileged/);
  assert.match(problems({ network_mode: 'host' }), /network_mode host/);
  assert.match(problems({ cap_add: ['SYS_ADMIN'] }), /cap_add/);
  assert.match(problems({ devices: ['/dev/sda:/dev/sda'] }), /devices/);
});

test('the GitHub repository is read from either form of remote address', () => {
  const { githubRepository } = prdModule;
  assert.equal(githubRepository('https://github.com/AjimuZ/narrastep.git\n'), 'AjimuZ/narrastep');
  assert.equal(githubRepository('git@github.com:AjimuZ/narrastep.git'), 'AjimuZ/narrastep');
  assert.equal(githubRepository('C:/temporary/remote'), null);
});
