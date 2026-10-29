// Electron entry of the window tests: starts the real shell, runs one scenario inside Electron and prints JSON results.
'use strict';

const electron = require('electron');

const { startShell } = require('../../src/shell.cjs');

const { app, BrowserWindow } = electron;

/** Prefix of every result line on standard output; the test parses only these lines. */
const RESULT_PREFIX = 'NARRASTEP_HARNESS_RESULT ';

const scenarioName = process.env.NARRASTEP_HARNESS_SCENARIO;
const otherOrigin = process.env.NARRASTEP_HARNESS_OTHER_ORIGIN;

// A private profile per test run; it also scopes the single-instance lock to the test.
app.setPath('userData', process.env.NARRASTEP_HARNESS_USER_DATA);
// Fake capture devices let getUserMedia reach the permission handler instead of failing for lack of hardware.
app.commandLine.appendSwitch('use-fake-device-for-media-stream');

/** Prints one result object as a single line. */
function report(result) {
  process.stdout.write(`${RESULT_PREFIX}${JSON.stringify(result)}\n`);
}

/** Resolves after `milliseconds`. */
function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/** Resolves once `check()` returns true, polling every 50 ms; rejects after `timeoutMs`. */
async function waitUntil(check, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (!check()) {
    if (Date.now() > deadline) {
      throw new Error('Timed out waiting for the window.');
    }
    await delay(50);
  }
}

/** Resolves once `contents` has finished loading exactly `url`. */
function waitForPage(contents, url) {
  return waitUntil(() => !contents.isLoading() && contents.getURL() === url);
}

/** Checks isolation, loopback-only requests, navigation, popups and permissions in the app page. */
async function isolation(window, appOrigin) {
  const contents = window.webContents;
  const entryUrl = `${appOrigin}/`;
  await waitForPage(contents, entryUrl);
  const run = (code) => contents.executeJavaScript(code, true);
  const preferences = contents.getLastWebPreferences();
  const result = {
    loadedUrl: contents.getURL(),
    visible: window.isVisible(),
    preferences: Object.fromEntries(
      [
        'contextIsolation',
        'sandbox',
        'nodeIntegration',
        'nodeIntegrationInWorker',
        'nodeIntegrationInSubFrames',
        'webviewTag',
        'webSecurity',
        'preload',
      ].map((key) => [key, preferences[key] ?? null]),
    ),
    spellCheckerEnabled: contents.session.isSpellCheckerEnabled(),
  };
  result.pageGlobals = await run(
    '({ require: typeof require, process: typeof process, module: typeof module, Buffer: typeof Buffer })',
  );
  result.windowOpenOther = await run(
    `window.open(${JSON.stringify(`${otherOrigin}/popup`)}) === null`,
  );
  result.windowOpenSame = await run("window.open('/popup') === null");
  result.fetchSameOrigin = await run("fetch('/data.json').then((r) => r.status, (e) => e.name)");
  result.fetchOther = await run(
    `fetch(${JSON.stringify(`${otherOrigin}/fetch`)}, { mode: 'no-cors' }).then((r) => 'sent', (e) => e.name)`,
  );
  result.fetchLocalhost = await run(
    "fetch('http://localhost:' + location.port + '/data.json', { mode: 'no-cors' }).then((r) => 'sent', (e) => e.name)",
  );
  result.microphone = await run(
    "navigator.mediaDevices.getUserMedia({ audio: true }).then((s) => { s.getTracks().forEach((t) => t.stop()); return 'granted'; }, (e) => e.name)",
  );
  result.screen = await run(
    "navigator.mediaDevices.getDisplayMedia({ video: true }).then((s) => { s.getTracks().forEach((t) => t.stop()); return 'granted'; }, (e) => e.name)",
  );
  result.notifications = await run('Notification.requestPermission()');
  result.fullscreen = await run(
    "document.documentElement.requestFullscreen().then(() => document.exitFullscreen()).then(() => 'granted', (e) => e.name)",
  );
  await run(`(() => {
    const frame = document.createElement('iframe');
    frame.src = ${JSON.stringify(`${otherOrigin}/frame`)};
    document.body.append(frame);
    const view = document.createElement('webview');
    view.setAttribute('src', ${JSON.stringify(`${otherOrigin}/webview`)});
    document.body.append(view);
    return true;
  })()`);
  await run(`location.href = ${JSON.stringify(`${otherOrigin}/away`)}; true`);
  await delay(1000);
  result.urlAfterOtherOrigin = contents.getURL();
  await run("location.href = '/redirect'; true");
  await delay(1000);
  result.urlAfterRedirect = contents.getURL();
  await run("location.href = '/second'; true");
  await waitForPage(contents, `${appOrigin}/second`);
  result.urlAfterSameOrigin = contents.getURL();
  result.windowCount = BrowserWindow.getAllWindows().length;
  report(result);
  window.close();
}

/** Checks the fallback page, then waits for a second launch and checks that the app is loaded again. */
async function fallback(window, appOrigin) {
  const contents = window.webContents;
  const entryUrl = `${appOrigin}/`;
  await waitUntil(() => !contents.isLoading() && contents.getURL().startsWith('data:text/html'));
  const page = await contents.executeJavaScript(`({
    lang: document.documentElement.lang,
    title: document.querySelector('h1').textContent,
    link: document.querySelector('a').href,
    linkText: document.querySelector('a').textContent,
    scripts: document.scripts.length,
  })`);
  const secondLaunch = new Promise((resolve) => app.once('second-instance', resolve));
  report({ phase: 'fallback', page, visible: window.isVisible() });
  await secondLaunch;
  await waitForPage(contents, entryUrl);
  report({
    phase: 'recovered',
    url: contents.getURL(),
    visible: window.isVisible(),
    windowCount: BrowserWindow.getAllWindows().length,
  });
  window.close();
}

const SCENARIOS = { isolation, fallback };

const shell = startShell({ electron, env: process.env, platform: process.platform });
if (!shell.primary) {
  report({ phase: 'secondary', primary: false });
} else {
  shell.ready
    .then((window) => SCENARIOS[scenarioName](window, shell.appOrigin))
    .catch((error) => {
      report({ error: String(error?.stack ?? error) });
      app.exit(1);
    });
}
