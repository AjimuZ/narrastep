// Tests of start.sh with a fake docker and a fake launcher: modes, arguments and the background window watcher.
'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { afterEach, beforeEach, describe, it } = require('node:test');

const START_SCRIPT = path.resolve(__dirname, '..', '..', 'start.sh');

// start.sh is a POSIX shell script; on Windows it runs in Git Bash, which this suite does not drive.
const skip = process.platform === 'win32' ? 'start.sh is exercised on POSIX shells' : false;

let project;
let bin;
let record;
let marker;

beforeEach(() => {
  // Inside the repository rather than os.tmpdir(): the fake commands must be executable, and /tmp
  // is mounted noexec on some systems. The folder is git-ignored and removed after each test.
  project = fs.mkdtempSync(path.join(__dirname, '.start-test-'));
  bin = path.join(project, 'fakebin');
  record = path.join(project, 'docker-calls.txt');
  marker = path.join(project, 'launcher-ran.txt');
  fs.mkdirSync(path.join(project, 'desktop'), { recursive: true });
  fs.mkdirSync(bin);
  fs.copyFileSync(START_SCRIPT, path.join(project, 'start.sh'));
  // Most tests start from an existing .env so that they never reach the generator.
  fs.writeFileSync(path.join(project, '.env'), 'NARRASTEP_WEB_PORT=4200\n');
  fs.writeFileSync(
    path.join(project, 'desktop', 'launch.cjs'),
    [
      "const fs = require('node:fs');",
      `fs.writeFileSync(${JSON.stringify(marker)}, 'ran ' + process.pid + '\\n');`,
      'if (process.env.FAKE_LAUNCHER_SLEEP) { setTimeout(() => {}, 30000); }',
    ].join('\n'),
  );
  writeTool(
    'docker',
    [
      'if [ "$1" = compose ] && [ "$2" = version ]; then exit "${FAKE_COMPOSE_VERSION_EXIT:-0}"; fi',
      `echo "$@" >> '${record}'`,
      // Behave like a foreground Compose: stay alive until the launcher has had its chance.
      `if [ -n "$FAKE_WAIT_FOR_LAUNCHER" ]; then i=0; while [ ! -e '${marker}' ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done; fi`,
      'exit "${FAKE_COMPOSE_EXIT:-0}"',
    ].join('\n'),
  );
  writeTool('npm', 'exit 0');
});

afterEach(() => {
  // Stop a launcher left running by a test, then remove the project.
  try {
    const pid = Number(fs.readFileSync(marker, 'utf8').split(' ')[1]);
    process.kill(pid, 'SIGKILL');
  } catch {
    // Already gone or never started.
  }
  fs.rmSync(project, { recursive: true, force: true });
});

/** Writes an executable fake command `name` into the fake bin folder. */
function writeTool(name, body) {
  const file = path.join(bin, name);
  fs.writeFileSync(file, `#!/bin/sh\n${body}\n`, { mode: 0o755 });
}

/** Returns the absolute path of a system tool, found through the real PATH. */
function systemTool(name) {
  return childProcess.execFileSync('sh', ['-c', `command -v ${name}`], { encoding: 'utf8' }).trim();
}

/**
 * Runs the copied start.sh with `args`. The PATH holds only the fake bin folder, a link to the
 * running Node.js (unless `withNode` is false) and links to the few system tools the
 * script uses, so the host's own docker, node and npm never take part.
 */
function runStart(args, { env = {}, withNode = true } = {}) {
  for (const tool of ['cat', 'dirname', 'sleep', 'sh', 'mv', 'rm']) {
    const link = path.join(bin, tool);
    if (!fs.existsSync(link)) {
      fs.symlinkSync(systemTool(tool), link);
    }
  }
  if (withNode && !fs.existsSync(path.join(bin, 'node'))) {
    fs.symlinkSync(process.execPath, path.join(bin, 'node'));
  }
  return childProcess.spawnSync(systemTool('sh'), [path.join(project, 'start.sh'), ...args], {
    cwd: project,
    env: { PATH: bin, ...env },
    encoding: 'utf8',
    timeout: 20000,
  });
}

