// Unit tests of the loopback origin, URL, request and permission rules of the desktop shell.
'use strict';

const assert = require('node:assert/strict');
const { describe, it } = require('node:test');

const {
  ALLOWED_PERMISSIONS,
  DesktopConfigurationError,
  WINDOW_WEB_PREFERENCES,
  appEntryUrl,
  isAllowedRequestUrl,
  isAppUrl,
  isPermissionAllowed,
  pickLanguage,
  resolveAppOrigin,
} = require('../src/policy.cjs');

const ORIGIN = 'http://127.0.0.1:4200';

describe('resolveAppOrigin', () => {
  it('uses 127.0.0.1 on port 4200 when the port is unset or empty', () => {
    assert.equal(resolveAppOrigin({}), ORIGIN);
    assert.equal(resolveAppOrigin({ NARRASTEP_WEB_PORT: '' }), ORIGIN);
  });

  it('uses the configured port on 127.0.0.1', () => {
    assert.equal(resolveAppOrigin({ NARRASTEP_WEB_PORT: '4310' }), 'http://127.0.0.1:4310');
    assert.equal(resolveAppOrigin({ NARRASTEP_WEB_PORT: '65535' }), 'http://127.0.0.1:65535');
    assert.equal(resolveAppOrigin({ NARRASTEP_WEB_PORT: '80' }), 'http://127.0.0.1');
  });

  for (const value of [
    '0',
    '65536',
    '-1',
    '+4200',
    ' 4200',
    '4200 ',
    '42.0',
    '0x10',
    '4200/x',
    'abc',
    '127.0.0.1:4200',
    '100000',
  ]) {
    it(`rejects ${JSON.stringify(value)}`, () => {
      assert.throws(
        () => resolveAppOrigin({ NARRASTEP_WEB_PORT: value }),
        DesktopConfigurationError,
      );
    });
  }

  it('names the setting in the error without echoing the value', () => {
    assert.throws(
      () => resolveAppOrigin({ NARRASTEP_WEB_PORT: 'evil.example' }),
      (error) => error.message.includes('NARRASTEP_WEB_PORT') && !error.message.includes('evil'),
    );
  });

  it('opens the root page of the origin', () => {
    assert.equal(appEntryUrl(ORIGIN), 'http://127.0.0.1:4200/');
  });
});

describe('isAppUrl', () => {
  for (const url of [
    'http://127.0.0.1:4200/',
    'http://127.0.0.1:4200',
    'http://127.0.0.1:4200/projects/1/review?tab=2#segment',
    'HTTP://127.0.0.1:4200/api/v1/health',
    'blob:http://127.0.0.1:4200/9b0f3c1e-1111-4a4a-9c9c-0123456789ab',
  ]) {
    it(`accepts ${url}`, () => {
      assert.equal(isAppUrl(url, ORIGIN), true);
    });
  }

  for (const url of [
    'http://localhost:4200/',
    'http://127.0.0.2:4200/',
    'http://[::1]:4200/',
    'http://127.0.0.1:4201/',
    'http://127.0.0.1/',
    'https://127.0.0.1:4200/',
    'ws://127.0.0.1:4200/',
    'http://127.0.0.1:4200@evil.example/',
    'http://evil.example/?u=http://127.0.0.1:4200/',
    'file:///C:/Users/owner/project.json',
    'data:text/html,hello',
    'about:blank',
    'javascript:alert(1)',
    'blob:null/9b0f3c1e',
    'not a url',
    '',
  ]) {
    it(`rejects ${JSON.stringify(url)}`, () => {
      assert.equal(isAppUrl(url, ORIGIN), false);
    });
  }

  it('rejects values that are not strings', () => {
    assert.equal(isAppUrl(undefined, ORIGIN), false);
    assert.equal(isAppUrl(null, ORIGIN), false);
    assert.equal(isAppUrl(new URL(ORIGIN), ORIGIN), false);
  });
});

