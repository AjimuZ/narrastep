// Opt-in window tests in the pinned Electron: isolation, loopback-only loading, fallback page and single instance.
'use strict';

const assert = require('node:assert/strict');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { after, before, describe, it } = require('node:test');

const HARNESS = path.join(__dirname, 'support', 'electron-harness.cjs');
const RESULT_PREFIX = 'NARRASTEP_HARNESS_RESULT ';
const RUN_TIMEOUT_MS = 60000;

/**
 * Returns the Electron executable installed in `desktop/node_modules`, or null when only the npm
 * package is there (`npm ci --ignore-scripts` skips the binary download). It reads the package's
 * `path.txt` instead of calling `require('electron')`, which would download a missing binary.
 */
function electronExecutable() {
  const packageDirectory = path.join(__dirname, '..', 'node_modules', 'electron');
  try {
    const relative = fs.readFileSync(path.join(packageDirectory, 'path.txt'), 'utf8').trim();
    const executable = path.join(packageDirectory, 'dist', relative);
    return relative !== '' && fs.existsSync(executable) ? executable : null;
  } catch {
    return null;
  }
}

// The suite is opt-in, like the backend's real-model test: the Electron binary also needs the
// host's GUI libraries and, on Linux, a usable Chromium sandbox, which a plain `npm test` cannot
// assume. Once requested, a missing binary fails the suite instead of skipping it.
const requested = process.env.NARRASTEP_RUN_DESKTOP_WINDOW_TESTS === '1';
const executable = requested ? electronExecutable() : null;
const skip = requested
  ? false
  : 'Set NARRASTEP_RUN_DESKTOP_WINDOW_TESTS=1 to run the window tests in the real Electron.';

/**
 * Starts an HTTP server on 127.0.0.1 that answers with `handler` and records each request's path
 * and Host header. Pass `port` to bind a specific port (0 picks a free one). Resolves to
 * `{ server, port, requests }`.
 */
function startServer(handler, port = 0) {
  const requests = [];
  const server = http.createServer((request, response) => {
    requests.push({ path: request.url, host: request.headers.host });
    handler(request, response);
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () =>
      resolve({ server, port: server.address().port, requests }),
    );
  });
}

/** Answers like a minimal web app: two HTML pages, a JSON file and a redirect to `otherOrigin`. */
function standInApp(otherOrigin) {
  return (request, response) => {
    if (request.url === '/' || request.url === '/second') {
      response.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      response.end(
        '<!doctype html><html lang="en"><title>Stand-in</title><body><h1>Stand-in app</h1></body></html>',
      );
    } else if (request.url === '/data.json') {
      response.writeHead(200, { 'Content-Type': 'application/json' });
      response.end('{"ok":true}');
    } else if (request.url === '/redirect') {
      response.writeHead(302, { Location: `${otherOrigin}/redirected` });
      response.end();
    } else {
      response.writeHead(404);
      response.end();
    }
  };
}

/** Resolves to a TCP port on 127.0.0.1 that was free a moment ago. */
async function freePort() {
  const { server, port } = await startServer(() => {});
  await new Promise((resolve) => server.close(resolve));
  return port;
}

/**
 * Launches the harness in Electron with `extraArguments`, the app on `appPort`, the profile in
 * `userData` and `scenario`; returns `{ child, results, exited }`. `results` fills with each parsed
 * result line; `exited` resolves to `{ code, signal, stderr }` and the process is killed after
 * `RUN_TIMEOUT_MS`. On Linux the window uses Chromium's headless platform, so no display is
 * needed. `NARRASTEP_DESKTOP_TEST_NO_SANDBOX=1` adds `--no-sandbox` for containers whose kernel
 * blocks the namespaces Chromium's sandbox needs; the shell's own settings do not change.
 */
function launch({ scenario, appPort, userData, otherOrigin = '', extraArguments = [] }) {
  const platformArguments =
    process.platform === 'linux' ? ['--ozone-platform=headless', '--disable-gpu'] : [];
  const sandboxArguments =
    process.env.NARRASTEP_DESKTOP_TEST_NO_SANDBOX === '1' ? ['--no-sandbox'] : [];
  const environment = {
    ...process.env,
    NARRASTEP_WEB_PORT: String(appPort),
    NARRASTEP_HARNESS_SCENARIO: scenario,
    NARRASTEP_HARNESS_USER_DATA: userData,
    NARRASTEP_HARNESS_OTHER_ORIGIN: otherOrigin,
  };
  // This variable would make Electron run the harness as plain Node.js.
  delete environment.ELECTRON_RUN_AS_NODE;
  const child = spawn(
    executable,
    [...platformArguments, ...sandboxArguments, ...extraArguments, HARNESS],
    {
      env: environment,
      stdio: ['ignore', 'pipe', 'pipe'],
    },
  );
  const results = [];
  let stdoutBuffer = '';
  let stderr = '';
  child.stdout.on('data', (chunk) => {
    stdoutBuffer += chunk;
    const lines = stdoutBuffer.split('\n');
    stdoutBuffer = lines.pop();
    for (const line of lines) {
      if (line.startsWith(RESULT_PREFIX)) {
        results.push(JSON.parse(line.slice(RESULT_PREFIX.length)));
        child.emit('result');
      }
    }
  });
  child.stderr.on('data', (chunk) => {
    stderr += chunk;
  });
  const timer = setTimeout(() => child.kill('SIGKILL'), RUN_TIMEOUT_MS);
  const exited = new Promise((resolve) => {
    child.once('exit', (code, signal) => {
      clearTimeout(timer);
      resolve({ code, signal, stderr });
    });
  });
  return { child, results, exited };
}

