'use strict';
// Runs the real ralph.sh, verify.sh and prd.cjs in a temporary repository with a stand-in for the
// agent, whose behavior each test scripts. Needs bash and git, and no Docker or Claude Code.

const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { afterEach, test } = require('node:test');

const SOURCE = path.resolve(__dirname, '..');
const FILES = ['ralph.sh', 'verify.sh', 'check.sh', 'status.sh', 'stop.sh', 'prd.cjs', 'CLAUDE.md', 'settings.json'];
const temporaryRoots = [];

afterEach(() => {
  for (const root of temporaryRoots.splice(0)) fs.rmSync(root, { recursive: true, force: true });
});

function git(root, ...args) {
  const result = spawnSync('git', args, { cwd: root, encoding: 'utf8' });
  assert.equal(result.status, 0, `git ${args.join(' ')}: ${result.stderr}`);
  return result.stdout.trim();
}

function task(id, priority, extra = {}) {
  return {
    id,
    title: `Task ${id}`,
    description: `Do ${id}`,
    acceptanceCriteria: ['It works'],
    priority,
    passes: false,
    dependsOn: [],
    areas: [],
    closes: [`P01.${priority}`],
    notes: '',
    ...extra,
  };
}

/**
 * Creates a repository with the runner, a task list and stand-ins for the agent and the sandbox.
 * `agent` is a bash script body, with $1 set to the number of this agent run and $TASK to the task.
 */
function setup(tasks, agent) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'ralph-run-'));
  temporaryRoots.push(root);
  const dir = path.join(root, 'scripts', 'ralph');
  fs.mkdirSync(path.join(root, 'docs'), { recursive: true });
  fs.mkdirSync(dir, { recursive: true });
  for (const file of FILES) fs.copyFileSync(path.join(SOURCE, file), path.join(dir, file));
  fs.writeFileSync(path.join(root, '.gitignore'), 'scripts/ralph/.state/\n.fake/\n');
  // The same line endings as the real repository, whatever the global Git settings say.
  fs.writeFileSync(path.join(root, '.gitattributes'), '* text=auto eol=lf\n');
  // The docs area always runs; the real link check has nothing to look at here.
  fs.mkdirSync(path.join(root, 'scripts'), { recursive: true });
  fs.writeFileSync(path.join(root, 'scripts', 'check_docs.py'), 'print("links ok")\n');
  fs.mkdirSync(path.join(root, 'tests'));
  fs.writeFileSync(path.join(root, 'tests', 'existing.test.js'), "test('kept', () => {});\n");
  fs.writeFileSync(path.join(dir, 'prd.json'), JSON.stringify({ project: 'T', branchName: 'main', userStories: tasks }, null, 2));
  fs.writeFileSync(path.join(dir, 'progress.txt'), '# Ralph Progress Log\n\n## Codebase Patterns\n- Known\n---\n');
  const items = tasks.flatMap((entry) => entry.closes ?? []);
  fs.writeFileSync(path.join(root, 'docs', 'ROADMAP.md'), `${items.map((item) => `- [ ] ${item} Item\n`).join('')}\nExit: all\n`);
  fs.writeFileSync(path.join(root, 'docs', 'STATUS.md'), '# Status\n');

  const fake = path.join(root, '.fake');
  fs.mkdirSync(fake);
  // Every stand-in lives in the ignored .fake folder, so it never shows up as a changed file.
  fs.writeFileSync(
    path.join(fake, 'claude'),
    `#!/usr/bin/env bash
FAKE="${toShellPath(fake)}"
if [ "$1" = "auth" ]; then
  [ -f "$FAKE/logged-out" ] && { echo '{"loggedIn": false}'; exit 1; }
  echo '{"loggedIn": true}'
  exit 0
fi
printf '%s\\n' "$@" >> "$FAKE/arguments"
cat > "$FAKE/last-prompt"
count=$(( $(cat "$FAKE/count" 2>/dev/null || echo 0) + 1 ))
echo "$count" > "$FAKE/count"
TASK="$(sed -n 's/^# Your task: \\([^ ]*\\) .*/\\1/p' "$FAKE/last-prompt")"
echo "$TASK" >> "$FAKE/tasks"
set -- "$count"
${agent}
echo '{"type":"result","is_error":false,"result":"Finished"}'
`,
  );
  fs.writeFileSync(path.join(fake, 'sandbox'), `#!/usr/bin/env bash\ncd "${toShellPath(root)}" && bash -c "$1"\n`);
  fs.chmodSync(path.join(fake, 'claude'), 0o755);
  fs.chmodSync(path.join(fake, 'sandbox'), 0o755);
  git(root, 'init', '--quiet', '--initial-branch=main');
  git(root, 'config', 'user.name', 'Test');
  git(root, 'config', 'user.email', 'test@example.invalid');
  git(root, 'add', '--all');
  git(root, 'commit', '--quiet', '-m', 'Start');
  return { root, dir, fake };
}

function toShellPath(file) {
  if (process.platform !== 'win32') return file;
  return file.replace(/\\/g, '/').replace(/^([A-Za-z]):/, (_, drive) => `/${drive.toLowerCase()}`);
}

function run(env, args = [], extraEnv = {}) {
  const result = spawnSync('bash', [path.join(env.dir, 'ralph.sh'), ...args], {
    cwd: env.root,
    encoding: 'utf8',
    timeout: 120000,
    env: { ...runnerEnvironment(env), ...extraEnv },
  });
  return { status: result.status, output: `${result.stdout}${result.stderr}` };
}

