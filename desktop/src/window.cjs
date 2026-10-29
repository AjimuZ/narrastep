// Narrastep main window: loads the app, shows a local page when it is unreachable, confirms leaving and zooms from the keyboard.
'use strict';

const { buildFallbackPageUrl } = require('./fallback-page.cjs');
const { messagesFor } = require('./messages.cjs');
const { WINDOW_WEB_PREFERENCES, appEntryUrl, isAppUrl, pickLanguage } = require('./policy.cjs');

/** Chromium's error for a navigation cancelled on purpose: by a guard, a newer load or a download. */
const ERR_ABORTED = -3;

/** Zoom steps of the keyboard shortcuts: Chromium's browser presets from 25% to 500%. */
const ZOOM_FACTORS = Object.freeze([
  0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5,
]);

/** Preferred and minimum window size in pixels; 320 is the narrowest width the web app supports. */
const WINDOW_SIZE = Object.freeze({ width: 1280, height: 800, minWidth: 320, minHeight: 480 });

/**
 * Returns fresh `BrowserWindow` options for a display whose usable area is `workArea`
 * (`{ width, height }` in pixels). The window is 1280 × 800, reduced to fit the work area of a
 * smaller screen but never below 320 × 480. It starts hidden and appears once painted; its
 * background is `--color-bg` from `apps/web/src/styles/tokens.scss`, so it never flashes white.
 * Windows and Linux show no menu bar.
 */
function windowOptions(workArea) {
  const fit = (preferred, available, minimum) =>
    Math.max(minimum, Math.min(preferred, Number.isFinite(available) ? available : preferred));
  return {
    title: 'Narrastep',
    width: fit(WINDOW_SIZE.width, workArea?.width, WINDOW_SIZE.minWidth),
    height: fit(WINDOW_SIZE.height, workArea?.height, WINDOW_SIZE.minHeight),
    minWidth: WINDOW_SIZE.minWidth,
    minHeight: WINDOW_SIZE.minHeight,
    show: false,
    backgroundColor: '#2d2f42',
    autoHideMenuBar: true,
    webPreferences: { ...WINDOW_WEB_PREFERENCES },
  };
}

/**
 * Returns the zoom action of a keyboard event from `before-input-event`: `'in'` for Ctrl or Cmd
 * with `+` or `=`, `'out'` with `-` or `_`, `'reset'` with `0`, and `null` for every other input
 * (key releases, Alt combinations and unmodified keys included). Without a menu, Electron has no
 * zoom shortcuts of its own; these keep the browser's 200% and 400% zoom available.
 */
function zoomShortcut(input) {
  if (input.type !== 'keyDown' || input.alt || !(input.control || input.meta)) {
    return null;
  }
  switch (input.key) {
    case '+':
    case '=':
      return 'in';
    case '-':
    case '_':
      return 'out';
    case '0':
      return 'reset';
    default:
      return null;
  }
}

/**
 * Returns the zoom factor after `action` from `current`: the next larger or smaller step of
 * `ZOOM_FACTORS`, clamped to 25% and 500%, or 1 for `'reset'`. A `current` between two steps moves
 * to the neighbouring step in the requested direction.
 */
function nextZoomFactor(current, action) {
  if (action === 'reset') {
    return 1;
  }
  // The tolerance absorbs the rounding of factors Chromium stores as zoom levels.
  if (action === 'in') {
    return ZOOM_FACTORS.find((factor) => factor > current + 0.001) ?? ZOOM_FACTORS.at(-1);
  }
  return ZOOM_FACTORS.findLast((factor) => factor < current - 0.001) ?? ZOOM_FACTORS[0];
}

/**
 * Creates the main window and starts loading the app at `appOrigin`. Requires Electron's
 * `BrowserWindow` and `dialog`, the app origin from the policy, the system locale and the work
 * area of the primary display.
 * The session and contents guards must already be installed: the load starts immediately.
 * Returns `{ window, bringToFront }`; `bringToFront()` restores, shows and focuses the window and,
 * when it shows the fallback page, loads the app again. It does nothing once the window is closed.
 *
 * Behavior:
 * - a main-frame load of the app that fails (stack stopped, wrong port) shows the local fallback
 *   page with a "Try again" link; cancelled loads and other URLs are ignored;
 * - when the page's `beforeunload` asks to stay (recording, uploads or unsaved edits in the app),
 *   a native dialog asks; Electron would otherwise cancel the close or reload without a word;
 * - Ctrl or Cmd with `+`, `-` and `0` zoom the page.
 */
function createMainWindow({ BrowserWindow, dialog, appOrigin, systemLocale, workArea }) {
  const entryUrl = appEntryUrl(appOrigin);
  const window = new BrowserWindow(windowOptions(workArea));
  const contents = window.webContents;
  let pageLanguage = null;
  const language = () => pickLanguage(pageLanguage, systemLocale);
  // The returned promise rejects on a failed load; `did-fail-load` handles that case.
  const load = (url) => {
    contents.loadURL(url).catch(() => {});
  };

  window.once('ready-to-show', () => window.show());

  contents.on('did-finish-load', () => {
    if (!isAppUrl(contents.getURL(), appOrigin)) {
      return;
    }
    // The app sets `<html lang>` from the user's choice; the shell's texts follow it.
    contents.executeJavaScript('document.documentElement.lang').then(
      (value) => {
        pageLanguage = typeof value === 'string' ? value : null;
      },
      () => {},
    );
  });

  contents.on('did-fail-load', (_event, errorCode, _description, validatedUrl, isMainFrame) => {
    if (!isMainFrame || errorCode === ERR_ABORTED || !isAppUrl(validatedUrl, appOrigin)) {
      return;
    }
    const current = language();
    load(buildFallbackPageUrl(current, messagesFor(current), entryUrl));
    window.show();
  });

  contents.on('will-prevent-unload', (event) => {
    const text = messagesFor(language());
    const choice = dialog.showMessageBoxSync(window, {
      type: 'question',
      title: 'Narrastep',
      message: text.leaveTitle,
      detail: text.leaveDetail,
      buttons: [text.leaveStay, text.leaveConfirm],
      defaultId: 0,
      cancelId: 0,
      noLink: true,
    });
    // preventDefault() overrides the page's beforeunload, so the close or reload goes ahead.
    if (choice === 1) {
      event.preventDefault();
    }
  });

  contents.on('before-input-event', (event, input) => {
    const action = zoomShortcut(input);
    if (action === null) {
      return;
    }
    event.preventDefault();
    contents.setZoomFactor(nextZoomFactor(contents.getZoomFactor(), action));
  });

  load(entryUrl);

  return {
    window,
    bringToFront() {
      if (window.isDestroyed()) {
        return;
      }
      if (window.isMinimized()) {
        window.restore();
      }
      window.show();
      window.focus();
      if (!isAppUrl(contents.getURL(), appOrigin)) {
        load(entryUrl);
      }
    },
  };
}

module.exports = {
  WINDOW_SIZE,
  ZOOM_FACTORS,
  createMainWindow,
  nextZoomFactor,
  windowOptions,
  zoomShortcut,
};
