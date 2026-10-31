// Unit tests of the shell lifecycle and the main window with in-memory Electron stand-ins.
'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { MESSAGES } = require('../src/messages.cjs');
const { DesktopConfigurationError, WINDOW_WEB_PREFERENCES } = require('../src/policy.cjs');
const { startShell } = require('../src/shell.cjs');
const { choiceUrl } = require('../src/source-picker.cjs');
const { ZOOM_FACTORS, nextZoomFactor, windowOptions, zoomShortcut } = require('../src/window.cjs');
const { FakeEvent, createFakeElectron } = require('./support/fake-electron.cjs');

/** Starts the shell on fakes and waits until its window exists; returns the fakes and the result. */
async function startOnFakes({ env = {}, platform = 'win32', ...fakeOptions } = {}) {
  const fake = createFakeElectron(fakeOptions);
  const shell = startShell({ electron: fake.electron, env, platform });
  const window = await shell.ready;
  return { ...fake, shell, window };
}

/** Emits a main-frame `did-fail-load` like Electron for `url` with Chromium `errorCode`. */
function failLoad(contents, url, errorCode = -102, isMainFrame = true) {
  contents.emit('did-fail-load', new FakeEvent(), errorCode, 'ERR', url, isMainFrame);
}

/** Decodes the HTML of a `data:text/html` URL. */
function decodePage(url) {
  return decodeURIComponent(url.slice(url.indexOf(',') + 1));
}

describe('startShell', () => {
  it('quits a second instance without a window or listeners', async () => {
    const { electron, calls, windows, shell } = await startOnFakes({ lock: false });
    assert.equal(shell.primary, false);
    assert.equal(await shell.ready, null);
    assert.deepEqual(calls, ['app.enableSandbox', 'app.requestSingleInstanceLock', 'app.quit']);
    assert.equal(windows.length, 0);
    assert.equal(electron.app.listenerCount('second-instance'), 0);
    assert.equal(electron.app.listenerCount('web-contents-created'), 0);
  });

  it('rejects an invalid port before touching Electron', () => {
    const fake = createFakeElectron();
    assert.throws(
      () =>
        startShell({
          electron: fake.electron,
          env: { NARRASTEP_WEB_PORT: 'x' },
          platform: 'linux',
        }),
      DesktopConfigurationError,
    );
    assert.deepEqual(fake.calls, []);
  });

  it('enables the sandbox and installs every guard before the first load', async () => {
    const { calls } = await startOnFakes();
    const firstLoad = calls.indexOf('contents.loadURL');
    assert.equal(calls[0], 'app.enableSandbox');
    for (const guard of [
      'session.webRequest.onBeforeRequest',
      'session.setPermissionRequestHandler',
      'session.setPermissionCheckHandler',
      'session.setDevicePermissionHandler',
      'session.setDisplayMediaRequestHandler',
      'session.setSpellCheckerEnabled',
      'session.on:will-download',
      'contents.setWindowOpenHandler',
    ]) {
      const index = calls.indexOf(guard);
      assert.ok(index >= 0 && index < firstLoad, `${guard} before the first load`);
    }
  });

  /** Opens the picker for a capture request of the app, chooses the first source and returns the grant. */
  async function captureFirstSource(platform, locale = 'en-US') {
    const chosen = { id: 'screen:0:0', name: 'Screen 1', thumbnail: { isEmpty: () => true } };
    const { session, windows } = await startOnFakes({ platform, locale, sources: [chosen] });
    const answer = new Promise((resolve) => {
      session.handlers.displayMedia(
        { securityOrigin: 'http://127.0.0.1:4200', videoRequested: true, audioRequested: true },
        resolve,
      );
    });
    await new Promise(setImmediate);
    const picker = windows[1];
    assert.equal(picker.options.parent, windows[0]);
    assert.match(
      decodePage(picker.webContents.loads[0]),
      new RegExp(`<html lang="${locale.slice(0, 2)}">`),
    );
    picker.webContents.emit('will-frame-navigate', new FakeEvent({ url: choiceUrl(0) }));
    return { chosen, grant: await answer };
  }

  it('answers a screen capture request of the app with the picker in the system language', async () => {
    const { chosen, grant } = await captureFirstSource('linux', 'it-IT');
    assert.deepEqual(grant, { video: chosen });
  });

  it('adds Windows loopback audio to the picked source only when the shell runs on Windows', async () => {
    const windows = await captureFirstSource('win32');
    assert.deepEqual(windows.grant, { video: windows.chosen, audio: 'loopback' });
    const macos = await captureFirstSource('darwin');
    assert.deepEqual(macos.grant, { video: macos.chosen });
  });

  it('opens the configured loopback port with the isolated preferences', async () => {
    const { shell, window } = await startOnFakes({ env: { NARRASTEP_WEB_PORT: '4310' } });
    assert.equal(shell.primary, true);
    assert.equal(shell.appOrigin, 'http://127.0.0.1:4310');
    assert.deepEqual(window.webContents.loads, ['http://127.0.0.1:4310/']);
    assert.deepEqual(window.options.webPreferences, { ...WINDOW_WEB_PREFERENCES });
    assert.equal(window.options.show, false);
    assert.equal(window.options.minWidth, 320);
  });

  it('guards web contents created after the window', async () => {
    const { electron, FakeWebContents } = await startOnFakes();
    const later = new FakeWebContents();
    electron.app.emit('web-contents-created', new FakeEvent(), later);
    const event = new FakeEvent({ url: 'https://example.com/' });
    later.emit('will-frame-navigate', event);
    assert.equal(event.defaultPrevented, true);
    assert.deepEqual(later.windowOpenHandler({ url: 'https://example.com/' }), { action: 'deny' });
  });

  it('removes the menu on Windows and Linux and keeps it on macOS', async () => {
    assert.ok(
      (await startOnFakes({ platform: 'win32' })).calls.includes('Menu.setApplicationMenu:null'),
    );
    assert.ok(
      (await startOnFakes({ platform: 'linux' })).calls.includes('Menu.setApplicationMenu:null'),
    );
    assert.ok(
      !(await startOnFakes({ platform: 'darwin' })).calls.some((call) => call.startsWith('Menu.')),
    );
  });

  it('brings the running window to the front on a second launch', async () => {
    const { electron, window } = await startOnFakes();
    window.state.minimized = true;
    electron.app.emit('second-instance', new FakeEvent(), [], '');
    assert.deepEqual(window.state, {
      visible: true,
      minimized: false,
      focused: true,
      destroyed: false,
    });
    assert.equal(window.webContents.loads.length, 1, 'the loaded app is not reloaded');
  });

  it('retries the app on a second launch while the fallback page is shown', async () => {
    const { electron, window } = await startOnFakes();
    failLoad(window.webContents, 'http://127.0.0.1:4200/');
    electron.app.emit('second-instance', new FakeEvent(), [], '');
    assert.equal(window.webContents.loads.at(-1), 'http://127.0.0.1:4200/');
  });

  it('ignores a second launch after the window is closed', async () => {
    const { electron, window } = await startOnFakes();
    window.state.destroyed = true;
    electron.app.emit('second-instance', new FakeEvent(), [], '');
    assert.equal(window.state.visible, false);
  });

  it('quits when the last window closes', async () => {
    const { electron, calls } = await startOnFakes({ platform: 'darwin' });
    electron.app.emit('window-all-closed');
    assert.equal(calls.at(-1), 'app.quit');
  });
});