/** The environment of a run here: stand-ins, no Docker, no waits, and only the docs area to check. */
function runnerEnvironment(env) {
  return {
    ...process.env,
    CLAUDECODE: '',
    RALPH_ALLOW_NESTED: '',
    RALPH_CLAUDE_BIN: path.join(env.fake, 'claude'),
    RALPH_SANDBOX: path.join(env.fake, 'sandbox'),
    RALPH_PAUSE_SECONDS: '0',
    RALPH_LIMIT_WAIT_SECONDS: '0',
    RALPH_SESSION_WAIT_SECONDS: '0',
    RALPH_DOCKER: '0',
    RALPH_BASE_AREAS: 'docs',
    RALPH_STACK_EVERY: '0',
    RALPH_MIN_FREE_DISK_GB: '0',
  };
}

const readPrd = (env) => JSON.parse(fs.readFileSync(path.join(env.dir, 'prd.json'), 'utf8'));
const stateOf = (env, id) => readPrd(env).userStories.find((entry) => entry.id === id);
const agentRuns = (env) => Number(fs.readFileSync(path.join(env.fake, 'count'), 'utf8').trim());
const clean = (env) => git(env.root, 'status', '--porcelain') === '';

// A finished task as a well-behaved agent leaves it. The roadmap tick follows the task's item.
const FINISH = `
  item="P01.\${TASK#T}"
  sed -i "s/^- \\[ \\] $item /- [x] $item /" docs/ROADMAP.md
  sed -i "/^- \\[x\\] $item /a\\\\  Evidence (2026-10-30): checked." docs/ROADMAP.md
  echo "$TASK done" >> docs/STATUS.md
  echo "$TASK" > "feature-$TASK.txt"
  mkdir -p scripts/ralph/.state
  cat > scripts/ralph/.state/result.json <<JSON
{"outcome":"done","summary":"Did $TASK","commitMessage":"Add feature $TASK","learnings":["Learned on $TASK"],"notRun":[]}
JSON
`;
const REPORT = (outcome, summary) => `
  mkdir -p scripts/ralph/.state
  echo '{"outcome":"${outcome}","summary":"${summary}"}' > scripts/ralph/.state/result.json
`;

