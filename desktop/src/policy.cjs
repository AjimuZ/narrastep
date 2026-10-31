// Security policy of the desktop shell: the one loopback origin it loads and what that origin may do (ADR 0014).
'use strict';

/**
 * Host of every URL the shell loads. `localhost` is not used: it may resolve to `::1` or to an
 * address chosen by the hosts file, and it is a different origin for the browser.
 */
const LOOPBACK_HOST = '127.0.0.1';

/** Web port used when `NARRASTEP_WEB_PORT` is unset or empty, as in Compose and `.env.example`. */
const DEFAULT_WEB_PORT = 4200;

/**
 * Chromium permissions the app origin may use; every other permission is denied, for every origin.
 * `fullscreen` lets the native video controls in Review fill the screen and exposes no host data.
 * `display-capture` only lets a screen request reach the display-media handler: Chromium asks for it
 * first and rejects the request when it is denied, and the handler (P07.3a) and its picker still
 * decide what is shared. `media` is the microphone: Chromium uses the same name for the camera, so
 * `isPermissionAllowed` also requires the request to ask for audio only.
 */
const ALLOWED_PERMISSIONS = Object.freeze(['display-capture', 'fullscreen', 'media']);

/**
 * Web preferences of every Narrastep window. The page runs as an ordinary sandboxed web page:
 * no Node.js in the page, its workers or its frames, no preload script, context isolation on,
 * no webviews, no mixed or insecure content, no file drops that navigate, and no spell checker
 * (Chromium would download dictionaries from the Internet).
 */
const WINDOW_WEB_PREFERENCES = Object.freeze({
  contextIsolation: true,
  sandbox: true,
  nodeIntegration: false,
  nodeIntegrationInWorker: false,
  nodeIntegrationInSubFrames: false,
  webSecurity: true,
  allowRunningInsecureContent: false,
  webviewTag: false,
  navigateOnDragDrop: false,
  experimentalFeatures: false,
  spellcheck: false,
  safeDialogs: true,
});

/** Raised when the shell configuration would make it load something other than the loopback app. */
class DesktopConfigurationError extends Error {
  /** Creates the error with an English message that names the setting and the accepted values. */
  constructor(message) {
    super(message);
    this.name = 'DesktopConfigurationError';
  }
}

/**
 * Returns the app origin, `http://127.0.0.1:{port}`, from `env.NARRASTEP_WEB_PORT` (4200 when
 * unset or empty). Port 80 yields `http://127.0.0.1`, the browser's form of that origin.
 * Requires one to five decimal digits with a value from 1 to 65535: no sign, spaces, host or
 * scheme. Throws `DesktopConfigurationError` for anything else, so a malformed value can never
 * widen what the shell loads.
 */
function resolveAppOrigin(env) {
  const raw = env.NARRASTEP_WEB_PORT;
  if (raw === undefined || raw === '') {
    return `http://${LOOPBACK_HOST}:${DEFAULT_WEB_PORT}`;
  }
  const port = /^[0-9]{1,5}$/.test(raw) ? Number(raw) : Number.NaN;
  if (!(port >= 1 && port <= 65535)) {
    throw new DesktopConfigurationError(
      'NARRASTEP_WEB_PORT must be a port number from 1 to 65535.',
    );
  }
  return new URL(`http://${LOOPBACK_HOST}:${port}`).origin;
}

/** Returns the URL the window opens: the app origin's root page. */
function appEntryUrl(appOrigin) {
  return `${appOrigin}/`;
}

/**
 * Returns true only when `url` is a string whose origin is exactly `appOrigin` (scheme, host and
 * port). `blob:` URLs created by the app count, because a blob URL's origin is its creator's.
 * Unparsable values, `localhost`, other loopback addresses or ports, `https:`, `ws:`, `file:`,
 * `data:` and `about:` URLs all return false.
 */
function isAppUrl(url, appOrigin) {
  if (typeof url !== 'string') {
    return false;
  }
  let parsed;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  return parsed.origin === appOrigin;
}

/**
 * Returns whether the network layer may perform a request for `url`: app-origin URLs and inline
 * `data:` URLs, which never reach the network. Every other request is cancelled, so the app (or
 * anything injected into it) cannot reach another server, another local service or another port.
 */
function isAllowedRequestUrl(url, appOrigin) {
  if (isAppUrl(url, appOrigin)) {
    return true;
  }
  return typeof url === 'string' && url.slice(0, 5).toLowerCase() === 'data:';
}

/**
 * Returns whether `permission` may be granted to the page at `requestingUrl` (a URL or a bare
 * origin): true only for a name in `ALLOWED_PERMISSIONS` requested by the app origin.
 * `mediaTypes` lists what a `media` request asks for (`'audio'`, `'video'`); the microphone is
 * granted only when it is a non-empty list of `'audio'`, so a camera or unknown request is denied.
 */
function isPermissionAllowed(permission, requestingUrl, appOrigin, mediaTypes) {
  if (!ALLOWED_PERMISSIONS.includes(permission) || !isAppUrl(requestingUrl, appOrigin)) {
    return false;
  }
  if (permission === 'media') {
    return (
      Array.isArray(mediaTypes) &&
      mediaTypes.length > 0 &&
      mediaTypes.every((type) => type === 'audio')
    );
  }
  return true;
}

/**
 * Returns the language of the shell's own texts, `'it'` or `'en'`, with the web app's rule
 * (ADR 0015): the page's `<html lang>` when the app has loaded, otherwise the system locale;
 * Italian for `it` and `it-*`, English for everything else.
 */
function pickLanguage(pageLanguage, systemLocale) {
  const candidate =
    typeof pageLanguage === 'string' && pageLanguage !== '' ? pageLanguage : systemLocale;
  const lower = typeof candidate === 'string' ? candidate.toLowerCase() : '';
  return lower === 'it' || lower.startsWith('it-') ? 'it' : 'en';
}

module.exports = {
  ALLOWED_PERMISSIONS,
  DEFAULT_WEB_PORT,
  DesktopConfigurationError,
  LOOPBACK_HOST,
  WINDOW_WEB_PREFERENCES,
  appEntryUrl,
  isAllowedRequestUrl,
  isAppUrl,
  isPermissionAllowed,
  pickLanguage,
  resolveAppOrigin,
};