describe('main window', () => {
  it('shows the window once the first page is painted', async () => {
    const { window } = await startOnFakes();
    assert.equal(window.state.visible, false);
    window.emit('ready-to-show');
    assert.equal(window.state.visible, true);
  });

  it('shows the fallback page in the system language when the app is unreachable', async () => {
    const { window } = await startOnFakes({ locale: 'it-IT' });
    failLoad(window.webContents, 'http://127.0.0.1:4200/');
    const fallback = window.webContents.loads.at(-1);
    assert.ok(fallback.startsWith('data:text/html;charset=utf-8,'));
    const html = decodePage(fallback);
    assert.match(html, /<html lang="it">/);
    assert.ok(html.includes(MESSAGES.it.unavailableTitle));
    assert.ok(html.includes('<a href="http://127.0.0.1:4200/">Riprova</a>'));
    assert.ok(html.includes("default-src 'none'"));
    assert.ok(!html.includes('<script'));
    assert.equal(window.state.visible, true);
  });

  it('follows the language the app reported once it had loaded', async () => {
    const { window } = await startOnFakes({ locale: 'it-IT' });
    window.webContents.emit('did-finish-load');
    await new Promise(setImmediate);
    assert.deepEqual(window.webContents.scripts, ['document.documentElement.lang']);
    failLoad(window.webContents, 'http://127.0.0.1:4200/');
    const html = decodePage(window.webContents.loads.at(-1));
    assert.match(html, /<html lang="en">/);
    assert.ok(html.includes(MESSAGES.en.unavailableTitle));
    assert.ok(html.includes('>Try again</a>'));
  });

  it('does not run scripts in pages outside the app origin', async () => {
    const { window } = await startOnFakes();
    failLoad(window.webContents, 'http://127.0.0.1:4200/');
    window.webContents.emit('did-finish-load');
    assert.deepEqual(window.webContents.scripts, []);
  });

  it('ignores cancelled loads, subframes and other URLs', async () => {
    const { window } = await startOnFakes();
    failLoad(window.webContents, 'http://127.0.0.1:4200/', -3);
    failLoad(window.webContents, 'http://127.0.0.1:4200/frame', -102, false);
    failLoad(window.webContents, 'https://example.com/', -105);
    assert.deepEqual(window.webContents.loads, ['http://127.0.0.1:4200/']);
  });

  it('asks before leaving a page that blocks unloading and leaves only on confirmation', async () => {
    const { window, dialogs } = await startOnFakes({ dialogChoices: [0, 1] });
    const stay = new FakeEvent();
    window.webContents.emit('will-prevent-unload', stay);
    assert.equal(stay.defaultPrevented, false);
    const leave = new FakeEvent();
    window.webContents.emit('will-prevent-unload', leave);
    assert.equal(leave.defaultPrevented, true);
    const { window: parent, options } = dialogs[0];
    assert.equal(parent, window);
    assert.equal(options.message, MESSAGES.en.leaveTitle);
    assert.deepEqual(options.buttons, [MESSAGES.en.leaveStay, MESSAGES.en.leaveConfirm]);
    assert.equal(options.cancelId, 0);
    assert.equal(options.defaultId, 0);
  });

  it('asks in Italian when the app runs in Italian', async () => {
    const { window, dialogs } = await startOnFakes();
    window.webContents.pageLanguage = 'it';
    window.webContents.emit('did-finish-load');
    await new Promise(setImmediate);
    window.webContents.emit('will-prevent-unload', new FakeEvent());
    assert.deepEqual(dialogs[0].options.buttons, ['Resta', 'Esci']);
  });

  it('zooms with Ctrl and Cmd shortcuts only', async () => {
    const { window } = await startOnFakes();
    const press = (input) => {
      const event = new FakeEvent();
      window.webContents.emit('before-input-event', event, {
        type: 'keyDown',
        alt: false,
        ...input,
      });
      return event.defaultPrevented;
    };
    assert.equal(press({ control: true, key: '=' }), true);
    assert.equal(window.webContents.zoomFactor, 1.1);
    assert.equal(press({ meta: true, key: '+' }), true);
    assert.equal(window.webContents.zoomFactor, 1.25);
    assert.equal(press({ control: true, key: '0' }), true);
    assert.equal(window.webContents.zoomFactor, 1);
    assert.equal(press({ control: true, key: '-' }), true);
    assert.equal(window.webContents.zoomFactor, 0.9);
    assert.equal(press({ key: '=' }), false);
    assert.equal(press({ control: true, key: 's' }), false);
    assert.equal(window.webContents.zoomFactor, 0.9);
  });
});

