// Unit tests of the session and web contents guards with in-memory Electron stand-ins.
'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const { installContentsGuards, installSessionGuards } = require('../src/guards.cjs');
const { FakeEvent, createFakeElectron } = require('./support/fake-electron.cjs');

const ORIGIN = 'http://127.0.0.1:4200';

/** Returns the fake session with the guards installed for `ORIGIN`. */
function guardedSession() {
  const { session } = createFakeElectron();
  installSessionGuards(session, ORIGIN);
  return session;
}

/** Calls the request filter for `url` and returns whether it cancelled the request. */
function cancels(session, url) {
  let response;
  session.handlers.request.listener({ url }, (value) => {
    response = value;
  });
  return response.cancel;
}

/** Calls the permission request handler and returns what it granted. */
function grants(session, permission, requestingUrl) {
  let granted;
  session.handlers.permissionRequest(
    {},
    permission,
    (value) => {
      granted = value;
    },
    { requestingUrl, isMainFrame: true },
  );
  return granted;
}

/** Calls the permission request handler with a `media` request for `mediaTypes` and returns what it granted. */
function grantsMedia(session, requestingUrl, mediaTypes) {
  let granted;
  session.handlers.permissionRequest(
    {},
    'media',
    (value) => {
      granted = value;
    },
    { requestingUrl, isMainFrame: true, mediaTypes },
  );
  return granted;
}

describe('installSessionGuards', () => {
  it('filters every URL and cancels requests outside the app origin', () => {
    const session = guardedSession();
    assert.deepEqual(session.handlers.request.filter, { urls: ['<all_urls>'] });
    assert.equal(cancels(session, 'http://127.0.0.1:4200/api/v1/projects'), false);
    assert.equal(cancels(session, 'data:image/png;base64,AAAA'), false);
    assert.equal(cancels(session, 'http://127.0.0.1:8002/api/v1/health'), true);
    assert.equal(cancels(session, 'http://localhost:4200/'), true);
    assert.equal(cancels(session, 'https://example.com/'), true);
  });

  it('grants full screen to the app origin and denies everything else', () => {
    const session = guardedSession();
    assert.equal(grants(session, 'fullscreen', 'http://127.0.0.1:4200/projects/1/review'), true);
    assert.equal(grants(session, 'fullscreen', 'http://127.0.0.1:4300/'), false);
    assert.equal(grants(session, 'display-capture', 'http://127.0.0.1:4200/record'), false);
    assert.equal(grants(session, 'openExternal', 'http://127.0.0.1:4200/'), false);
    assert.equal(grants(session, 'notifications', 'http://127.0.0.1:4200/'), false);
  });

  it('grants the microphone to the app origin only, never the camera', () => {
    const session = guardedSession();
    const record = 'http://127.0.0.1:4200/record';
    assert.equal(grantsMedia(session, record, ['audio']), true);
    // Negative controls: camera, mixed, empty, missing types and every other origin.
    assert.equal(grantsMedia(session, record, ['video']), false);
    assert.equal(grantsMedia(session, record, ['audio', 'video']), false);
    assert.equal(grantsMedia(session, record, []), false);
    assert.equal(grantsMedia(session, record, undefined), false);
    assert.equal(grantsMedia(session, 'http://127.0.0.1:4300/', ['audio']), false);
    assert.equal(grantsMedia(session, 'http://localhost:4200/', ['audio']), false);
    assert.equal(grantsMedia(session, 'https://example.com/', ['audio']), false);
  });

  it('answers permission checks with the same rule', () => {
    const session = guardedSession();
    const check = session.handlers.permissionCheck;
    assert.equal(check(null, 'fullscreen', ORIGIN, {}), true);
    assert.equal(check(null, 'media', ORIGIN, { mediaType: 'audio' }), true);
    assert.equal(check(null, 'media', ORIGIN, { mediaType: 'video' }), false);
    assert.equal(check(null, 'media', ORIGIN, { mediaType: 'unknown' }), false);
    assert.equal(check(null, 'media', ORIGIN, {}), false);
    assert.equal(check(null, 'media', 'http://localhost:4200', { mediaType: 'audio' }), false);
    assert.equal(check(null, 'notifications', ORIGIN, {}), false);
    assert.equal(
      session.handlers.permissionCheck(null, 'fullscreen', 'http://localhost:4200', {}),
      false,
    );
  });

  it('refuses devices and turns the spell checker off', () => {
    const session = guardedSession();
    assert.equal(session.handlers.devicePermission({ deviceType: 'usb', origin: ORIGIN }), false);
    assert.equal(session.spellCheckerEnabled, false);
  });
});

