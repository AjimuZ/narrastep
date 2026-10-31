// Tests of start-podman.sh with fake podman commands: modes, provider choice, the .env step and the window.
'use strict';

const assert = require('node:assert/strict');
const childProcess = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const { afterEach, beforeEach, describe, it } = require('node:test');

const ROOT = path.resolve(__dirname, '..', '..');
const START_SCRIPT = path.join(ROOT, 'start-podman.sh');
const START_COMMON = path.join(ROOT, 'scripts', 'start-common.sh');

// The script is POSIX shell; on Windows it runs in Git Bash, which this suite does not drive.
const skip = process.platform === 'win32' ? 'start-podman.sh is exercised on POSIX shells' : false;

let project;
let bin;
let record;
let marker;

beforeEach(() => {
  // Inside the repository rather than os.tmpdir(): /tmp is mounted noexec on some systems.
  project = fs.mkdtempSync(path.join(__dirname, '.podman-test-'));
  bin = path.join(project, 'fakebin');
  record = path.join(project, 'calls.txt');
  marker = path.join(project, 'launcher-ran.txt');
  fs.mkdirSync(path.join(project, 'desktop'), { recursive: true });
  fs.mkdirSync(path.join(project, 'scripts'));
  fs.mkdirSync(bin);
  fs.copyFileSync(START_SCRIPT, path.join(project, 'start-podman.sh'));
  fs.copyFileSync(START_COMMON, path.join(project, 'scripts', 'start-common.sh'));
  fs.writeFileSync(path.join(project, '.env'), 'NARRASTEP_WEB_PORT=4200\n');
  fs.writeFileSync(
    path.join(project, 'desktop', 'launch.cjs'),
    `require('node:fs').writeFileSync(${JSON.stringify(marker)}, 'ran ' + process.pid + '\\n');`,
  );
  // A fake podman: 'compose version' succeeds unless told otherwise, 'info' follows FAKE_INFO_EXIT.
  writeTool(
    'podman',
    [
      'if [ "$1" = compose ] && [ "$2" = version ]; then exit "${FAKE_PLUGIN_EXIT:-0}"; fi',
      'if [ "$1" = info ]; then exit "${FAKE_INFO_EXIT:-0}"; fi',
      `echo "podman $@" >> '${record}'`,
      'exit "${FAKE_COMPOSE_EXIT:-0}"',
    ].join('\n'),
  );
  writeTool('npm', 'exit 0');
});

afterEach(() => {
  try {
    process.kill(Number(fs.readFileSync(marker, 'utf8').split(' ')[1]), 'SIGKILL');
  } catch {
    // Already gone or never started.
  }
  fs.rmSync(project, { recursive: true, force: true });
});

/** Writes an executable fake command `name` into the fake bin folder. */
function writeTool(name, body) {
  fs.writeFileSync(path.join(bin, name), `#!/bin/sh\n${body}\n`, { mode: 0o755 });
}

/** Returns the absolute path of a system tool, found through the real PATH. */
function systemTool(name) {
  return childProcess.execFileSync('sh', ['-c', `command -v ${name}`], { encoding: 'utf8' }).trim();
}

/** Runs the copied script with `args` on a PATH that holds only the fakes and a few system tools. */
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
  return childProcess.spawnSync(
    systemTool('sh'),
    [path.join(project, 'start-podman.sh'), ...args],
    {
      cwd: project,
      env: { PATH: bin, ...env },
      encoding: 'utf8',
      timeout: 20000,
    },
  );
}