describe('isAllowedRequestUrl', () => {
  it('allows app-origin and inline data requests', () => {
    assert.equal(isAllowedRequestUrl('http://127.0.0.1:4200/api/v1/projects', ORIGIN), true);
    assert.equal(isAllowedRequestUrl('data:image/png;base64,AAAA', ORIGIN), true);
    assert.equal(isAllowedRequestUrl('DATA:text/plain,x', ORIGIN), true);
  });

  it('cancels every other request', () => {
    for (const url of [
      'http://127.0.0.1:8002/api/v1/health',
      'http://localhost:4200/',
      'https://fonts.example/font.woff2',
      'ws://127.0.0.1:4200/ng-cli-ws',
      'file:///etc/passwd',
      'chrome://gpu',
      'ftp://127.0.0.1:4200/',
    ]) {
      assert.equal(isAllowedRequestUrl(url, ORIGIN), false, url);
    }
  });
});

describe('isPermissionAllowed', () => {
  it('allows only full screen, only for the app origin', () => {
    assert.deepEqual([...ALLOWED_PERMISSIONS], ['fullscreen']);
    assert.equal(
      isPermissionAllowed('fullscreen', 'http://127.0.0.1:4200/projects/1', ORIGIN),
      true,
    );
    assert.equal(isPermissionAllowed('fullscreen', ORIGIN, ORIGIN), true);
    assert.equal(isPermissionAllowed('fullscreen', 'http://127.0.0.1:9999/', ORIGIN), false);
    assert.equal(isPermissionAllowed('fullscreen', '', ORIGIN), false);
  });

  it('denies every other permission to the app origin', () => {
    for (const permission of [
      'media',
      'display-capture',
      'notifications',
      'geolocation',
      'openExternal',
      'clipboard-read',
      'clipboard-sanitized-write',
      'hid',
      'serial',
      'usb',
      'midi',
      'fileSystem',
      'unknown',
    ]) {
      assert.equal(isPermissionAllowed(permission, ORIGIN, ORIGIN), false, permission);
    }
  });

  it('cannot be widened at run time', () => {
    assert.throws(() => ALLOWED_PERMISSIONS.push('media'), TypeError);
  });
});

describe('WINDOW_WEB_PREFERENCES', () => {
  it('isolates the page from Node.js and Electron', () => {
    assert.equal(WINDOW_WEB_PREFERENCES.contextIsolation, true);
    assert.equal(WINDOW_WEB_PREFERENCES.sandbox, true);
    assert.equal(WINDOW_WEB_PREFERENCES.nodeIntegration, false);
    assert.equal(WINDOW_WEB_PREFERENCES.nodeIntegrationInWorker, false);
    assert.equal(WINDOW_WEB_PREFERENCES.nodeIntegrationInSubFrames, false);
    assert.equal(WINDOW_WEB_PREFERENCES.webviewTag, false);
    assert.equal(WINDOW_WEB_PREFERENCES.webSecurity, true);
    assert.equal(WINDOW_WEB_PREFERENCES.allowRunningInsecureContent, false);
    assert.equal(WINDOW_WEB_PREFERENCES.spellcheck, false);
    assert.equal('preload' in WINDOW_WEB_PREFERENCES, false);
    assert.equal(Object.isFrozen(WINDOW_WEB_PREFERENCES), true);
  });
});

describe('pickLanguage', () => {
  it('follows the page language once the app has loaded', () => {
    assert.equal(pickLanguage('it', 'en-US'), 'it');
    assert.equal(pickLanguage('en', 'it-IT'), 'en');
  });

  it('falls back to the system locale before the app has loaded', () => {
    assert.equal(pickLanguage(null, 'it-IT'), 'it');
    assert.equal(pickLanguage('', 'IT'), 'it');
    assert.equal(pickLanguage(null, 'en-GB'), 'en');
    assert.equal(pickLanguage(null, 'ita'), 'en');
    assert.equal(pickLanguage(undefined, undefined), 'en');
  });
});
