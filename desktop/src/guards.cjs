// Installs the loopback-only, deny-by-default rules on the Electron session and on every web contents (ADR 0014).
'use strict';

const { isAllowedRequestUrl, isAppUrl, isPermissionAllowed } = require('./policy.cjs');

/**
 * Applies the session rules for `appOrigin` to an Electron `Session`. Must run before the first
 * window loads. Afterwards:
 * - every network request outside the app origin is cancelled (only inline `data:` is exempt);
 * - permission requests and checks are denied, except the policy's allowed permissions for the
 *   app origin, so external protocol links, notifications, location, camera and microphone fail;
 * - HID, serial and USB devices are refused, and screen capture requests are denied;
 * - the spell checker is off, so Chromium never downloads dictionaries.
 * Throws only if the session object lacks one of these Electron methods.
 */
function installSessionGuards(session, appOrigin) {
  session.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    callback({ cancel: !isAllowedRequestUrl(details.url, appOrigin) });
  });
  session.setPermissionRequestHandler((_contents, permission, callback, details) => {
    callback(isPermissionAllowed(permission, details.requestingUrl, appOrigin));
  });
  session.setPermissionCheckHandler((_contents, permission, requestingOrigin) =>
    isPermissionAllowed(permission, requestingOrigin, appOrigin),
  );
  session.setDevicePermissionHandler(() => false);
  // `null` streams deny getDisplayMedia(); the source picker of P07.3 replaces this handler.
  session.setDisplayMediaRequestHandler((_request, callback) => callback(null));
  session.setSpellCheckerEnabled(false);
}

/**
 * Applies the per-contents rules for `appOrigin` to an Electron `WebContents`: no frame navigates
 * or is redirected outside the app origin, the page never opens a new window and never attaches
 * a webview. The shell calls it from `web-contents-created`, so it covers contents created later.
 */
function installContentsGuards(contents, appOrigin) {
  const keepInsideApp = (event) => {
    if (!isAppUrl(event.url, appOrigin)) {
      event.preventDefault();
    }
  };
  // `will-frame-navigate` covers the main frame and subframes; `will-redirect` covers server redirects.
  contents.on('will-frame-navigate', keepInsideApp);
  contents.on('will-redirect', keepInsideApp);
  contents.on('will-attach-webview', (event) => event.preventDefault());
  contents.setWindowOpenHandler(() => ({ action: 'deny' }));
}

module.exports = { installContentsGuards, installSessionGuards };