/** Returns the lines the fake docker recorded for its non-version calls. */
function dockerCalls() {
  return fs.existsSync(record) ? fs.readFileSync(record, 'utf8').trim().split('\n') : [];
}

/** Polls until `check()` is true or `timeoutMs` passes. */
async function eventually(check, timeoutMs = 8000) {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) {
      return false;
    }
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  return true;
}

/**
 * Installs a stub generator that appends its call to the shared record, then runs `body`.
 * The real generator is covered by generate-env.test.cjs; here only the hand-over matters.
 */
function writeGenerator(body) {
  fs.mkdirSync(path.join(project, 'scripts'), { recursive: true });
  fs.writeFileSync(
    path.join(project, 'scripts', 'generate-env.sh'),
    `#!/bin/sh\necho "generate $*" >> '${record}'\n${body}\n`,
    { mode: 0o755 },
  );
}

describe('start.sh .env generation', { skip }, () => {
  it('creates a missing .env with the docker engine before Compose starts', () => {
    fs.rmSync(path.join(project, '.env'));
    writeGenerator('echo "NARRASTEP_WEB_PORT=4207"');
    const result = runStart(['--headless']);
    assert.equal(result.status, 0);
    assert.deepEqual(dockerCalls(), ['generate --engine docker', 'compose up --build']);
    assert.equal(fs.readFileSync(path.join(project, '.env'), 'utf8'), 'NARRASTEP_WEB_PORT=4207\n');
    assert.deepEqual(
      fs.readdirSync(project).filter((name) => name.startsWith('.env.generating')),
      [],
    );
  });

  it('leaves an existing or empty .env byte-identical and never runs the generator', () => {
    writeGenerator('echo "NARRASTEP_WEB_PORT=9999"');
    for (const content of ['KEEP=me\r\nNO_NEWLINE', '']) {
      fs.writeFileSync(path.join(project, '.env'), content);
      fs.rmSync(record, { force: true });
      const result = runStart(['--headless']);
      assert.equal(result.status, 0);
      assert.equal(fs.readFileSync(path.join(project, '.env'), 'utf8'), content);
      assert.deepEqual(dockerCalls(), ['compose up --build']);
    }
  });

  it('stops with a readable message and no .env when generation fails', () => {
    fs.rmSync(path.join(project, '.env'));
    writeGenerator('echo "partial=1"; exit 1');
    const result = runStart(['--headless']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /could not generate \.env/);
    assert.match(result.stderr, /\.env\.example/);
    assert.equal(fs.existsSync(path.join(project, '.env')), false);
    assert.deepEqual(dockerCalls(), ['generate --engine docker']);
  });

  it('stops the same way when the generator script is missing', () => {
    fs.rmSync(path.join(project, '.env'));
    const result = runStart(['--headless']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /could not generate \.env/);
    assert.equal(fs.existsSync(path.join(project, '.env')), false);
    assert.deepEqual(dockerCalls(), []);
  });

  it('hands the generated web port to the desktop launcher', () => {
    fs.rmSync(path.join(project, '.env'));
    writeGenerator('echo "NARRASTEP_WEB_PORT=4207"');
    assert.equal(runStart(['--headless']).status, 0);
    const { resolveShellEnvironment } = require('../src/launcher.cjs');
    const environment = resolveShellEnvironment({ env: {}, projectDirectory: project });
    assert.equal(environment.NARRASTEP_WEB_PORT, '4207');
  });
});

