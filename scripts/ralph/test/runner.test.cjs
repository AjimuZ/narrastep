'use strict';
// Runs the real ralph.sh, verify.sh and prd.cjs in a temporary repository with a stand-in for the
// agent, whose behavior each test scripts. Needs bash and git, and no Docker or Claude Code.

const assert = require('node:assert/strict');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
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
    env: {
      ...process.env,
      CLAUDECODE: '',
      RALPH_ALLOW_NESTED: '',
      RALPH_CLAUDE_BIN: path.join(env.fake, 'claude'),
      RALPH_SANDBOX: path.join(env.fake, 'sandbox'),
      RALPH_PAUSE_SECONDS: '0',
      RALPH_LIMIT_WAIT_SECONDS: '0',
      ...extraEnv,
    },
  });
  return { status: result.status, output: `${result.stdout}${result.stderr}` };
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
  assert.equal(agentRuns(env), 2);
  const state = stateOf(env, 'T1');
  assert.equal(state.attempts ?? 0, 0);
  assert.notEqual(state.blocked, true);
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
  const active = spawn('bash', [path.join(env.dir, 'ralph.sh')], {
    cwd: env.root,
    stdio: 'ignore',
    env: {
      ...process.env,
      CLAUDECODE: '',
      RALPH_ALLOW_NESTED: '',
      RALPH_CLAUDE_BIN: path.join(env.fake, 'claude'),
      RALPH_SANDBOX: path.join(env.fake, 'sandbox'),
    },
  });
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

test('the agent permission file is valid and the shipped task list fits the roadmap', () => {
  JSON.parse(fs.readFileSync(path.join(SOURCE, 'settings.json'), 'utf8'));
  const repository = path.resolve(SOURCE, '..', '..');
  const result = spawnSync('node', [path.join(SOURCE, 'prd.cjs'), 'validate'], {
    encoding: 'utf8',
    env: { ...process.env, RALPH_ROOT: repository },
  });
  assert.equal(result.status, 0, result.stderr);
});