describe('window size', () => {
  it('opens at 1280 × 800 on a large screen', async () => {
    const { window } = await startOnFakes();
    assert.equal(window.options.width, 1280);
    assert.equal(window.options.height, 800);
  });

  it('fits a smaller work area without going below 320 × 480', async () => {
    const { window } = await startOnFakes({ workArea: { width: 1366, height: 728 } });
    assert.deepEqual([window.options.width, window.options.height], [1280, 728]);
    const tiny = windowOptions({ width: 200, height: 300 });
    assert.deepEqual(
      [tiny.width, tiny.height, tiny.minWidth, tiny.minHeight],
      [320, 480, 320, 480],
    );
    const unknown = windowOptions(undefined);
    assert.deepEqual([unknown.width, unknown.height], [1280, 800]);
  });
});

describe('zoom helpers', () => {
  it('maps only key presses with Ctrl or Cmd and without Alt', () => {
    assert.equal(zoomShortcut({ type: 'keyDown', control: true, key: '_' }), 'out');
    assert.equal(zoomShortcut({ type: 'keyUp', control: true, key: '=' }), null);
    assert.equal(zoomShortcut({ type: 'keyDown', control: true, alt: true, key: '=' }), null);
    assert.equal(zoomShortcut({ type: 'keyDown', shift: true, key: '+' }), null);
  });

  it('steps through the presets and stops at 25% and 500%', () => {
    assert.equal(nextZoomFactor(1, 'in'), 1.1);
    assert.equal(nextZoomFactor(2, 'in'), 2.5);
    assert.equal(nextZoomFactor(3, 'in'), 4);
    assert.equal(nextZoomFactor(5, 'in'), 5);
    assert.equal(nextZoomFactor(0.25, 'out'), 0.25);
    assert.equal(nextZoomFactor(1.2, 'out'), 1.1);
    assert.equal(nextZoomFactor(1.2, 'in'), 1.25);
    assert.equal(nextZoomFactor(3.7, 'reset'), 1);
    assert.deepEqual([ZOOM_FACTORS[0], ZOOM_FACTORS.at(-1)], [0.25, 5]);
  });
});
