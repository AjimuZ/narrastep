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
    assert.equal(grants(session, 'media', 'http://127.0.0.1:4200/record'), false);
    assert.equal(grants(session, 'display-capture', 'http://127.0.0.1:4200/record'), false);
    assert.equal(grants(session, 'openExternal', 'http://127.0.0.1:4200/'), false);
    assert.equal(grants(session, 'notifications', 'http://127.0.0.1:4200/'), false);
  });

  it('answers permission checks with the same rule', () => {
    const session = guardedSession();
    assert.equal(session.handlers.permissionCheck(null, 'fullscreen', ORIGIN, {}), true);
    assert.equal(session.handlers.permissionCheck(null, 'media', ORIGIN, {}), false);
    assert.equal(
      session.handlers.permissionCheck(null, 'fullscreen', 'http://localhost:4200', {}),
      false,
    );
  });

  it('refuses devices and screen capture and turns the spell checker off', () => {
    const session = guardedSession();
    assert.equal(session.handlers.devicePermission({ deviceType: 'usb', origin: ORIGIN }), false);
    let streams;
    session.handlers.displayMedia({}, (value) => {
      streams = value;
    });
    assert.equal(streams, null);
    assert.equal(session.spellCheckerEnabled, false);
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
