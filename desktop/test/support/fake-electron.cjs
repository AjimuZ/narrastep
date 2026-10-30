// In-memory stand-ins for the Electron modules the shell uses, recording every call in order for unit tests.
'use strict';

const { EventEmitter } = require('node:events');

/** Event object of Electron handlers: records whether a handler called `preventDefault()`. */
class FakeEvent {
  /** Copies `properties` (for example `url`) onto the event. */
  constructor(properties = {}) {
    Object.assign(this, properties);
    this.defaultPrevented = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }
}

/**
 * Returns `{ electron, calls, windows, session, dialogChoices }`. `electron` has `app`,
 * `BrowserWindow`, `desktopCapturer`, `dialog`, `Menu` and `session` with the methods the shell calls; `calls` lists
 * `'module.method'` names in call order; `windows` lists created windows. `lock` is the result of
 * `requestSingleInstanceLock()`, `locale` of `app.getLocale()`, `dialogChoices` the button
 * indexes `dialog.showMessageBoxSync()` returns, in order (0 when empty), and `workArea` the
 * primary display's work area size. `sources` is what `desktopCapturer.getSources()` resolves to,
 * or it rejects with `sourcesError` when that is set.
 */
function createFakeElectron({
  lock = true,
  locale = 'en-US',
  dialogChoices = [],
  workArea = { width: 1920, height: 1040 },
  sources = [],
  sourcesError = null,
} = {}) {
  const calls = [];
  const windows = [];
  const dialogs = [];
  const record = (name) => calls.push(name);

  const app = new EventEmitter();
  Object.assign(app, {
    enableSandbox: () => record('app.enableSandbox'),
    requestSingleInstanceLock: () => {
      record('app.requestSingleInstanceLock');
      return lock;
    },
    quit: () => record('app.quit'),
    exit: (code) => record(`app.exit:${code}`),
    getLocale: () => locale,
    whenReady: () => {
      record('app.whenReady');
      return Promise.resolve();
    },
  });

  const session = {
    handlers: {},
    spellCheckerEnabled: true,
    webRequest: {
      onBeforeRequest(filter, listener) {
        record('session.webRequest.onBeforeRequest');
        session.handlers.request = { filter, listener };
      },
    },
    setPermissionRequestHandler(handler) {
      record('session.setPermissionRequestHandler');
      session.handlers.permissionRequest = handler;
    },
    setPermissionCheckHandler(handler) {
      record('session.setPermissionCheckHandler');
      session.handlers.permissionCheck = handler;
    },
    setDevicePermissionHandler(handler) {
      record('session.setDevicePermissionHandler');
      session.handlers.devicePermission = handler;
    },
    setDisplayMediaRequestHandler(handler) {
      record('session.setDisplayMediaRequestHandler');
      session.handlers.displayMedia = handler;
    },
    setSpellCheckerEnabled(enabled) {
      record('session.setSpellCheckerEnabled');
      session.spellCheckerEnabled = enabled;
    },
  };

  class FakeWebContents extends EventEmitter {
    constructor() {
      super();
      this.url = '';
      this.loads = [];
      this.scripts = [];
      this.zoomFactor = 1;
      this.pageLanguage = 'en';
      this.windowOpenHandler = null;
    }

    loadURL(url) {
      record('contents.loadURL');
      this.loads.push(url);
      this.url = url;
      return Promise.resolve();
    }

    getURL() {
      return this.url;
    }

    executeJavaScript(code) {
      this.scripts.push(code);
      return Promise.resolve(this.pageLanguage);
    }

    setWindowOpenHandler(handler) {
      record('contents.setWindowOpenHandler');
      this.windowOpenHandler = handler;
    }

    getZoomFactor() {
      return this.zoomFactor;
    }

    setZoomFactor(factor) {
      this.zoomFactor = factor;
    }
  }

  class FakeBrowserWindow extends EventEmitter {
    constructor(options) {
      super();
      record('BrowserWindow.new');
      this.options = options;
      this.webContents = new FakeWebContents();
      this.state = { visible: false, minimized: false, focused: false, destroyed: false };
      windows.push(this);
      // Electron announces the new contents before the constructor returns.
      app.emit('web-contents-created', new FakeEvent(), this.webContents);
    }

    show() {
      this.state.visible = true;
    }

    focus() {
      this.state.focused = true;
    }

    restore() {
      this.state.minimized = false;
    }

    isMinimized() {
      return this.state.minimized;
    }

    isDestroyed() {
      return this.state.destroyed;
    }

    // Like Electron, closing destroys the window and then announces `closed`.
    close() {
      record('BrowserWindow.close');
      this.state.destroyed = true;
      this.state.visible = false;
      this.emit('closed');
    }
  }

  const desktopCapturer = {
    requests: [],
    async getSources(options) {
      record('desktopCapturer.getSources');
      desktopCapturer.requests.push(options);
      if (sourcesError) {
        throw sourcesError;
      }
      return sources;
    },
  };

  const electron = {
    app,
    session: { defaultSession: session },
    BrowserWindow: FakeBrowserWindow,
    desktopCapturer,
    screen: {
      getPrimaryDisplay: () => ({ workAreaSize: workArea }),
    },
    Menu: {
      setApplicationMenu(menu) {
        record(`Menu.setApplicationMenu:${menu}`);
      },
    },
    dialog: {
      showMessageBoxSync(window, options) {
        dialogs.push({ window, options });
        return dialogChoices.shift() ?? 0;
      },
    },
  };
  return { electron, calls, windows, dialogs, session, FakeWebContents };
}

module.exports = { FakeEvent, createFakeElectron };