describe('downloads', () => {
  /** Returns a download item stand-in for `url` that records the dialog options and cancellation. */
  function downloadItem(url) {
    const item = {
      cancelled: false,
      dialogOptions: undefined,
      getURL: () => url,
      cancel() {
        item.cancelled = true;
      },
      setSaveDialogOptions(options) {
        item.dialogOptions = options;
      },
    };
    return item;
  }

  /** Fires `will-download` on a guarded session for `item` started by the page at `pageUrl`. */
  function download(item, pageUrl = `${ORIGIN}/projects/1/export`) {
    const session = guardedSession();
    session.listeners['will-download']({}, item, { getURL: () => pageUrl });
    return item;
  }

  it('keeps downloads from the app origin and leaves the native Save as dialog to open', () => {
    for (const url of [`${ORIGIN}/api/v1/exports/1/file`, `blob:${ORIGIN}/0b1c-uuid`]) {
      const item = download(downloadItem(url));
      assert.equal(item.cancelled, false, url);
      assert.deepEqual(item.dialogOptions, { properties: ['showOverwriteConfirmation'] }, url);
    }
  });

  it('cancels downloads from another origin or page without a dialog', () => {
    const foreign = [
      'http://127.0.0.1:4300/file.zip',
      'http://localhost:4200/file.zip',
      'https://example.com/file.zip',
      'blob:https://example.com/0b1c-uuid',
      'data:text/plain;base64,AAAA',
    ];
    for (const url of foreign) {
      const item = download(downloadItem(url));
      assert.equal(item.cancelled, true, url);
      assert.equal(item.dialogOptions, undefined, url);
    }
    const fromForeignPage = download(downloadItem(`${ORIGIN}/file.zip`), 'https://example.com/');
    assert.equal(fromForeignPage.cancelled, true);
  });

  it('cancels a download whose item cannot be read', () => {
    const item = downloadItem(undefined);
    item.getURL = () => {
      throw new Error('destroyed');
    };
    assert.equal(download(item).cancelled, true);
  });
});

