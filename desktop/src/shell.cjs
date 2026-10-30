// Lifecycle of the Narrastep desktop shell: one instance, guards before the first load, quit when the window closes (ADR 0014).
'use strict';

const { installContentsGuards, installSessionGuards } = require('./guards.cjs');
const { pickLanguage, resolveAppOrigin } = require('./policy.cjs');
const { createSourcePicker } = require('./source-picker.cjs');
const { createMainWindow } = require('./window.cjs');

/**
 * Starts the shell with the Electron modules in `electron` (`app`, `BrowserWindow`, `desktopCapturer`,
 * `dialog`, `Menu`, `screen`, `session`), the environment `env` and the `platform` name. Must run before Electron is
 * ready, because the renderer sandbox is enabled for every process here.
 *
 * Returns `{ primary, appOrigin, ready }`:
 * - in the first instance `primary` is true and `ready` resolves to the main `BrowserWindow` once
 *   the session guards are installed and the window has started loading the app;
 * - when another shell already runs with the same user data folder, this process asks Electron to
 *   quit, the running window comes to the front, `primary` is false and `ready` resolves to null.
 *
 * Throws `DesktopConfigurationError` before touching Electron when `NARRASTEP_WEB_PORT` is
 * invalid. Closing the window quits the shell on every platform; the shell never starts, stops
 * or contacts Docker, so containers and running jobs are unaffected.
 */
function startShell({ electron, env, platform }) {
  const { app, BrowserWindow, Menu, desktopCapturer, dialog, screen, session } = electron;
  const appOrigin = resolveAppOrigin(env);

  app.enableSandbox();
  if (!app.requestSingleInstanceLock()) {
    app.quit();
    return { primary: false, appOrigin, ready: Promise.resolve(null) };
  }

  let mainWindow = null;
  app.on('second-instance', () => {
    mainWindow?.bringToFront();
  });
  // Emitted for every web contents, including the main window's, before it loads anything.
  app.on('web-contents-created', (_event, contents) => {
    installContentsGuards(contents, appOrigin);
  });
  app.on('window-all-closed', () => {
    app.quit();
  });

  const ready = app.whenReady().then(() => {
    const picker = createSourcePicker({
      BrowserWindow,
      desktopCapturer,
      getParent: () => mainWindow?.window ?? null,
      getLanguage: () => mainWindow?.language() ?? pickLanguage(null, app.getLocale()),
    });
    installSessionGuards(session.defaultSession, appOrigin, picker.pickSource);
    // macOS keeps Electron's default menu, which carries the system editing shortcuts.
    if (platform !== 'darwin') {
      Menu.setApplicationMenu(null);
    }
    mainWindow = createMainWindow({
      BrowserWindow,
      dialog,
      appOrigin,
      systemLocale: app.getLocale(),
      workArea: screen.getPrimaryDisplay().workAreaSize,
    });
    return mainWindow.window;
  });
  return { primary: true, appOrigin, ready };
}

module.exports = { startShell };
