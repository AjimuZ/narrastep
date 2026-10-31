// Installs the loopback-only, deny-by-default rules on the Electron session and on every web contents (ADR 0014).
'use strict';

const { isAllowedRequestUrl, isAppUrl, isPermissionAllowed } = require('./policy.cjs');

/**
 * Applies the session rules for `appOrigin` to an Electron `Session`. Must run before the first
 * window loads. Afterwards:
 * - every network request outside the app origin is cancelled (only inline `data:` is exempt);
 * - permission requests and checks are denied, except the policy's allowed permissions for the
 *   app origin (the microphone, audio only), so external protocol links, notifications, location
 *   and the camera fail;
 * - HID, serial and USB devices are refused;
 * - screen capture requests from the app origin get the Narrastep picker (`pickSource`, from
 *   `createSourcePicker`); other origins, and every request when `pickSource` is omitted, are denied;
 *   when `platform` is `'win32'` the grant also carries the system audio (loopback) of the chosen
 *   source, and on any other platform it carries video only;
 * - the spell checker is off, so Chromium never downloads dictionaries.
 * Throws only if the session object lacks one of these Electron methods.
 */
function installSessionGuards(session, appOrigin, pickSource, platform) {
  session.webRequest.onBeforeRequest({ urls: ['<all_urls>'] }, (details, callback) => {
    callback({ cancel: !isAllowedRequestUrl(details.url, appOrigin) });
  });
  session.setPermissionRequestHandler((_contents, permission, callback, details) => {
    callback(isPermissionAllowed(permission, details.requestingUrl, appOrigin, details.mediaTypes));
  });
  // A check reports one `mediaType` (or `'unknown'`), a request reports a `mediaTypes` list.
  session.setPermissionCheckHandler((_contents, permission, requestingOrigin, details) =>
    isPermissionAllowed(
      permission,
      requestingOrigin,
      appOrigin,
      details && details.mediaType ? [details.mediaType] : undefined,
    ),
  );
  session.setDevicePermissionHandler(() => false);
  // Answers the picker's source for the app origin and `null` (which rejects getDisplayMedia()) otherwise.
  session.setDisplayMediaRequestHandler((request, callback) => {
    answerDisplayMediaRequest(request, callback, appOrigin, pickSource, platform);
  });
  session.setSpellCheckerEnabled(false);
}

/**
 * Returns whether a screen capture `request` comes from the app origin: its security origin and,
 * when Electron reports the frame, the frame's URL must both be the app origin, and it must ask
 * for video. Anything else, including a missing or malformed request, returns false.
 */
function isAppCaptureRequest(request, appOrigin) {
  if (request === null || typeof request !== 'object' || request.videoRequested === false) {
    return false;
  }
  if (!isAppUrl(request.securityOrigin, appOrigin)) {
    return false;
  }
  return request.frame?.url === undefined || isAppUrl(request.frame.url, appOrigin);
}

/**
 * Answers one `getDisplayMedia()` request. A request from the app origin opens the picker through
 * `pickSource()` and is granted the video of the chosen source, plus `audio: 'loopback'` (the system
 * audio mix) when `platform` is `'win32'` and the request asked for audio; elsewhere Electron has no
 * loopback capture, so only video is granted. Cancelling, a picker failure, a foreign origin or a
 * missing `pickSource` all answer `null`, which makes the page's promise reject. Never throws.
 */
function answerDisplayMediaRequest(request, callback, appOrigin, pickSource, platform) {
  if (typeof pickSource !== 'function' || !isAppCaptureRequest(request, appOrigin)) {
    callback(null);
    return;
  }
  Promise.resolve()
    .then(() => pickSource())
    .then(
      (source) => callback(grantFor(source, request, platform)),
      () => callback(null),
    );
}

/**
 * Returns the grant for the picked `source`, or `null` when nothing was picked. Loopback audio is
 * added only on Windows and only when the page asked for audio (a missing flag counts as asked).
 */
function grantFor(source, request, platform) {
  if (!source) {
    return null;
  }
  const grant = { video: source };
  if (platform === 'win32' && request.audioRequested !== false) {
    grant.audio = 'loopback';
  }
  return grant;
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
