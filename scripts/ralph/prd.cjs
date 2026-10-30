'use strict';
// Task state of the runner: reads and changes prd.json and progress.txt, picks the next task and
// decides whether a finished iteration may be committed. Node.js only, so the host needs no jq.

const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');

const MAX_ATTEMPTS = 2;
const KEPT_PROGRESS_ENTRIES = 12;
const BRIEF_PROGRESS_ENTRIES = 3;
const MAX_FAILURE_CHARS = 4000;
const AREAS = new Set(['docs', 'backend', 'web', 'e2e', 'desktop', 'ralph', 'compose', 'stack']);
// Every task runs these, which take about four minutes and use no agent session. A change can
// break another part, and with the push there is no later review to catch it.
const BASE_AREAS = ['docs', 'backend', 'web', 'e2e', 'desktop', 'ralph', 'compose'];
const STACK_EVERY_TASKS = 5;
const EMPTY_TREE = '4b825dc642cb6eb9a060e54bf8d69288fbee4904';
const MAX_JOB_OUTPUT_CHARS = 6000;
const CI_CACHE_MILLISECONDS = 5 * 60 * 1000;
const TEST_FILE = /(^|\/)tests?\/|(^|\/)e2e\/|\.(spec|test)\.[cm]?[jt]sx?$|(^|\/)test_[^/]*\.py$/;
// `pytest.mark.skipif` stays allowed: the opt-in tests depend on it.
const WEAKENED_TEST = /\b(?:it|test|describe)(?:\.\w+)*\.(?:only|skip|fixme)\s*\(|\bx(?:it|describe)\s*\(|pytest\.mark\.(?:skip|xfail)\b|@unittest\.skip|\bpytest\.xfail\s*\(/;

// Same words as the local commit-message check, which is absent from a fresh clone.
const FORBIDDEN_COMMIT_WORDS =
  /\b(?:ai|a\.i\.|llms?|assistants?|chatbots?|gpt[\w.-]*|copilot|chatgpt|openai|anthropic|claude|codex|gemini)\b|artificial[ -]intelligence|co-authored-by|generated[ -]by|generated with|written by an?\b/i;
const FORBIDDEN_PATHS = [
  /^\.env$/,
  /^\.env\.(?!example$)/,
  /^\.claude\//,
  /^skills-lock\.json$/,
  /^data\/(?!\.gitkeep$)/,
  /(^|\/)node_modules\//,
  /\.(pem|key|p12|pfx)$/i,
  /(^|\/)id_(rsa|ed25519)$/,
];
const SECRET_PATTERNS = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/,
  /\bsk-ant-[A-Za-z0-9_-]{16,}/,
  /\bgh[pousr]_[A-Za-z0-9]{30,}/,
  /\bgithub_pat_[A-Za-z0-9_]{30,}/,
  /\bAKIA[0-9A-Z]{16}\b/,
  /\bxox[abprs]-[A-Za-z0-9-]{16,}/,
  /\bhf_[A-Za-z0-9]{30,}\b/,
];
const MAX_SCANNED_BYTES = 2 * 1024 * 1024;

/** Returns the paths of the runner's files below `root`. Pure; touches nothing. */
function runnerPaths(root) {
  const dir = path.join(root, 'scripts', 'ralph');
  return {
    dir,
    prd: path.join(dir, 'prd.json'),
    progress: path.join(dir, 'progress.txt'),
    history: path.join(dir, 'archive', 'progress-history.txt'),
    result: path.join(dir, '.state', 'result.json'),
    roadmap: path.join(root, 'docs', 'ROADMAP.md'),
  };
}

/** Returns the parsed task list. Throws when the file is missing or is not JSON. */
function loadPrd(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}

/** Writes through a temporary file, so an interruption cannot leave a half-written task list. */
function savePrd(file, prd) {
  const temporary = `${file}.tmp`;
  fs.writeFileSync(temporary, `${JSON.stringify(prd, null, 2)}\n`);
  fs.renameSync(temporary, file);
}

/** Returns the task with this ID. Throws for an unknown ID, which is a caller's mistake. */
function findStory(prd, id) {
  const story = prd.userStories.find((candidate) => candidate.id === id);
  if (!story) throw new Error(`unknown task '${id}'`);
  return story;
}

/** Returns readable problems of the task list's shape, identifiers, areas and dependency order. */
function validatePrd(prd) {
  const errors = [];
  if (typeof prd.branchName !== 'string' || !prd.branchName) errors.push('branchName is missing');
  if (!Array.isArray(prd.userStories) || prd.userStories.length === 0) {
    return [...errors, 'userStories is empty'];
  }
  const byId = new Map();
  const priorities = new Set();
  for (const story of prd.userStories) {
    if (typeof story.id !== 'string' || !story.id) {
      errors.push('a task has no id');
      continue;
    }
    if (byId.has(story.id)) errors.push(`${story.id}: duplicate id`);
    byId.set(story.id, story);
    if (!story.title) errors.push(`${story.id}: no title`);
    if (!story.description) errors.push(`${story.id}: no description`);
    if (!Array.isArray(story.acceptanceCriteria) || story.acceptanceCriteria.length === 0) {
      errors.push(`${story.id}: no acceptance criteria`);
    }
    if (!Number.isInteger(story.priority)) errors.push(`${story.id}: priority is not an integer`);
    if (priorities.has(story.priority)) errors.push(`${story.id}: priority ${story.priority} is used twice`);
    priorities.add(story.priority);
    if (typeof story.passes !== 'boolean') errors.push(`${story.id}: passes is not a boolean`);
    if (!Array.isArray(story.dependsOn)) errors.push(`${story.id}: dependsOn is not a list`);
    if (!Array.isArray(story.areas)) errors.push(`${story.id}: areas is not a list`);
    for (const area of story.areas ?? []) {
      if (!AREAS.has(area)) errors.push(`${story.id}: unknown area '${area}'`);
    }
    if (story.job && (typeof story.job.command !== 'string' || !Number.isInteger(story.job.timeoutSeconds))) {
      errors.push(`${story.id}: a job needs a command and timeoutSeconds`);
    }
  }
  for (const story of prd.userStories) {
    for (const dependency of story.dependsOn ?? []) {
      const required = byId.get(dependency);
      if (!required) errors.push(`${story.id}: unknown dependency '${dependency}'`);
      else if (required.priority >= story.priority) {
        // Priorities strictly follow dependencies, so this also rules out every cycle.
        errors.push(`${story.id}: depends on '${dependency}', which does not come earlier`);
      }
    }
  }
  return errors;
}

/** Returns the roadmap's items as a map from ID to whether the item is checked. */
function roadmapItems(roadmapText) {
  const items = new Map();
  for (const match of roadmapText.matchAll(/^- \[( |x)\] (P\d+\.\d+)\b/gm)) {
    items.set(match[2], match[1] === 'x');
  }
  return items;
}

/** Returns problems between the task list and the roadmap: open items no task covers, and so on. */
function validateRoadmapCoverage(prd, roadmapText) {
  const errors = [];
  const items = roadmapItems(roadmapText);
  const covered = new Set();
  for (const story of prd.userStories) {
    for (const item of [...(story.roadmapItems ?? []), ...(story.closes ?? [])]) {
      covered.add(item);
      if (!items.has(item)) errors.push(`${story.id}: roadmap item ${item} does not exist`);
    }
    for (const item of story.closes ?? []) {
      if (items.get(item) === true && !story.passes) {
        errors.push(`${story.id}: closes ${item}, which is already checked`);
      }
    }
  }
  for (const [item, checked] of items) {
    if (!checked && !covered.has(item)) errors.push(`roadmap item ${item} is open and no task covers it`);
    if (!checked && !prd.userStories.some((story) => (story.closes ?? []).includes(item))) {
      errors.push(`roadmap item ${item} is open and no task closes it`);
    }
  }
  return errors;
}

/**
 * Chooses what the runner does next, from verified state only.
 * Returns {state: 'run', id}, {state: 'complete'} when every task an agent can do has passed, or
 * {state: 'stuck'} when the remaining ones are blocked or wait for a blocked or owner task.
 */
function nextStep(prd) {
  const passed = new Set(prd.userStories.filter((story) => story.passes).map((story) => story.id));
  const open = prd.userStories
    .filter((story) => !story.passes && !story.owner)
    .sort((a, b) => a.priority - b.priority);
  if (open.length === 0) return { state: 'complete' };
  const runnable = open.find(
    (story) => !story.blocked && (story.dependsOn ?? []).every((dependency) => passed.has(dependency)),
  );
  return runnable ? { state: 'run', id: runnable.id } : { state: 'stuck' };
}

/** Counts one failed attempt, and blocks the task at the limit. Returns the stored state. */
function recordFailure(prd, id, reason) {
  const story = findStory(prd, id);
  story.attempts = (story.attempts ?? 0) + 1;
  story.lastFailure = String(reason).slice(-MAX_FAILURE_CHARS);
  if (story.attempts >= MAX_ATTEMPTS) {
    story.blocked = true;
    story.blockedReason = `Failed ${story.attempts} attempts. Last failure: ${story.lastFailure}`;
  }
  return story;
}

/** Blocks a task at once, for a cause no further attempt can solve. Returns the stored state. */
function recordBlocked(prd, id, reason) {
  const story = findStory(prd, id);
  story.blocked = true;
  story.blockedReason = String(reason).slice(-MAX_FAILURE_CHARS);
  return story;
}

/** Marks a task as passed. Only the runner calls this, after every verification succeeded. */
function recordPassed(prd, id, when) {
  const story = findStory(prd, id);
  story.passes = true;
  story.verifiedAt = when;
  story.blocked = false;
  story.blockedReason = '';
  story.lastFailure = '';
  return story;
}

/** Makes a blocked task runnable again with fresh attempts, after the owner removed the cause. */
function unblock(prd, id) {
  const story = findStory(prd, id);
  story.blocked = false;
  story.blockedReason = '';
  story.attempts = 0;
  story.lastFailure = '';
  return story;
}

/** Returns the text between the Codebase Patterns heading and the next separator. */
function codebasePatterns(progressText) {
  const match = progressText.match(/^## Codebase Patterns\n([\s\S]*?)^---$/m);
  return match ? match[1].trim() : '';
}

/** Returns the dated log entries of progress.txt, oldest first, without the separators. */
function progressEntries(progressText) {
  return progressText
    .split(/^---$/m)
    .map((part) => part.trim())
    .filter((part) => /^## \[/.test(part));
}

/**
 * Adds one log entry and any new general learnings, and moves old entries to the history file, so
 * progress.txt stays short enough for every fresh session. `learnings` already present are skipped.
 */
function appendProgress(paths, entry, learnings = []) {
  let text = fs.readFileSync(paths.progress, 'utf8');
  const known = codebasePatterns(text);
  const fresh = learnings.map((line) => String(line).trim()).filter((line) => line && !known.includes(line));
  if (fresh.length > 0) {
    const bullets = fresh.map((line) => `- ${line}`).join('\n');
    text = text.replace(/^(## Codebase Patterns\n[\s\S]*?)(^---$)/m, (_, section, separator) => {
      return `${section.trimEnd()}\n${bullets}\n${separator}`;
    });
  }
  text = `${text.trimEnd()}\n\n${entry.trim()}\n---\n`;

  const entries = progressEntries(text);
  if (entries.length > KEPT_PROGRESS_ENTRIES) {
    const moved = entries.slice(0, entries.length - KEPT_PROGRESS_ENTRIES);
    fs.mkdirSync(path.dirname(paths.history), { recursive: true });
    fs.appendFileSync(paths.history, moved.map((part) => `${part}\n---\n`).join(''));
    for (const part of moved) text = text.replace(`${part}\n---\n`, '');
    text = text.replace(/\n{3,}/g, '\n\n');
  }
  fs.writeFileSync(paths.progress, text);
}

/**
 * Returns the areas a task must pass: the base areas, its own, and the real stack at every fifth
 * passed task. `everyTasks` 0 turns that last rule off.
 */
function verificationAreas(prd, id, base = BASE_AREAS, everyTasks = STACK_EVERY_TASKS) {
  const passed = prd.userStories.filter((story) => story.passes).length;
  const areas = [...base, ...(findStory(prd, id).areas ?? [])];
  if (everyTasks > 0 && (passed + 1) % everyTasks === 0) areas.push('stack');
  return [...new Set(areas)];
}

/** Returns the settings-aware areas of a task; the tests narrow them through the environment. */
function areasFromEnvironment(prd, id) {
  const base = process.env.RALPH_BASE_AREAS?.split(' ').filter(Boolean) ?? BASE_AREAS;
  const every = process.env.RALPH_STACK_EVERY === undefined ? STACK_EVERY_TASKS : Number(process.env.RALPH_STACK_EVERY);
  return verificationAreas(prd, id, base, every);
}

/** Returns the stored output of a task's job, or null when it has none or the job has not run. */
function jobReport(paths, id) {
  const state = path.dirname(paths.result);
  try {
    const status = fs.readFileSync(path.join(state, `job-${id}.status`), 'utf8').trim();
    const output = fs.readFileSync(path.join(state, `job-${id}.log`), 'utf8').slice(-MAX_JOB_OUTPUT_CHARS);
    return { status, output };
  } catch {
    return null;
  }
}

/** Returns the context one iteration needs for one task, and nothing about the other sessions. */
function brief(prd, id, progressText, job = null) {
  const story = findStory(prd, id);
  const state = (dependency) => `${dependency} (${findStory(prd, dependency).passes ? 'passed' : 'open'})`;
  const lines = [
    `# Your task: ${story.id} - ${story.title}`,
    '',
    story.description,
    '',
    `Roadmap items: ${(story.roadmapItems ?? []).join(', ') || 'none'}.`,
    `Tick in docs/ROADMAP.md, with dated evidence: ${(story.closes ?? []).join(', ') || 'none; record progress under the item, leave it unchecked'}.`,
    `Dependencies: ${(story.dependsOn ?? []).map(state).join(', ') || 'none'}.`,
    `Areas checked by the runner: ${areasFromEnvironment(prd, id).join(', ')}.`,
    `User-visible, so CHANGELOG.md must change: ${story.userVisible ? 'yes' : 'no'}.`,
    '',
    '## Acceptance criteria',
    ...story.acceptanceCriteria.map((criterion) => `- ${criterion}`),
  ];
  const verifications = [...(story.verify ?? []), ...(story.verifyHost ?? [])];
  if (verifications.length > 0) {
    lines.push('', '## Extra commands the runner will run', ...verifications.map((command) => `- \`${command}\``));
  }
  if (story.notes) lines.push('', '## Technical notes', story.notes);
  if (job) {
    lines.push(
      '',
      `## Output of this task's job (exit status ${job.status})`,
      'The runner already ran it, so do not run it again. Record what it measured; when it failed, find out why and fix it.',
      '```text',
      job.output,
      '```',
    );
  }
  if (story.attempts > 0) {
    lines.push(
      '',
      `## Attempt ${story.attempts + 1} of ${MAX_ATTEMPTS}`,
      'The previous attempt failed and its work is still in the working tree (see `git status` and `git diff`).',
      'Continue from it, and fix this failure first:',
      '```text',
      story.lastFailure,
      '```',
    );
  }
  lines.push('', '## Codebase patterns from earlier sessions', codebasePatterns(progressText) || '(none yet)');
  const recent = progressEntries(progressText).slice(-BRIEF_PROGRESS_ENTRIES);
  if (recent.length > 0) lines.push('', '## Most recent progress', ...recent);
  return `${lines.join('\n')}\n`;
}

/** Returns the agent's report. Throws with a readable message when it is absent or malformed. */
function readResult(file) {
  let result;
  try {
    result = JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch (error) {
    throw new Error(`no readable result file at scripts/ralph/.state/result.json (${error.code ?? error.message})`);
  }
  if (!['done', 'failed', 'blocked'].includes(result.outcome)) {
    throw new Error(`result outcome must be done, failed or blocked, not '${result.outcome}'`);
  }
  if (typeof result.summary !== 'string' || !result.summary.trim()) throw new Error('result has no summary');
  return result;
}

/** Returns problems of a commit message under the project's commit rules. */
function commitMessageProblems(message) {
  const problems = [];
  const lines = String(message ?? '').split(/\r?\n/);
  const subject = lines[0].trim();
  if (!subject) return ['the commit subject is empty'];
  if (subject.length > 72) problems.push(`the commit subject has ${subject.length} characters; the limit is 72`);
  if (subject.endsWith('.')) problems.push('the commit subject ends with a period');
  if (lines.length > 1 && lines[1].trim()) problems.push('the commit subject and body need a blank line between them');
  if (/^(?:added|adds|adding|fixed|fixes|fixing|updated|updates|updating|changed|changes|removed|removes|implemented|implements)\b/i.test(subject)) {
    problems.push('the commit subject must be imperative, for example "Add" or "Fix"');
  }
  lines.forEach((line, index) => {
    const match = line.match(FORBIDDEN_COMMIT_WORDS);
    if (match) problems.push(`commit message line ${index + 1}: remove the reference "${match[0]}"`);
  });
  return problems;
}

/** Returns the final commit message: the agent's text with the task trailer that traces it. */
function finalCommitMessage(result, id) {
  const text = String(result.commitMessage ?? '').trim();
  const trailer = `Task: ${id}`;
  return text.includes(trailer) ? `${text}\n` : `${text}\n\n${trailer}\n`;
}

/** Runs git in `root` and returns its output. Throws when git fails. */
function git(root, ...args) {
  return execFileSync('git', args, { cwd: root, encoding: 'utf8', maxBuffer: 256 * 1024 * 1024 });
}

/** Returns the changed and untracked paths of the working tree, without Git-ignored ones. */
function changedPaths(root) {
  const fields = git(root, 'status', '--porcelain', '-uall', '-z').split('\0').filter(Boolean);
  const paths = [];
  for (let index = 0; index < fields.length; index += 1) {
    paths.push(fields[index].slice(3));
    // A rename or copy is followed by its old path, as a field without a status.
    if (/^[RC]|^.[RC]/.test(fields[index])) index += 1;
  }
  return paths;
}

/** Returns the patterns found in a changed file's text. A file it cannot read counts as clean. */
function secretsIn(file) {
  try {
    const stat = fs.statSync(file);
    if (!stat.isFile() || stat.size > MAX_SCANNED_BYTES) return [];
    const text = fs.readFileSync(file, 'utf8');
    return SECRET_PATTERNS.filter((pattern) => pattern.test(text)).map(String);
  } catch {
    return [];
  }
}

/** Tells whether a checked roadmap item carries dated evidence: in its block, or `Pxx.y evidence`. */
function hasRoadmapEvidence(roadmapText, item) {
  const escaped = item.replace('.', '\\.');
  if (new RegExp(`^${escaped}(?:[–-]P\\d+\\.\\d+)? evidence \\(\\d{4}-\\d{2}-\\d{2}\\)`, 'm').test(roadmapText)) {
    return true;
  }
  const block = roadmapText.match(new RegExp(`^- \\[x\\] ${escaped}\\b[\\s\\S]*?(?=^- \\[|^## |^Exit:|$(?![\\s\\S]))`, 'm'));
  return Boolean(block && /Evidence \(\d{4}-\d{2}-\d{2}\)/.test(block[0]));
}

/**
 * Returns the ways a change weakens the tests: test files deleted, or `.only`, `.skip`, `fixme` or
 * `xfail` added. A task whose purpose is to move tests sets `allowTestChanges`.
 */
function weakenedTests(root, changed) {
  const problems = [];
  const deleted = new Set(git(root, 'ls-files', '--deleted').split('\n').filter(Boolean));
  for (const file of deleted) {
    if (TEST_FILE.test(file)) problems.push(`${file}: a test file must not be deleted`);
  }
  for (const file of changed.filter((candidate) => TEST_FILE.test(candidate) && !deleted.has(candidate))) {
    let added;
    if (git(root, 'ls-files', '--', file).trim()) {
      const diff = git(root, 'diff', '--unified=0', 'HEAD', '--', file);
      added = diff.split('\n').filter((line) => line.startsWith('+') && !line.startsWith('+++'));
    } else {
      // Untracked, so every line is new. A folder or unreadable path has nothing to check.
      try {
        added = fs.readFileSync(path.join(root, file), 'utf8').split('\n');
      } catch {
        added = [];
      }
    }
    const match = added.find((line) => WEAKENED_TEST.test(line));
    if (match) problems.push(`${file}: a test must not be skipped or narrowed: ${match.trim().slice(0, 100)}`);
  }
  return problems;
}

/**
 * Checks everything about a finished task that needs no test run: scope of the changed files,
 * forbidden paths, secrets, weakened tests, required documents, roadmap ticks and the commit message.
 * Returns the problems found; an empty list means the checks may run.
 */
function preflight(root, prd, id, result) {
  const paths = runnerPaths(root);
  const story = findStory(prd, id);
  const problems = commitMessageProblems(result.commitMessage);
  const runnerFiles = new Set(['scripts/ralph/prd.json', 'scripts/ralph/progress.txt', 'scripts/ralph/archive/progress-history.txt']);
  const changed = changedPaths(root).filter((file) => !runnerFiles.has(file));

  if (changed.length === 0) problems.push('nothing changed in the working tree');
  for (const file of changed) {
    if (FORBIDDEN_PATHS.some((pattern) => pattern.test(file))) problems.push(`${file}: must not be committed`);
    if (file.startsWith('scripts/ralph/')) problems.push(`${file}: the runner's own files are not part of a task`);
    for (const pattern of secretsIn(path.join(root, file))) problems.push(`${file}: looks like a secret (${pattern})`);
  }
  if (!story.allowTestChanges) problems.push(...weakenedTests(root, changed));
  for (const document of ['docs/ROADMAP.md', 'docs/STATUS.md']) {
    if (!changed.includes(document)) problems.push(`${document} must be updated in the same change`);
  }
  if (story.userVisible && !changed.includes('CHANGELOG.md')) problems.push('CHANGELOG.md must be updated for this user-visible task');

  const roadmap = fs.readFileSync(paths.roadmap, 'utf8');
  const items = roadmapItems(roadmap);
  for (const item of story.closes ?? []) {
    if (items.get(item) !== true) problems.push(`docs/ROADMAP.md: ${item} must be checked by this task`);
    else if (!hasRoadmapEvidence(roadmap, item)) problems.push(`docs/ROADMAP.md: ${item} needs dated evidence (Evidence (YYYY-MM-DD): ...)`);
  }
  const closedElsewhere = prd.userStories.filter((other) => other.id !== id && !other.passes).flatMap((other) => other.closes ?? []);
  for (const item of closedElsewhere) {
    if (items.get(item) === true) problems.push(`docs/ROADMAP.md: ${item} is checked, but a later task closes it`);
  }
  // A new open item with no task would stop the next start of the run.
  const known = new Set(prd.userStories.flatMap((other) => [...(other.roadmapItems ?? []), ...(other.closes ?? [])]));
  for (const [item, checked] of items) {
    if (!checked && !known.has(item)) problems.push(`docs/ROADMAP.md: ${item} is new; report it in the summary instead of adding a roadmap item`);
  }
  try {
    git(root, 'diff', '--check', 'HEAD');
  } catch (error) {
    problems.push(`git diff --check found whitespace errors:\n${error.stdout}`);
  }
  return problems;
}

/**
 * Checks the commits about to be published, because the repository is public: every commit in the
 * range, not only its end result, since history keeps what a later commit removed.
 * Returns the problems found. A missing `baseRef` means nothing is published yet: all of HEAD counts.
 */
function prepushProblems(root, baseRef) {
  let range = 'HEAD';
  try {
    git(root, 'rev-parse', '--verify', '--quiet', `${baseRef}^{commit}`);
    range = `${baseRef}..HEAD`;
  } catch {
    range = 'HEAD';
  }
  const problems = [];
  const paths = git(root, 'log', '--format=', '--name-only', '--diff-filter=d', range).split('\n').filter(Boolean);
  for (const file of new Set(paths)) {
    if (FORBIDDEN_PATHS.some((pattern) => pattern.test(file))) problems.push(`${file}: must not be published`);
  }
  const patch = git(root, 'log', '--format=', '--no-color', '-p', range);
  for (const line of patch.split('\n')) {
    if (!line.startsWith('+') || line.startsWith('+++')) continue;
    for (const pattern of SECRET_PATTERNS) {
      if (pattern.test(line)) problems.push(`an added line looks like a secret (${pattern})`);
    }
  }
  const messages = git(root, 'log', '--format=%B', range);
  const forbidden = messages.match(FORBIDDEN_COMMIT_WORDS);
  if (forbidden) problems.push(`a commit message has the reference "${forbidden[0]}"`);
  return [...new Set(problems)];
}

/**
 * Returns what a rendered Compose configuration must not do, because the agent can edit that file:
 * privileged containers, host namespaces, added capabilities, the Docker socket, and host folders
 * other than the read-only model import.
 */
function composeProblems(config) {
  const problems = [];
  for (const [name, service] of Object.entries(config.services ?? {})) {
    if (service.privileged) problems.push(`${name}: privileged is refused`);
    for (const key of ['pid', 'ipc', 'uts', 'userns_mode', 'network_mode']) {
      if (service[key] === 'host') problems.push(`${name}: ${key} host is refused`);
    }
    if ((service.cap_add ?? []).length > 0) problems.push(`${name}: cap_add is refused`);
    if ((service.devices ?? []).length > 0) problems.push(`${name}: devices are refused`);
    for (const volume of service.volumes ?? []) {
      if (volume.type !== 'bind') continue;
      const source = String(volume.source).replace(/\\/g, '/');
      if (source.includes('docker.sock')) problems.push(`${name}: the Docker socket is refused`);
      else if (!source.endsWith('/data/models-import')) problems.push(`${name}: the host folder ${source} is refused`);
      else if (volume.read_only !== true) problems.push(`${name}: the model import folder must be read-only`);
    }
  }
  return problems;
}

/** Returns "owner/name" of a GitHub remote URL, or null for any other remote. */
function githubRepository(url) {
  const match = String(url).trim().match(/github\.com[:/]([^/\s]+\/[^/\s]+?)(?:\.git)?$/);
  return match ? match[1] : null;
}

/**
 * Prints the CI result of the pushed commit from GitHub's public API, without a login. Reused from
 * a short-lived cache, as the anonymous limit is 60 requests an hour. Any failure prints "unknown";
 * this is information only and decides nothing.
 */
async function printCiStatus(root, paths, branch) {
  const cacheFile = path.join(path.dirname(paths.result), 'ci-status.json');
  let label = 'unknown';
  let sha = '';
  try {
    sha = git(root, 'rev-parse', `origin/${branch}`).trim();
    const repository = githubRepository(git(root, 'remote', 'get-url', 'origin'));
    let cached = null;
    try {
      cached = JSON.parse(fs.readFileSync(cacheFile, 'utf8'));
    } catch {
      cached = null;
    }
    if (cached?.sha === sha && Date.now() - cached.at < CI_CACHE_MILLISECONDS) {
      label = cached.label;
    } else if (repository) {
      const response = await fetch(`https://api.github.com/repos/${repository}/actions/runs?head_sha=${sha}&per_page=1`, {
        headers: { 'User-Agent': 'narrastep-ralph', Accept: 'application/vnd.github+json' },
        signal: AbortSignal.timeout(5000),
      });
      if (response.ok) {
        const run = (await response.json()).workflow_runs?.[0];
        label = run ? (run.conclusion ?? run.status) : 'no run yet';
        fs.mkdirSync(path.dirname(cacheFile), { recursive: true });
        fs.writeFileSync(cacheFile, JSON.stringify({ sha, label, at: Date.now() }));
      }
    }
  } catch {
    label = 'unknown';
  }
  process.stdout.write(`CI on ${sha.slice(0, 7) || 'origin'}: ${label}\n`);
}

/** Prints stream-json output readably, and returns the final result event when one arrived. */
function renderStreamLine(line, write) {
  let event;
  try {
    event = JSON.parse(line);
  } catch {
    if (line.trim()) write(`${line}\n`);
    return null;
  }
  if (event.type === 'assistant') {
    for (const block of event.message?.content ?? []) {
      if (block.type === 'text' && block.text.trim()) write(`${block.text.trim()}\n`);
      if (block.type === 'tool_use') {
        const detail = block.input?.command ?? block.input?.file_path ?? block.input?.pattern ?? block.input?.skill ?? '';
        write(`  [${block.name}] ${String(detail).split('\n')[0].slice(0, 160)}\n`);
      }
    }
  }
  return event.type === 'result' ? event : null;
}

/** Filters the agent's stream from stdin to stdout and stores the final result event in `file`. */
function runStreamFilter(file) {
  let buffer = '';
  let last = null;
  const take = (line) => {
    const event = renderStreamLine(line, (text) => process.stdout.write(text));
    if (event) last = event;
  };
  process.stdin.setEncoding('utf8');
  process.stdin.on('data', (chunk) => {
    buffer += chunk;
    const lines = buffer.split('\n');
    buffer = lines.pop();
    lines.forEach(take);
  });
  process.stdin.on('end', () => {
    if (buffer) take(buffer);
    fs.writeFileSync(file, JSON.stringify(last ?? { missing: true }));
  });
}

/**
 * Classifies a finished agent process from its stored result event and exit status.
 * Returns 'limit' for a usage or rate limit (retry later, no attempt counted), 'error' when the
 * session itself failed, or 'ok' when it ended normally.
 */
function classifyRun(event, exitStatus, outputTail = '') {
  const hasEvent = Boolean(event) && event.missing !== true;
  // A session that reported its end is read from that report: the raw output can quote any text.
  const text = hasEvent ? `${event.result ?? ''} ${event.subtype ?? ''}` : outputTail;
  const failed = exitStatus !== 0 || event?.is_error === true || !hasEvent;
  if (!failed) return 'ok';
  // A killed session says nothing about limits, whatever its last output quoted.
  if (exitStatus === 124 || exitStatus === 137) return 'error';
  if (/usage limit|limit reached|hit your .{0,40}limit|rate.?limit|overloaded|\b429\b|\b529\b/i.test(text)) return 'limit';
  return 'error';
}

/** Returns the counts and per-task lines that status.sh prints. */
function statusReport(prd, roadmapText = '') {
  const stories = [...prd.userStories].sort((a, b) => a.priority - b.priority);
  const passed = stories.filter((story) => story.passes);
  const blocked = stories.filter((story) => !story.passes && story.blocked);
  const owner = stories.filter((story) => !story.passes && story.owner);
  const open = stories.filter((story) => !story.passes && !story.blocked && !story.owner);
  const step = nextStep(prd);
  const lines = [
    `Tasks: ${stories.length} | passed ${passed.length} | open ${open.length} | blocked ${blocked.length} | waiting for the owner ${owner.length}`,
    `Next: ${step.state === 'run' ? step.id : step.state}`,
  ];
  if (roadmapText) {
    const items = [...roadmapItems(roadmapText).values()];
    lines.push(`Roadmap items checked: ${items.filter(Boolean).length} of ${items.length}`);
  }
  const mark = (story) => {
    if (story.passes) return 'PASS   ';
    if (story.blocked) return 'BLOCKED';
    if (story.owner) return 'OWNER  ';
    return story.attempts > 0 ? `RETRY${story.attempts} ` : 'open   ';
  };
  lines.push('');
  for (const story of stories) lines.push(`${mark(story)} ${story.id.padEnd(8)} ${story.title}`);
  for (const story of blocked) lines.push('', `Blocked ${story.id}: ${story.blockedReason}`);
  return lines.join('\n');
}

/** Runs one command of the CLI below `root`. Returns the process exit status. */
function main(argv, root) {
  const paths = runnerPaths(root);
  const [command, ...args] = argv;
  const prd = loadPrd(paths.prd);
  const save = () => savePrd(paths.prd, prd);
  const out = (text) => process.stdout.write(`${text}\n`);

  switch (command) {
    case 'validate': {
      // The runner reads the committed roadmap: an interrupted task may already have ticked its
      // item in the working tree, and that must not stop it from continuing.
      const roadmap = args[0] === '--committed' ? git(root, 'show', 'HEAD:docs/ROADMAP.md') : fs.readFileSync(paths.roadmap, 'utf8');
      const errors = [...validatePrd(prd), ...validateRoadmapCoverage(prd, roadmap)];
      errors.forEach((error) => process.stderr.write(`${error}\n`));
      if (errors.length === 0) out(`Task list is valid: ${prd.userStories.length} tasks.`);
      return errors.length === 0 ? 0 : 1;
    }
    case 'branch':
      out(prd.branchName);
      return 0;
    case 'next': {
      const step = nextStep(prd);
      out(step.state === 'run' ? `run ${step.id}` : step.state);
      return 0;
    }
    case 'runnable': {
      const story = findStory(prd, args[0]);
      return !story.passes && !story.blocked && !story.owner ? 0 : 1;
    }
    case 'brief':
      process.stdout.write(brief(prd, args[0], fs.readFileSync(paths.progress, 'utf8'), jobReport(paths, args[0])));
      return 0;
    case 'areas':
      out(areasFromEnvironment(prd, args[0]).join(' '));
      return 0;
    case 'job-command':
      out(findStory(prd, args[0]).job?.command ?? '');
      return 0;
    case 'job-timeout':
      out(String(findStory(prd, args[0]).job?.timeoutSeconds ?? 0));
      return 0;
    case 'prepush': {
      const problems = prepushProblems(root, args[0]);
      problems.forEach((problem) => out(`prepush: ${problem}`));
      return problems.length === 0 ? 0 : 1;
    }
    case 'compose-check': {
      const problems = composeProblems(JSON.parse(fs.readFileSync(0, 'utf8')));
      problems.forEach((problem) => process.stderr.write(`stack: ${problem}\n`));
      return problems.length === 0 ? 0 : 1;
    }
    case 'ci-status':
      printCiStatus(root, paths, prd.branchName);
      return 0;
    case 'verify-commands': {
      const story = findStory(prd, args[0]);
      for (const entry of story.verify ?? []) out(`sandbox\t${entry}`);
      for (const entry of story.verifyHost ?? []) out(`host\t${entry}`);
      return 0;
    }
    case 'result': {
      const result = readResult(paths.result);
      out(result.outcome);
      return 0;
    }
    case 'result-reason':
      out(readResult(paths.result).summary);
      return 0;
    case 'preflight': {
      const problems = preflight(root, prd, args[0], readResult(paths.result));
      problems.forEach((problem) => out(`preflight: ${problem}`));
      return problems.length === 0 ? 0 : 1;
    }
    case 'commit-message':
      fs.writeFileSync(args[1], finalCommitMessage(readResult(paths.result), args[0]));
      return 0;
    case 'fail': {
      const story = recordFailure(prd, args[0], args[1]);
      save();
      appendProgress(paths, `## [${new Date().toISOString()}] - ${story.id} - attempt ${story.attempts} failed\n${story.lastFailure.split('\n').slice(0, 6).join('\n')}`);
      out(story.blocked ? 'blocked' : 'retry');
      return 0;
    }
    case 'block': {
      const story = recordBlocked(prd, args[0], args[1]);
      save();
      appendProgress(paths, `## [${new Date().toISOString()}] - ${story.id} - blocked\n${story.blockedReason}`);
      return 0;
    }
    case 'pass': {
      const when = new Date().toISOString();
      const result = readResult(paths.result);
      const story = recordPassed(prd, args[0], when);
      save();
      const learnings = Array.isArray(result.learnings) ? result.learnings : [];
      const notRun = Array.isArray(result.notRun) && result.notRun.length > 0 ? `\nNot run: ${result.notRun.join('; ')}` : '';
      appendProgress(paths, `## [${when}] - ${story.id} - passed\n${result.summary.trim()}${notRun}`, learnings);
      return 0;
    }
    case 'unblock':
      unblock(prd, args[0]);
      save();
      out(`${args[0]} can run again. Its stashed work, if any, is in \`git stash list\`.`);
      return 0;
    case 'confirm': {
      // The owner's own check is the evidence, so the roadmap must already say it passed.
      const story = findStory(prd, args[0]);
      if (!story.owner) throw new Error(`${story.id} is not an owner task`);
      const items = roadmapItems(fs.readFileSync(paths.roadmap, 'utf8'));
      const unticked = (story.closes ?? []).filter((item) => items.get(item) !== true);
      if (unticked.length > 0) {
        process.stderr.write(`Check ${unticked.join(', ')} in docs/ROADMAP.md with dated evidence first.\n`);
        return 1;
      }
      recordPassed(prd, story.id, new Date().toISOString());
      save();
      out(`${story.id} recorded as passed. Commit docs/ROADMAP.md and scripts/ralph/prd.json.`);
      return 0;
    }
    case 'stream':
      runStreamFilter(args[0]);
      return 0;
    case 'classify': {
      let event = null;
      try {
        event = JSON.parse(fs.readFileSync(args[0], 'utf8'));
      } catch {
        event = null;
      }
      out(classifyRun(event, Number(args[1]), args[2] ?? ''));
      return 0;
    }
    case 'status':
      out(statusReport(prd, fs.existsSync(paths.roadmap) ? fs.readFileSync(paths.roadmap, 'utf8') : ''));
      return 0;
    default:
      process.stderr.write(`unknown command '${command}'\n`);
      return 2;
  }
}

if (require.main === module) {
  const root = process.env.RALPH_ROOT ?? path.resolve(__dirname, '..', '..');
  try {
    process.exitCode = main(process.argv.slice(2), root);
  } catch (error) {
    process.stderr.write(`prd: ${error.message}\n`);
    process.exitCode = 2;
  }
}

module.exports = {
  appendProgress,
  brief,
  classifyRun,
  codebasePatterns,
  commitMessageProblems,
  composeProblems,
  finalCommitMessage,
  githubRepository,
  hasRoadmapEvidence,
  nextStep,
  preflight,
  prepushProblems,
  progressEntries,
  recordBlocked,
  recordFailure,
  recordPassed,
  roadmapItems,
  runnerPaths,
  statusReport,
  unblock,
  validatePrd,
  validateRoadmapCoverage,
  verificationAreas,
};