test('a verified task is committed by the runner with its trailer, and the run completes', () => {
  const env = setup([task('T1', 1), task('T2', 2, { dependsOn: ['T1'] })], FINISH);
  const { status, output } = run(env);
  assert.equal(status, 0, output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.equal(stateOf(env, 'T2').passes, true);
  assert.equal(agentRuns(env), 2);
  assert.ok(clean(env), 'nothing is left uncommitted');
  assert.match(git(env.root, 'log', '-1', '--format=%B'), /^Add feature T2\n\nTask: T2/);
  assert.match(git(env.root, 'log', '-1', '--format=%B', 'HEAD~1'), /^Add feature T1\n\nTask: T1/);
  assert.equal(git(env.root, 'rev-parse', '--abbrev-ref', 'HEAD'), 'main');
  const progress = fs.readFileSync(path.join(env.dir, 'progress.txt'), 'utf8');
  assert.match(progress, /T1 - passed\nDid T1/);
  assert.ok(progress.indexOf('Learned on T1') < progress.indexOf('T1 - passed'), 'learnings go to the patterns section');
});

test('each agent process is fresh and runs with limited permissions', () => {
  const env = setup([task('T1', 1)], FINISH);
  assert.equal(run(env).status, 0);
  const args = fs.readFileSync(path.join(env.fake, 'arguments'), 'utf8').split('\n');
  assert.ok(args.includes('dontAsk'));
  assert.ok(args.includes('--strict-mcp-config'));
  assert.ok(args.includes('scripts/ralph/settings.json'));
  assert.ok(!args.some((argument) => /dangerously|bypassPermissions|--continue|--resume/.test(argument)));
  const settings = JSON.parse(fs.readFileSync(path.join(SOURCE, 'settings.json'), 'utf8'));
  for (const command of ['git push', 'git commit', 'git reset', 'docker volume', 'docker compose', 'rm ']) {
    assert.ok(settings.permissions.deny.some((rule) => rule.startsWith(`Bash(${command}`)), `${command} is denied`);
  }
  assert.ok(settings.permissions.deny.includes('Read(./data/**)'), "the owner's recordings stay unread");
  assert.ok(!settings.permissions.allow.some((rule) => /^Bash\((git (push|commit)|\*)/.test(rule)));
  assert.match(fs.readFileSync(path.join(env.fake, 'last-prompt'), 'utf8'), /# Ralph agent instructions[\s\S]*# Your task: T1/);
});

test('a claim of completion without a report is a failed attempt, and two block the task', () => {
  const env = setup([task('T1', 1)], 'echo "<promise>COMPLETE</promise>"; echo edit >> docs/STATUS.md');
  const { status, output } = run(env);
  assert.equal(status, 5, output);
  const state = stateOf(env, 'T1');
  assert.equal(state.passes, false);
  assert.equal(state.blocked, true);
  assert.equal(state.attempts, 2);
  assert.match(state.blockedReason, /without a usable report/);
  assert.equal(git(env.root, 'log', '-1', '--format=%s'), 'Record task T1 as blocked');
  assert.ok(clean(env));
  assert.match(git(env.root, 'stash', 'list'), /ralph blocked T1/, 'the partial work stays recoverable');
});

test('a done report that fails verification is not committed, and the retry sees the failure', () => {
  const env = setup([task('T1', 1, { verify: ['test -f proof.txt'] })], `
    ${FINISH}
    if [ "$1" -eq 2 ]; then grep -q "Verification failed" "${'$'}{FAKE}/last-prompt" && touch proof.txt; fi
  `);
  const { status, output } = run(env);
  assert.equal(status, 0, output);
  assert.equal(agentRuns(env), 2);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.equal(git(env.root, 'log', '--format=%s', '--grep=Task: T1').split('\n').length, 1);
});

test('the commit does not happen and passes stays false while verification keeps failing', () => {
  const env = setup([task('T1', 1, { verify: ['false'] })], FINISH);
  const head = git(env.root, 'rev-parse', 'HEAD');
  assert.equal(run(env).status, 5);
  assert.equal(stateOf(env, 'T1').passes, false);
  assert.equal(git(env.root, 'log', '--format=%s', '--grep=Add feature').length, 0);
  assert.equal(git(env.root, 'rev-parse', 'HEAD~1'), head, 'only the blocked state was committed');
});

test('a blocked task and its dependents are passed over for independent tasks', () => {
  const env = setup(
    [task('T1', 1), task('T2', 2, { dependsOn: ['T1'] }), task('T3', 3)],
    `if [ "$TASK" = "T1" ]; then ${REPORT('blocked', 'Needs an owner decision')} else ${FINISH} fi`,
  );
  const { status, output } = run(env);
  assert.equal(status, 5, output);
  assert.equal(stateOf(env, 'T1').blocked, true);
  assert.match(stateOf(env, 'T1').blockedReason, /owner decision/);
  assert.equal(stateOf(env, 'T2').passes, false);
  assert.equal(stateOf(env, 'T3').passes, true);
  assert.deepEqual(fs.readFileSync(path.join(env.fake, 'tasks'), 'utf8').trim().split('\n'), ['T1', 'T3']);
});

test('owner tasks are never given to an agent and do not prevent completion', () => {
  const env = setup([task('T1', 1), task('O', 2, { owner: true, closes: ['P01.2'] })], FINISH);
  const { status, output } = run(env);
  assert.equal(status, 0, output);
  assert.equal(agentRuns(env), 1);
  assert.equal(stateOf(env, 'O').passes, false);
});

test('an agent that marks its own task as passed gains nothing and loses the attempt', () => {
  const env = setup(
    [task('T1', 1)],
    `sed -i 's/"passes": false/"passes": true/' scripts/ralph/prd.json; ${REPORT('done', 'I say so')}`,
  );
  assert.equal(run(env, ['1']).status, 1);
  const state = stateOf(env, 'T1');
  assert.equal(state.passes, false, 'the state file was restored');
  assert.equal(state.attempts, 1, 'the counter survives the restoration');
  assert.match(state.lastFailure, /only the runner may change/);
});

test('an agent that changes the runner loses the attempt and the change', () => {
  const env = setup([task('T1', 1)], `echo tampered >> scripts/ralph/verify.sh; ${FINISH}`);
  const before = fs.readFileSync(path.join(env.dir, 'verify.sh'), 'utf8');
  assert.equal(run(env, ['1']).status, 1);
  assert.equal(fs.readFileSync(path.join(env.dir, 'verify.sh'), 'utf8'), before);
  assert.match(stateOf(env, 'T1').lastFailure, /runner's own files/);
  assert.equal(stateOf(env, 'T1').attempts, 1);
});

test('history that moves during an iteration stops the run', () => {
  const env = setup([task('T1', 1)], `git commit --allow-empty --quiet -m "Sneak"; ${FINISH}`);
  const { status, output } = run(env);
  assert.equal(status, 4, output);
  assert.equal(stateOf(env, 'T1').passes, false);
});

test('a usage limit waits without counting an attempt, then the task continues', () => {
  const env = setup([task('T1', 1)], `
    if [ "$1" -eq 1 ]; then echo '{"type":"result","is_error":true,"result":"You have reached your usage limit"}'; exit 1; fi
    ${FINISH}
  `);
  const { status, output } = run(env, [], { RALPH_LIMIT_WAIT_SECONDS: '1' });
  assert.equal(status, 0, output);
  assert.match(output, /usage limit reached; waiting 1 s/);
  assert.equal(stateOf(env, 'T1').attempts ?? 0, 0);
});

test('a limit that does not clear stops the run and --resume continues the same task', () => {
  const env = setup([task('T1', 1)], `
    if [ "$1" -eq 1 ]; then
      echo partial > partial.txt
      echo '{"type":"result","is_error":true,"result":"usage limit"}'; exit 1
    fi
    ${FINISH}
  `);
  assert.equal(run(env).status, 3);
  assert.equal(stateOf(env, 'T1').attempts ?? 0, 0);
  assert.ok(fs.existsSync(path.join(env.root, 'partial.txt')));
  const refused = run(env);
  assert.equal(refused.status, 2);
  assert.match(refused.output, /--resume/);
  const resumed = run(env, ['--resume']);
  assert.equal(resumed.status, 0, resumed.output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.match(git(env.root, 'show', '--stat', '--format=', 'HEAD'), /partial\.txt/);
});

test('repeated session failures stop the run without blaming the task', () => {
  const env = setup([task('T1', 1)], 'echo "Invalid API key"; exit 1');
  const { status, output } = run(env);
  assert.equal(status, 3, output);
  assert.equal(agentRuns(env), 6);
  const state = stateOf(env, 'T1');
  assert.equal(state.attempts ?? 0, 0);
  assert.notEqual(state.blocked, true);
});

test('a passing failure such as a network outage costs no attempt and no iteration', () => {
  const env = setup([task('T1', 1)], `
    if [ "$1" -lt 4 ]; then echo "getaddrinfo ENOTFOUND api"; exit 1; fi
    ${FINISH}
  `);
  // A limit of 2 is reached only if the failures count as iterations, and then T1 never finishes.
  const { status, output } = run(env, ['2']);
  assert.equal(status, 0, output);
  assert.equal(agentRuns(env), 4);
  assert.equal(stateOf(env, 'T1').attempts ?? 0, 0);
  assert.equal(stateOf(env, 'T1').passes, true);
});

test('a login that has expired refuses the start with a clear message', () => {
  const env = setup([task('T1', 1)], FINISH);
  fs.writeFileSync(path.join(env.fake, 'logged-out'), '');
  const { status, output } = run(env);
  assert.equal(status, 2);
  assert.match(output, /not logged in/);
  assert.equal(fs.existsSync(path.join(env.fake, 'count')), false);
});

test('a verification that hangs counts as a failed attempt', () => {
  const env = setup([task('T1', 1, { verify: ['sleep 20'] })], FINISH);
  const { status, output } = run(env, ['1'], { RALPH_VERIFY_TIMEOUT_SECONDS: '2' });
  assert.equal(status, 1, output);
  assert.equal(stateOf(env, 'T1').passes, false);
  assert.match(stateOf(env, 'T1').lastFailure, /did not finish in 2 s/);
});

test('a run killed in the middle of an iteration continues with --resume', async () => {
  const env = setup([task('T1', 1)], `
    if [ "$1" -eq 1 ]; then echo partial > partial.txt; sleep 3; exit 1; fi
    ${FINISH}
  `);
  const active = spawn('bash', [path.join(env.dir, 'ralph.sh')], { cwd: env.root, stdio: 'ignore', env: runnerEnvironment(env) });
  const ended = new Promise((resolve) => active.on('close', resolve));
  const partial = path.join(env.root, 'partial.txt');
  for (let waited = 0; waited < 40 && !fs.existsSync(partial); waited += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  assert.ok(fs.existsSync(partial), 'the agent started');
  active.kill('SIGKILL');
  await ended;
  await new Promise((resolve) => setTimeout(resolve, 3500));

  const resumed = run(env, ['--resume']);
  assert.equal(resumed.status, 0, resumed.output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.equal(stateOf(env, 'T1').attempts ?? 0, 0, 'being killed costs no attempt');
  assert.match(git(env.root, 'show', '--stat', '--format=', 'HEAD'), /partial\.txt/);
});

test('the job of a task runs before its agent, once it passed, and its output reaches the brief', () => {
  const env = setup(
    [task('T1', 1, { verify: ['test -f proof.txt'], job: { command: 'echo run >> .fake/job-count; echo measured 42 seconds', timeoutSeconds: 30 } })],
    `
    grep -q "measured 42 seconds" "$FAKE/last-prompt" && touch "$FAKE/saw-output"
    ${FINISH}
    if [ "$1" -eq 2 ]; then touch proof.txt; fi
  `,
  );
  const { status, output } = run(env);
  assert.equal(status, 0, output);
  assert.equal(agentRuns(env), 2);
  assert.ok(fs.existsSync(path.join(env.fake, 'saw-output')));
  assert.equal(fs.readFileSync(path.join(env.fake, 'job-count'), 'utf8').trim(), 'run', 'a job that passed is not repeated');
});

test('a job that failed runs again on the next attempt and its status reaches the agent', () => {
  const env = setup(
    [task('T1', 1, { job: { command: 'echo run >> .fake/job-count; exit 3', timeoutSeconds: 30 } })],
    `
    grep -q "exit status 3" "$FAKE/last-prompt" && touch "$FAKE/saw-status"
    ${REPORT('failed', 'Not yet')}
  `,
  );
  assert.equal(run(env).status, 5);
  assert.ok(fs.existsSync(path.join(env.fake, 'saw-status')));
  assert.equal(fs.readFileSync(path.join(env.fake, 'job-count'), 'utf8').trim().split('\n').length, 2);
});

/** Adds a bare `origin` with the current history, and returns its path. */
function addOrigin(env) {
  const remote = fs.mkdtempSync(path.join(os.tmpdir(), 'ralph-remote-'));
  temporaryRoots.push(remote);
  git(remote, 'init', '--quiet', '--bare', '--initial-branch=main');
  git(env.root, 'remote', 'add', 'origin', remote);
  git(env.root, 'push', '--quiet', 'origin', 'main');
  return remote;
}

test('each verified commit reaches origin, and tags stay local', () => {
  const env = setup([task('T1', 1), task('T2', 2)], FINISH);
  const remote = addOrigin(env);
  const { status, output } = run(env);
  assert.equal(status, 0, output);
  assert.equal(git(remote, 'rev-parse', 'main'), git(env.root, 'rev-parse', 'HEAD'));
  assert.equal(git(remote, 'log', '--format=%s', '-2'), 'Add feature T2\nAdd feature T1');
  assert.match(git(env.root, 'tag', '--list'), /^ralph-start-/, 'a local point to return to');
  assert.equal(git(remote, 'tag', '--list'), '');
  assert.match(output, /pushed 1 commit\(s\)/);
});

test('a blocked state is published too, without the work set aside', () => {
  const env = setup([task('T1', 1)], `echo wip > wip.txt; ${REPORT('blocked', 'Needs a decision')}`);
  const remote = addOrigin(env);
  assert.equal(run(env).status, 5);
  assert.equal(git(remote, 'log', '--format=%s', '-1', 'main'), 'Record task T1 as blocked');
  assert.doesNotMatch(git(remote, 'ls-tree', '-r', '--name-only', 'main'), /wip\.txt/);
});

test('when origin moves during a run the push is refused without force and the run stops', () => {
  const env = setup([task('T1', 1), task('T2', 2)], `
    ${FINISH}
    git clone --quiet "$RALPH_TEST_REMOTE" "$FAKE/other" 2>/dev/null
    git -C "$FAKE/other" -c user.name=Other -c user.email=o@example.invalid commit --quiet --allow-empty -m "Work from elsewhere"
    git -C "$FAKE/other" push --quiet origin main
  `);
  const remote = addOrigin(env);
  const { status, output } = run(env, [], { RALPH_TEST_REMOTE: remote });
  assert.equal(status, 7, output);
  assert.match(output, /integrate/);
  assert.equal(git(remote, 'log', '--format=%s', '-1', 'main'), 'Work from elsewhere', 'origin kept its commit');
  assert.equal(stateOf(env, 'T1').passes, true, 'the verified work stays committed locally');
  assert.equal(agentRuns(env), 1);
});

test('an unreachable origin does not stop the work, and a later run publishes it', () => {
  const env = setup([task('T1', 1)], FINISH);
  const remote = addOrigin(env);
  git(env.root, 'remote', 'set-url', 'origin', path.join(env.root, 'no-such-remote'));
  const first = run(env);
  assert.equal(first.status, 0, first.output);
  assert.match(first.output, /push failed, keeping 1 commit/);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.notEqual(git(remote, 'rev-parse', 'main'), git(env.root, 'rev-parse', 'HEAD'));

  git(env.root, 'remote', 'set-url', 'origin', remote);
  const second = run(env);
  assert.equal(second.status, 0, second.output);
  assert.equal(git(remote, 'rev-parse', 'main'), git(env.root, 'rev-parse', 'HEAD'));
});

test('nothing is published when a waiting commit holds a secret, even one removed later', () => {
  const env = setup([task('T1', 1)], FINISH);
  const remote = addOrigin(env);
  const before = git(remote, 'rev-parse', 'main');
  fs.writeFileSync(path.join(env.root, 'config.txt'), `token=${'ghp_'}${'a1'.repeat(20)}\n`);
  git(env.root, 'add', '--all');
  git(env.root, 'commit', '--quiet', '-m', 'Add configuration');
  fs.rmSync(path.join(env.root, 'config.txt'));
  git(env.root, 'add', '--all');
  git(env.root, 'commit', '--quiet', '-m', 'Remove configuration');
  const { status, output } = run(env);
  assert.equal(status, 8, output);
  assert.match(output, /looks like a secret/);
  assert.equal(git(remote, 'rev-parse', 'main'), before);
  assert.equal(fs.existsSync(path.join(env.fake, 'count')), false, 'no agent started');
});

test('nothing is published when a waiting commit message names an authoring tool', () => {
  const env = setup([task('T1', 1)], FINISH);
  const remote = addOrigin(env);
  const before = git(remote, 'rev-parse', 'main');
  git(env.root, 'commit', '--quiet', '--allow-empty', '-m', 'Add a thing', '-m', `Co-${'Authored'}-By: someone`);
  assert.equal(run(env).status, 8);
  assert.equal(git(remote, 'rev-parse', 'main'), before);
});

test('the owner session records numbers only, then commits and pushes', async () => {
  const env = setup([task('T1', 1, { passes: true, closes: [] }), task('P05.6', 2, { owner: true, closes: ['P05.6'] }), task('P07-CHECK', 3, { owner: true, closes: [], roadmapItems: ['P05.6'] })], '');
  fs.writeFileSync(path.join(env.root, 'docs', 'ROADMAP.md'), '- [ ] P05.6 Manual test.\n- [x] P01.1 Done.\n');
  fs.writeFileSync(path.join(env.root, 'docs', 'STATUS.md'), '# Status\n\n## Last documentation check\n\nOlder.\n');
  git(env.root, 'add', '--all');
  git(env.root, 'commit', '--quiet', '-m', 'Prepare');
  const remote = addOrigin(env);

  const secretText = 'PRIVATE WORDS FROM THE CALL';
  const server = http.createServer((request, response) => {
    const answers = {
      '/api/v1/health': { status: 'ok' },
      '/api/v1/projects': { items: [{ id: 'p1', name: 'Owner check' }], total: 1 },
      '/api/v1/projects/p1': { id: 'p1', name: 'Owner check', videoAsset: { durationSeconds: 121.4 } },
      '/api/v1/projects/p1/segments': [
        { speakerLabel: 'you', reviewedText: secretText },
        { speakerLabel: 'you', reviewedText: secretText },
        { speakerLabel: 'others', reviewedText: secretText },
        { speakerLabel: null, reviewedText: secretText },
      ],
    };
    response.setHeader('Content-Type', 'application/json');
    response.end(JSON.stringify(answers[request.url] ?? {}));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));

  const child = spawn('node', [path.join(SOURCE, 'owner-check.cjs')], {
    env: { ...process.env, RALPH_ROOT: env.root, NARRASTEP_WEB_PORT: String(server.address().port) },
  });
  let output = '';
  let answered = 0;
  child.stderr.on('data', (chunk) => { output += chunk; });
  child.stdout.on('data', (chunk) => {
    output += chunk;
    const prompts = (output.match(/Press Enter|\(y\/n\)/g) ?? []).length;
    while (answered < prompts) {
      child.stdin.write('y\n');
      answered += 1;
    }
  });
  const status = await new Promise((resolve) => child.on('close', resolve));
  await new Promise((resolve) => server.close(resolve));

  assert.equal(status, 0, output);
  assert.doesNotMatch(output, /PRIVATE WORDS/);
  const roadmap = fs.readFileSync(path.join(env.root, 'docs', 'ROADMAP.md'), 'utf8');
  assert.match(roadmap, /- \[x\] P05\.6 Manual test\.\n {2}Evidence \(\d{4}-\d{2}-\d{2}\): .*121 s, 4 segments \(2 You, 1 Others, 1 without a label\)/);
  assert.doesNotMatch(git(env.root, 'log', '-1', '-p'), /PRIVATE WORDS/);
  assert.equal(stateOf(env, 'P05.6').passes, true);
  assert.equal(stateOf(env, 'P07-CHECK').passes, true);
  assert.equal(git(remote, 'rev-parse', 'main'), git(env.root, 'rev-parse', 'HEAD'));
});

test('the owner session waits for the desktop tasks, and for the stack', async () => {
  const env = setup([task('P07.4b', 1), task('P05.6', 2, { owner: true, closes: ['P05.6'] })], '');
  const notReady = spawnSync('node', [path.join(SOURCE, 'owner-check.cjs')], { encoding: 'utf8', env: { ...process.env, RALPH_ROOT: env.root } });
  assert.equal(notReady.status, 2);
  assert.match(notReady.stdout, /P07\.4b must pass first/);

  const prd = readPrd(env);
  prd.userStories[0].passes = true;
  fs.writeFileSync(path.join(env.dir, 'prd.json'), JSON.stringify(prd));
  const noStack = spawnSync('node', [path.join(SOURCE, 'owner-check.cjs')], {
    encoding: 'utf8',
    env: { ...process.env, RALPH_ROOT: env.root, NARRASTEP_WEB_PORT: '1' },
  });
  assert.equal(noStack.status, 2);
  assert.match(noStack.stdout, /Start Narrastep first/);
  assert.match(fs.readFileSync(path.join(env.root, 'docs', 'ROADMAP.md'), 'utf8'), /- \[ \] P01\.1/, 'nothing was recorded');
});

test('the isolated stack refuses a Compose file that reaches the host', () => {
  const env = setup([task('T1', 1)], '');
  const check = (config) => spawnSync('node', [path.join(SOURCE, 'prd.cjs'), 'compose-check'], {
    encoding: 'utf8',
    input: JSON.stringify(config),
    env: { ...process.env, RALPH_ROOT: env.root },
  });
  assert.equal(check({ services: { api: { volumes: [{ type: 'volume', source: 'data', target: '/data' }] } } }).status, 0);
  const refused = check({ services: { api: { privileged: true, volumes: [{ type: 'bind', source: 'C:\\Users', target: '/host' }] } } });
  assert.equal(refused.status, 1);
  assert.match(refused.stderr, /privileged is refused/);
  assert.match(refused.stderr, /host folder/);
});

test('the iteration limit is honored and a later run goes on from the verified state', () => {
  const env = setup([task('T1', 1), task('T2', 2)], FINISH);
  const first = run(env, ['1']);
  assert.equal(first.status, 1, first.output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.equal(stateOf(env, 'T2').passes, false);
  assert.equal(run(env, ['--resume', '5']).status, 0);
  assert.equal(agentRuns(env), 2);
});

test('--resume continues a task whose roadmap item is already ticked in the working tree', () => {
  const env = setup([task('T1', 1, { verify: ['test -f proof.txt'] })], `
    ${FINISH}
    if [ "$1" -eq 2 ]; then touch proof.txt; fi
  `);
  const first = run(env, ['1']);
  assert.equal(first.status, 1, first.output);
  assert.match(fs.readFileSync(path.join(env.root, 'docs', 'ROADMAP.md'), 'utf8'), /- \[x\] P01\.1/);
  assert.equal(stateOf(env, 'T1').attempts, 1);
  const resumed = run(env, ['--resume']);
  assert.equal(resumed.status, 0, resumed.output);
  assert.equal(stateOf(env, 'T1').passes, true);
});

test('a requested stop ends the run before the next iteration', () => {
  const env = setup([task('T1', 1), task('T2', 2)], `${FINISH} touch scripts/ralph/.state/stop`);
  const { status, output } = run(env);
  assert.equal(status, 6, output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.equal(stateOf(env, 'T2').passes, false);
  assert.equal(agentRuns(env), 1);
});

test('stop.sh --now ends an active run, and --resume continues the same task', async () => {
  const env = setup([task('T1', 1)], `
    if [ "$1" -eq 1 ]; then echo partial > partial.txt; sleep 8; fi
    ${FINISH}
  `);
  const active = spawn('bash', [path.join(env.dir, 'ralph.sh')], { cwd: env.root, stdio: 'ignore', env: runnerEnvironment(env) });
  const ended = new Promise((resolve) => active.on('close', resolve));
  const partial = path.join(env.root, 'partial.txt');
  for (let waited = 0; waited < 40 && !fs.existsSync(partial); waited += 1) {
    await new Promise((resolve) => setTimeout(resolve, 250));
  }
  assert.ok(fs.existsSync(partial), 'the agent started');

  const started = Date.now();
  const stop = spawnSync('bash', [path.join(env.dir, 'stop.sh'), '--now'], { cwd: env.root, encoding: 'utf8' });
  assert.equal(stop.status, 0, stop.stderr);
  assert.match(stop.stdout, /--resume/);
  const code = await ended;
  assert.notEqual(code, 0, 'the run did not report completion');
  if (process.platform === 'win32') assert.ok(Date.now() - started < 6000, 'taskkill ended the process tree at once');

  assert.equal(stateOf(env, 'T1').passes, false);
  assert.equal(stateOf(env, 'T1').attempts ?? 0, 0, 'an interruption costs no attempt');
  const resumed = run(env, ['--resume']);
  assert.equal(resumed.status, 0, resumed.output);
  assert.equal(stateOf(env, 'T1').passes, true);
  assert.match(git(env.root, 'show', '--stat', '--format=', 'HEAD'), /partial\.txt/, 'the interrupted work was kept');
});

test('stop.sh without --now lets the iteration finish, and reports when nothing is running', () => {
  const env = setup([task('T1', 1)], FINISH);
  const idle = spawnSync('bash', [path.join(env.dir, 'stop.sh')], { cwd: env.root, encoding: 'utf8' });
  assert.equal(idle.status, 0);
  assert.match(idle.stdout, /No active run/);
  assert.equal(fs.existsSync(path.join(env.dir, '.state', 'stop')), false, 'no stale request is left behind');
  assert.equal(run(env).status, 0);
});

test('a session that outlives its time limit counts as a failed attempt', () => {
  const env = setup([task('T1', 1)], 'sleep 20');
  const { status, output } = run(env, ['1'], { RALPH_ITERATION_TIMEOUT_SECONDS: '1' });
  assert.equal(status, 1, output);
  assert.match(stateOf(env, 'T1').lastFailure, /did not finish in 1 s/);
});

test('the run refuses another branch, changes it does not own, and a nested session', () => {
  const env = setup([task('T1', 1)], FINISH);
  fs.writeFileSync(path.join(env.root, 'mine.txt'), 'the owner is working');
  const dirty = run(env);
  assert.equal(dirty.status, 2);
  assert.match(dirty.output, /working tree has changes/);
  const resumed = run(env, ['--resume']);
  assert.equal(resumed.status, 2);
  assert.match(resumed.output, /no interrupted task owns/);
  fs.rmSync(path.join(env.root, 'mine.txt'));

  const nested = run(env, [], { CLAUDECODE: '1' });
  assert.equal(nested.status, 2);
  assert.match(nested.output, /separate terminal/);

  git(env.root, 'switch', '--quiet', '-c', 'other');
  const branch = run(env);
  assert.equal(branch.status, 2);
  assert.match(branch.output, /works on 'main'/);
  assert.equal(fs.existsSync(path.join(env.fake, 'count')), false, 'no agent started');
});

test('the run refuses to start when its state folder is not ignored by Git', () => {
  const env = setup([task('T1', 1)], FINISH);
  fs.writeFileSync(path.join(env.root, '.gitignore'), '.fake/\n');
  git(env.root, 'add', '--all');
  git(env.root, 'commit', '--quiet', '-m', 'Forget the state folder');
  const { status, output } = run(env);
  assert.equal(status, 2);
  assert.match(output, /must be in \.gitignore/);
  assert.equal(fs.existsSync(path.join(env.fake, 'count')), false);
});

test('undoing a change to the runner keeps the logs and the report folder', () => {
  const env = setup([task('T1', 1)], `echo tampered >> scripts/ralph/check.sh; echo new > scripts/ralph/extra.txt; ${FINISH}`);
  assert.equal(run(env, ['1']).status, 1);
  assert.equal(fs.existsSync(path.join(env.dir, 'extra.txt')), false, 'a new file in the runner is removed');
  assert.ok(fs.readdirSync(path.join(env.dir, '.state', 'logs')).some((file) => file.endsWith('.jsonl')));
  assert.ok(fs.existsSync(path.join(env.dir, 'prd.json')));
  assert.equal(stateOf(env, 'T1').attempts, 1);
});

test('a local branch behind origin refuses to start, because its roadmap is stale', () => {
  const env = setup([task('T1', 1)], FINISH);
  const remote = fs.mkdtempSync(path.join(os.tmpdir(), 'ralph-remote-'));
  temporaryRoots.push(remote);
  git(remote, 'init', '--quiet', '--bare', '--initial-branch=main');
  git(env.root, 'remote', 'add', 'origin', remote);
  git(env.root, 'push', '--quiet', 'origin', 'main');
  git(env.root, 'commit', '--quiet', '--allow-empty', '-m', 'Remote work');
  git(env.root, 'push', '--quiet', 'origin', 'main');
  git(env.root, 'reset', '--quiet', '--hard', 'HEAD~1');
  const { status, output } = run(env);
  assert.equal(status, 2);
  assert.match(output, /1 commit\(s\) behind origin/);
});

test('unsafe content or an incomplete change fails the task before any check runs', async (t) => {
  const cases = [
    // The split keeps this source file itself free of anything shaped like a token.
    ['a secret', `${FINISH} echo "ghp_""0123456789abcdefghijklmnopqrstuvwxyz" > leak.txt`, /looks like a secret/],
    ['no status update', `${FINISH} git checkout docs/STATUS.md`, /docs\/STATUS\.md must be updated/],
    ['no tick', `${FINISH} git checkout docs/ROADMAP.md; echo x >> docs/ROADMAP.md`, /must be checked by this task/],
    ['an authorship reference', `${FINISH} sed -i 's/Add feature/Add assistant feature/' scripts/ralph/.state/result.json`, /remove the reference/],
    ['a committed .env', `${FINISH} echo A=1 > .env`, /\.env: must not be committed/],
    ['a skipped test', `${FINISH} echo "test.skip('later', () => {});" >> tests/existing.test.js`, /must not be skipped or narrowed/],
    ['a focused test in a new file', `${FINISH} echo "it.only('x', () => {});" > tests/new.test.js`, /must not be skipped or narrowed/],
    ['an expected failure', `${FINISH} mkdir -p pkg/tests; echo "@pytest.mark.xfail" > pkg/tests/test_a.py`, /must not be skipped or narrowed/],
    ['a deleted test', `${FINISH} rm tests/existing.test.js`, /a test file must not be deleted/],
    ['a new roadmap item', `${FINISH} echo "- [ ] P77.1 Something new" >> docs/ROADMAP.md`, /P77\.1 is new/],
  ];
  for (const [name, agent, expected] of cases) {
    await t.test(name, () => {
      const env = setup([task('T1', 1)], agent);
      assert.equal(run(env, ['1']).status, 1);
      assert.equal(stateOf(env, 'T1').passes, false);
      assert.match(stateOf(env, 'T1').lastFailure, expected);
      assert.equal(git(env.root, 'log', '--format=%s').split('\n').length, 1, 'nothing was committed');
    });
  }
});

test('a checked item that a later task closes is refused', () => {
  const env = setup(
    [task('T1', 1), task('T2', 2)],
    `${FINISH} sed -i "s/^- \\[ \\] P01.2 /- [x] P01.2 /" docs/ROADMAP.md`,
  );
  assert.equal(run(env, ['1']).status, 1);
  assert.match(stateOf(env, 'T1').lastFailure, /P01\.2 is checked, but a later task closes it/);
});

test('status reports passed, blocked, owner and open tasks without changing anything', () => {
  const env = setup(
    [task('T1', 1, { passes: true }), task('T2', 2, { blocked: true, blockedReason: 'needs a decision' }), task('O', 3, { owner: true }), task('T4', 4)],
    '',
  );
  const result = spawnSync('bash', [path.join(env.dir, 'status.sh')], { cwd: env.root, encoding: 'utf8' });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.stdout, /Tasks: 4 \| passed 1 \| open 1 \| blocked 1 \| waiting for the owner 1/);
  assert.match(result.stdout, /Next: T4/);
  assert.match(result.stdout, /Blocked T2: needs a decision/);
  assert.match(result.stdout, /Run: not active/);
  assert.ok(clean(env));
});

test('the owner records a passed check, and unblock frees a task, from the command line', () => {
  const env = setup([task('T1', 1, { blocked: true, attempts: 2, lastFailure: 'x' }), task('O', 2, { owner: true })], '');
  const prd = (...args) => spawnSync('node', [path.join(env.dir, 'prd.cjs'), ...args], {
    encoding: 'utf8',
    env: { ...process.env, RALPH_ROOT: env.root },
  });

  const early = prd('confirm', 'O');
  assert.equal(early.status, 1, 'an owner check needs its roadmap tick first');
  assert.match(early.stderr, /Check P01\.2/);
  assert.equal(stateOf(env, 'O').passes, false);
  fs.writeFileSync(path.join(env.root, 'docs', 'ROADMAP.md'), '- [ ] P01.1 Item\n- [x] P01.2 Item\n  Evidence (2026-10-30): the owner ran it.\n');
  assert.equal(prd('confirm', 'O').status, 0);
  assert.equal(stateOf(env, 'O').passes, true);
  assert.equal(prd('confirm', 'T1').status, 2, 'a task for an agent cannot be confirmed by hand');
  assert.equal(stateOf(env, 'T1').passes, false);

  assert.equal(prd('unblock', 'T1').status, 0);
  assert.equal(stateOf(env, 'T1').blocked, false);
  assert.equal(stateOf(env, 'T1').attempts, 0);
  assert.equal(prd('next').stdout.trim(), 'run T1');
});

test('a dry run starts nothing', () => {
  const env = setup([task('T1', 1)], FINISH);
  const { status, output } = run(env, ['--dry-run']);
  assert.equal(status, 0, output);
  assert.match(output, /Next: T1/);
  assert.equal(fs.existsSync(path.join(env.fake, 'count')), false);
});

test('a check repeats after a lack of resources, but never after a real failure', () => {
  const env = setup([task('T1', 1)], '');
  const counter = toShellPath(path.join(env.fake, 'tries'));
  const sandboxWith = (body) => {
    const file = path.join(env.fake, 'sandbox-script');
    fs.writeFileSync(file, `#!/usr/bin/env bash\necho x >> "${counter}"\n${body}\n`);
    fs.chmodSync(file, 0o755);
    fs.rmSync(path.join(env.fake, 'tries'), { force: true });
    return spawnSync('bash', [path.join(env.dir, 'check.sh'), 'docs'], {
      encoding: 'utf8',
      env: { ...process.env, RALPH_SANDBOX: file, RALPH_RESOURCE_WAIT_SECONDS: '0' },
    });
  };
  const tries = () => fs.readFileSync(path.join(env.fake, 'tries'), 'utf8').trim().split('\n').length;

  const recovered = sandboxWith(`[ "$(wc -l < "${counter}")" -ge 2 ] && exit 0; echo "Error: ENOMEM: not enough memory, read"; exit 1`);
  assert.equal(recovered.status, 0, recovered.stdout);
  assert.equal(tries(), 2);
  assert.match(recovered.stdout, /ran out of resources/);

  const lasting = sandboxWith('echo "page.goto: net::ERR_CONNECTION_REFUSED"; exit 1');
  assert.equal(lasting.status, 1);
  assert.equal(tries(), 3, 'it gives up after two repeats');

  const real = sandboxWith('echo "AssertionError: expected 1 to equal 2"; exit 3');
  assert.equal(real.status, 3, 'the real status is kept');
  assert.equal(tries(), 1, 'a failing test is never repeated');
  assert.doesNotMatch(real.stdout, /ran out of resources/);
});

test('every file of the runner is protected from the agent, but its report folder is not', () => {
  const { deny } = JSON.parse(fs.readFileSync(path.join(SOURCE, 'settings.json'), 'utf8')).permissions;
  const files = fs.readdirSync(SOURCE, { withFileTypes: true }).filter((entry) => entry.isFile()).map((entry) => entry.name);
  assert.ok(files.includes('ralph.sh'));
  for (const file of files) {
    assert.ok(deny.includes(`Edit(./scripts/ralph/${file})`), `${file} must be in the deny list of settings.json`);
  }
  // A folder-wide rule also matches .state/, where the agent must write its report: every task
  // would then fail. Seen with the real binary on 2026-10-30.
  assert.ok(!deny.some((rule) => /^Edit\(\.\/scripts\/ralph\/\*+\)$/.test(rule)));
  assert.ok(!deny.some((rule) => rule.includes('.state')));
});

// The shipped task list is not checked here: Ajimu Autopilot now owns scripts/ralph/prd.json and
// may add its own fields and tasks, which must not turn this job red.
test('the agent permission file is valid', () => {
  JSON.parse(fs.readFileSync(path.join(SOURCE, 'settings.json'), 'utf8'));
});