describe('start.sh', { skip }, () => {
  it('runs directly as ./start.sh, as the README says, because Git keeps it executable', () => {
    const result = childProcess.spawnSync(START_SCRIPT, ['--help'], { encoding: 'utf8' });
    assert.equal(result.error, undefined, 'start.sh must have the executable mode in Git');
    assert.equal(result.status, 0);
    assert.match(result.stdout, /Usage: \.\/start\.sh/);
  });

  it('prints usage for -h and --help and starts nothing', () => {
    for (const flag of ['-h', '--help']) {
      const result = runStart([flag]);
      assert.equal(result.status, 0, flag);
      assert.match(result.stdout, /--headless/);
      assert.match(result.stdout, /Node\.js 22\.12/);
      assert.deepEqual(dockerCalls(), []);
    }
  });

  it('rejects unknown options and arguments with exit code 2', () => {
    for (const args of [['--bogus'], ['-x'], ['extra']]) {
      const result = runStart(args);
      assert.equal(result.status, 2, args.join(' '));
      assert.match(result.stderr, /Usage: \.\/start\.sh/);
    }
    assert.deepEqual(dockerCalls(), []);
    assert.equal(fs.existsSync(marker), false);
  });

  it('fails before anything starts when Docker Compose is missing', () => {
    const result = runStart([], { env: { FAKE_COMPOSE_VERSION_EXIT: '1' } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Docker Compose is required/);
    assert.deepEqual(dockerCalls(), []);
    assert.equal(fs.existsSync(marker), false);
  });

  it('--headless runs only Compose and never the launcher', () => {
    const result = runStart(['--headless']);
    assert.equal(result.status, 0);
    assert.deepEqual(dockerCalls(), ['compose up --build']);
    assert.equal(fs.existsSync(marker), false);
  });

  it('combines --headless with -r to reuse images without a window', () => {
    const result = runStart(['-r', '--headless']);
    assert.equal(result.status, 0);
    assert.deepEqual(dockerCalls(), ['compose up --no-build']);
    assert.equal(fs.existsSync(marker), false);
  });

  it('starts the launcher in the background while Compose stays in the foreground', async () => {
    const result = runStart([], { env: { FAKE_WAIT_FOR_LAUNCHER: '1' } });
    assert.equal(result.status, 0);
    assert.deepEqual(dockerCalls(), ['compose up --build']);
    assert.equal(await eventually(() => fs.existsSync(marker)), true);
  });

  it('passes -r to Compose and still opens the window', async () => {
    const result = runStart(['-r'], { env: { FAKE_WAIT_FOR_LAUNCHER: '1' } });
    assert.equal(result.status, 0);
    assert.deepEqual(dockerCalls(), ['compose up --no-build']);
    assert.equal(await eventually(() => fs.existsSync(marker)), true);
  });

  it('returns the exit code of Compose', () => {
    const result = runStart(['--headless'], { env: { FAKE_COMPOSE_EXIT: '3' } });
    assert.equal(result.status, 3);
  });

  it('keeps the stack running and explains when Node.js is missing', () => {
    const result = runStart([], { withNode: false });
    assert.equal(result.status, 0);
    assert.match(result.stderr, /Node\.js with npm was not found/);
    assert.match(result.stderr, /--headless/);
    assert.deepEqual(dockerCalls(), ['compose up --build']);
    assert.equal(fs.existsSync(marker), false);
  });

  it('keeps the stack running and explains when npm is missing', () => {
    fs.rmSync(path.join(bin, 'npm'));
    const result = runStart([]);
    assert.equal(result.status, 0);
    assert.match(result.stderr, /Node\.js with npm was not found/);
    assert.deepEqual(dockerCalls(), ['compose up --build']);
  });

  it('stops a launcher that is still waiting once Compose has exited', async () => {
    const result = runStart([], { env: { FAKE_LAUNCHER_SLEEP: '1', FAKE_WAIT_FOR_LAUNCHER: '1' } });
    assert.equal(result.status, 0);
    assert.equal(await eventually(() => fs.existsSync(marker)), true);
    const pid = Number(fs.readFileSync(marker, 'utf8').split(' ')[1]);
    const gone = await eventually(() => {
      try {
        process.kill(pid, 0);
        return false;
      } catch {
        return true;
      }
    }, 10000);
    assert.equal(gone, true, 'the watcher should stop the launcher after Compose exits');
  });
});