describe('screen capture requests', () => {
  const SOURCE = { id: 'screen:0:0', name: 'Screen 1' };

  /** Returns a `pickSource` stand-in that resolves to `result` and counts its calls. */
  function picker(result) {
    const stub = async () => {
      stub.calls += 1;
      if (result instanceof Error) {
        throw result;
      }
      return result;
    };
    stub.calls = 0;
    return stub;
  }

  /** Sends `request` to the display media handler of a session guarded with `pickSource` on `platform`. */
  async function capture(request, pickSource, platform = 'linux') {
    const { session } = createFakeElectron();
    installSessionGuards(session, ORIGIN, pickSource, platform);
    return new Promise((resolve) => {
      session.handlers.displayMedia(request, resolve);
    });
  }

  const appRequest = Object.freeze({
    securityOrigin: ORIGIN,
    videoRequested: true,
    audioRequested: true,
    frame: { url: `${ORIGIN}/record` },
  });

  it('offers the picker to the app origin and grants the chosen source', async () => {
    const pickSource = picker(SOURCE);
    assert.deepEqual(await capture(appRequest, pickSource), { video: SOURCE });
    assert.equal(pickSource.calls, 1);
  });

  it('adds loopback system audio on Windows for a screen and for a single window', async () => {
    const windowSource = { id: 'window:42:0', name: 'Call' };
    assert.deepEqual(await capture(appRequest, picker(SOURCE), 'win32'), {
      video: SOURCE,
      audio: 'loopback',
    });
    assert.deepEqual(await capture(appRequest, picker(windowSource), 'win32'), {
      video: windowSource,
      audio: 'loopback',
    });
  });

  it('grants video only on macOS, Linux and an unknown platform', async () => {
    for (const platform of ['darwin', 'linux', 'freebsd', undefined]) {
      assert.deepEqual(await capture(appRequest, picker(SOURCE), platform), { video: SOURCE });
    }
  });

  it('adds no audio on Windows when the page did not ask for audio', async () => {
    const videoOnly = { ...appRequest, audioRequested: false };
    assert.deepEqual(await capture(videoOnly, picker(SOURCE), 'win32'), { video: SOURCE });
  });

  it('denies the request when the picker is cancelled or fails, on every platform', async () => {
    for (const platform of ['win32', 'darwin', 'linux']) {
      assert.equal(await capture(appRequest, picker(null), platform), null);
      assert.equal(await capture(appRequest, picker(new Error('boom')), platform), null);
    }
  });

  it('never opens the picker for a foreign origin, a foreign frame or a malformed request', async () => {
    const pickSource = picker(SOURCE);
    for (const request of [
      { ...appRequest, securityOrigin: 'http://127.0.0.1:4300' },
      { ...appRequest, securityOrigin: 'http://localhost:4200' },
      { ...appRequest, securityOrigin: 'https://example.com' },
      { ...appRequest, frame: { url: 'https://example.com/embed' } },
      { ...appRequest, securityOrigin: undefined },
      { ...appRequest, videoRequested: false },
      {},
      null,
    ]) {
      assert.equal(await capture(request, pickSource, 'win32'), null);
    }
    assert.equal(pickSource.calls, 0);
  });

  it('denies every request when no picker is installed', async () => {
    assert.equal(await capture(appRequest, undefined), null);
    assert.equal(await capture(appRequest, undefined, 'win32'), null);
  });
});

describe('installContentsGuards', () => {
  /** Returns fake web contents with the guards installed for `ORIGIN`. */
  function guardedContents() {
    const { FakeWebContents } = createFakeElectron();
    const contents = new FakeWebContents();
    installContentsGuards(contents, ORIGIN);
    return contents;
  }

  /** Emits `eventName` for `url` and returns whether a guard prevented it. */
  function prevented(contents, eventName, url) {
    const event = new FakeEvent({ url, isMainFrame: true });
    contents.emit(eventName, event);
    return event.defaultPrevented;
  }

  it('keeps every frame navigation inside the app origin', () => {
    const contents = guardedContents();
    assert.equal(
      prevented(contents, 'will-frame-navigate', 'http://127.0.0.1:4200/projects'),
      false,
    );
    assert.equal(prevented(contents, 'will-frame-navigate', 'https://example.com/'), true);
    assert.equal(prevented(contents, 'will-frame-navigate', 'http://127.0.0.1:8002/docs'), true);
    assert.equal(prevented(contents, 'will-frame-navigate', 'file:///etc/hosts'), true);
    assert.equal(prevented(contents, 'will-frame-navigate', 'data:text/html,x'), true);
  });

  it('stops redirects that leave the app origin', () => {
    const contents = guardedContents();
    assert.equal(prevented(contents, 'will-redirect', 'http://127.0.0.1:4200/'), false);
    assert.equal(prevented(contents, 'will-redirect', 'http://example.com/'), true);
  });

  it('never attaches a webview', () => {
    const contents = guardedContents();
    assert.equal(prevented(contents, 'will-attach-webview', 'http://127.0.0.1:4200/'), true);
  });

  it('denies every new window, including same-origin ones', () => {
    const contents = guardedContents();
    assert.deepEqual(contents.windowOpenHandler({ url: 'https://example.com/' }), {
      action: 'deny',
    });
    assert.deepEqual(contents.windowOpenHandler({ url: 'http://127.0.0.1:4200/' }), {
      action: 'deny',
    });
  });
});