/** Returns the lines the fakes recorded. */
function calls() {
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

describe('start-podman.sh', { skip }, () => {
  it('prints usage for -h and --help and starts nothing', () => {
    for (const flag of ['-h', '--help']) {
      const result = runStart([flag]);
      assert.equal(result.status, 0, flag);
      assert.match(result.stdout, /Usage: \.\/start-podman\.sh/);
      assert.match(result.stdout, /--headless/);
      assert.deepEqual(calls(), []);
    }
  });

  it('rejects unknown options and arguments with exit code 2', () => {
    for (const args of [['--bogus'], ['-x'], ['extra']]) {
      const result = runStart(args);
      assert.equal(result.status, 2, args.join(' '));
      assert.match(result.stderr, /Usage: \.\/start-podman\.sh/);
    }
    assert.deepEqual(calls(), []);
  });

  it('--headless runs only podman compose with a build and no launcher', () => {
    const result = runStart(['--headless']);
    assert.equal(result.status, 0);
    assert.deepEqual(calls(), ['podman compose up --build']);
    assert.equal(fs.existsSync(marker), false);
  });

  it('-r reuses images, alone and with --headless', () => {
    assert.equal(runStart(['-r', '--headless']).status, 0);
    assert.deepEqual(calls(), ['podman compose up --no-build']);
  });

  it('opens the window while Compose stays in the foreground', async () => {
    writeTool(
      'podman',
      [
        'if [ "$1" = compose ] && [ "$2" = version ]; then exit 0; fi',
        `echo "podman $@" >> '${record}'`,
        `i=0; while [ ! -e '${marker}' ] && [ $i -lt 100 ]; do sleep 0.1; i=$((i+1)); done`,
      ].join('\n'),
    );
    const result = runStart([]);
    assert.equal(result.status, 0);
    assert.deepEqual(calls(), ['podman compose up --build']);
    assert.equal(await eventually(() => fs.existsSync(marker)), true);
  });

  it('falls back to podman-compose when podman compose has no provider', () => {
    writeTool('podman-compose', `echo "podman-compose $@" >> '${record}'`);
    const result = runStart(['--headless', '-r'], { env: { FAKE_PLUGIN_EXIT: '125' } });
    assert.equal(result.status, 0);
    assert.deepEqual(calls(), ['podman-compose up --no-build']);
  });

  it('prefers podman compose when both providers exist', () => {
    writeTool('podman-compose', `echo "podman-compose $@" >> '${record}'`);
    assert.equal(runStart(['--headless']).status, 0);
    assert.deepEqual(calls(), ['podman compose up --build']);
  });

  it('explains a missing Compose provider and starts nothing', () => {
    const result = runStart(['--headless'], { env: { FAKE_PLUGIN_EXIT: '125' } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Compose provider/);
    assert.match(result.stderr, /podman-compose/);
    assert.deepEqual(calls(), []);
  });

  it('explains a missing Podman', () => {
    fs.rmSync(path.join(bin, 'podman'));
    const result = runStart(['--headless']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /Podman is required/);
  });

  it('asks for podman machine start on macOS and Windows when Podman does not answer', () => {
    writeTool('uname', 'echo Darwin');
    const result = runStart(['--headless'], { env: { FAKE_INFO_EXIT: '125' } });
    assert.equal(result.status, 1);
    assert.match(result.stderr, /podman machine start/);
    assert.deepEqual(calls(), []);
  });

  it('creates a missing .env with the podman engine before Compose starts', () => {
    fs.rmSync(path.join(project, '.env'));
    fs.writeFileSync(
      path.join(project, 'scripts', 'generate-env.sh'),
      `#!/bin/sh\necho "generate $*" >> '${record}'\necho "NARRASTEP_WEB_PORT=4207"\n`,
      { mode: 0o755 },
    );
    assert.equal(runStart(['--headless']).status, 0);
    assert.deepEqual(calls(), ['generate --engine podman', 'podman compose up --build']);
    assert.equal(fs.readFileSync(path.join(project, '.env'), 'utf8'), 'NARRASTEP_WEB_PORT=4207\n');
  });

  it('stops with a message and no .env when generation fails', () => {
    fs.rmSync(path.join(project, '.env'));
    const result = runStart(['--headless']);
    assert.equal(result.status, 1);
    assert.match(result.stderr, /could not generate \.env/);
    assert.deepEqual(calls(), []);
  });

  it('returns the exit code of Compose', () => {
    assert.equal(runStart(['--headless'], { env: { FAKE_COMPOSE_EXIT: '3' } }).status, 3);
  });

  it('keeps the stack running and explains when Node.js is missing', () => {
    const result = runStart([], { withNode: false });
    assert.equal(result.status, 0);
    assert.match(result.stderr, /Node\.js with npm was not found/);
    assert.match(result.stderr, /\.\/start-podman\.sh --headless/);
    assert.deepEqual(calls(), ['podman compose up --build']);
    assert.equal(fs.existsSync(marker), false);
  });
});

describe('start-podman.sh source', () => {
  it('never uses sudo or --privileged', () => {
    const lines = fs
      .readFileSync(START_SCRIPT, 'utf8')
      .split('\n')
      .filter((line) => !line.trim().startsWith('#') && !/never|without/.test(line));
    assert.doesNotMatch(lines.join('\n'), /\bsudo\b|--privileged/);
  });

  it('uses LF line endings', () => {
    assert.equal(fs.readFileSync(START_SCRIPT, 'utf8').includes('\r'), false);
  });

  it('is executable in Git once tracked', () => {
    let listing = '';
    try {
      listing = childProcess.execFileSync('git', ['ls-files', '-s', 'start-podman.sh'], {
        cwd: ROOT,
        encoding: 'utf8',
      });
    } catch {
      return; // No Git here (for example a source archive): nothing to compare.
    }
    if (listing.trim() !== '') {
      assert.match(listing, /^100755 /);
    }
  });
});