/** Resolves once `run` has printed `count` results; rejects with Electron's output if it exits first. */
async function waitForResults(run, count) {
  while (run.results.length < count) {
    const outcome = await Promise.race([
      new Promise((resolve) => run.child.once('result', () => resolve(null))),
      run.exited,
    ]);
    if (outcome !== null && run.results.length < count) {
      throw new Error(
        `Electron exited (${outcome.code ?? outcome.signal}) early:\n${outcome.stderr.slice(-4000)}`,
      );
    }
  }
  return run.results;
}

describe('desktop window in Electron', { skip }, () => {
  let workDirectory;

  before(() => {
    assert.ok(
      executable,
      'Electron is not installed in desktop/node_modules; run `node node_modules/electron/install.js`.',
    );
    workDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'narrastep-desktop-test-'));
  });

  after(() => {
    if (workDirectory) {
      fs.rmSync(workDirectory, { recursive: true, force: true });
    }
  });

  it('isolates the page and keeps every load and request on the loopback app origin', async () => {
    const other = await startServer((_request, response) => response.end('reached'));
    const otherOrigin = `http://127.0.0.1:${other.port}`;
    const app = await startServer(standInApp(otherOrigin));
    try {
      const run = launch({
        scenario: 'isolation',
        appPort: app.port,
        userData: path.join(workDirectory, 'isolation'),
        otherOrigin,
      });
      const [result] = await waitForResults(run, 1);
      const exit = await run.exited;
      const appOrigin = `http://127.0.0.1:${app.port}`;

      assert.equal(result.error, undefined, result.error);
      assert.equal(result.loadedUrl, `${appOrigin}/`);
      assert.equal(result.visible, true);
      assert.deepEqual(result.preferences, {
        contextIsolation: true,
        sandbox: true,
        nodeIntegration: false,
        nodeIntegrationInWorker: false,
        nodeIntegrationInSubFrames: false,
        webviewTag: false,
        webSecurity: true,
        preload: null,
      });
      assert.equal(result.spellCheckerEnabled, false);
      assert.deepEqual(result.pageGlobals, {
        require: 'undefined',
        process: 'undefined',
        module: 'undefined',
        Buffer: 'undefined',
      });
      assert.equal(result.windowOpenOther, true, 'window.open to another origin returns null');
      assert.equal(result.windowOpenSame, true, 'window.open on the app origin returns null');
      assert.equal(result.fetchSameOrigin, 200);
      assert.equal(result.fetchOther, 'TypeError', 'a request to another loopback port fails');
      assert.equal(result.fetchLocalhost, 'TypeError', 'a request to localhost fails');
      assert.equal(result.microphone, 'granted');
      assert.equal(result.camera, 'NotAllowedError', 'the camera stays denied');
      assert.equal(result.screen, 'NotAllowedError');
      assert.equal(result.notifications, 'denied');
      assert.equal(result.fullscreen, 'granted');
      assert.equal(result.urlAfterOtherOrigin, `${appOrigin}/`);
      assert.equal(result.urlAfterRedirect, `${appOrigin}/`);
      assert.equal(result.urlAfterSameOrigin, `${appOrigin}/second`);
      assert.equal(result.windowCount, 1);
      assert.deepEqual(other.requests, [], 'no request reached the other origin');
      assert.ok(
        app.requests.every((request) => request.host === `127.0.0.1:${app.port}`),
        'every request to the app used the 127.0.0.1 origin',
      );
      assert.ok(
        app.requests.some((request) => request.path === '/redirect'),
        'the redirect was requested',
      );
      assert.ok(!app.requests.some((request) => request.path === '/popup'), 'no popup was loaded');
      assert.equal(exit.code, 0, 'closing the window quits the shell');
    } finally {
      app.server.close();
      other.server.close();
    }
  });

  it('shows the fallback page, keeps one instance and reloads the app on a second launch', async () => {
    const appPort = await freePort();
    const userData = path.join(workDirectory, 'single-instance');
    const primary = launch({
      scenario: 'fallback',
      appPort,
      userData,
      extraArguments: ['--lang=it-IT'],
    });
    const [unavailable] = await waitForResults(primary, 1);
    assert.equal(unavailable.error, undefined, unavailable.error);
    assert.equal(unavailable.phase, 'fallback');
    assert.equal(unavailable.visible, true);
    assert.deepEqual(unavailable.page, {
      lang: 'it',
      title: 'Narrastep non è raggiungibile',
      link: `http://127.0.0.1:${appPort}/`,
      linkText: 'Riprova',
      scripts: 0,
    });

    const app = await startServer(standInApp(''), appPort);
    try {
      const secondary = launch({ scenario: 'fallback', appPort, userData });
      const [secondaryResult] = await waitForResults(secondary, 1);
      const secondaryExit = await secondary.exited;
      assert.deepEqual(secondaryResult, { phase: 'secondary', primary: false });
      assert.equal(secondaryExit.code, 0, 'the second launch exits normally');

      const results = await waitForResults(primary, 2);
      assert.deepEqual(results[1], {
        phase: 'recovered',
        url: `http://127.0.0.1:${appPort}/`,
        visible: true,
        windowCount: 1,
      });
      assert.equal((await primary.exited).code, 0);
    } finally {
      app.server.close();
      primary.child.kill();
    }
  });
});
