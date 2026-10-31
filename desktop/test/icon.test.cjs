// Tests that every Narrastep window shows the logo instead of Electron's default icon.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { describe, it } = require('node:test');

const { ASSETS_DIR, windowIconPath } = require('../src/icon.cjs');
const { startShell } = require('../src/shell.cjs');
const { createSourcePicker } = require('../src/source-picker.cjs');
const { windowOptions } = require('../src/window.cjs');
const { createFakeElectron } = require('./support/fake-electron.cjs');

describe('window icon path', () => {
  it('is the .ico on Windows and the 512 px PNG elsewhere, below the shell folder', () => {
    assert.equal(windowIconPath('win32'), path.join(__dirname, '..', 'assets', 'narrastep.ico'));
    for (const platform of ['linux', 'darwin']) {
      assert.equal(
        windowIconPath(platform),
        path.join(__dirname, '..', 'assets', 'narrastep-512.png'),
      );
    }
    assert.ok(path.isAbsolute(windowIconPath('win32')));
    assert.equal(ASSETS_DIR, path.join(__dirname, '..', 'assets'));
  });

  it('names files that exist and carry the right signature', () => {
    const ico = fs.readFileSync(windowIconPath('win32'));
    assert.deepEqual([...ico.subarray(0, 4)], [0, 0, 1, 0]);
    const png = fs.readFileSync(windowIconPath('linux'));
    assert.equal(png.subarray(1, 4).toString('latin1'), 'PNG');
  });

  it('follows the running platform by default', () => {
    assert.equal(windowIconPath(), windowIconPath(process.platform));
  });
});

describe('window icon option', () => {
  it('is set on the main window options for each platform', () => {
    const workArea = { width: 1920, height: 1080 };
    assert.equal(windowOptions(workArea, 'win32').icon, windowIconPath('win32'));
    assert.equal(windowOptions(workArea, 'linux').icon, windowIconPath('linux'));
  });

  it('reaches the main window the shell creates', async () => {
    const fake = createFakeElectron();
    const window = await startShell({ electron: fake.electron, env: {}, platform: 'win32' }).ready;
    assert.equal(window.options.icon, windowIconPath());
  });

  it('reaches the source picker window', async () => {
    const fake = createFakeElectron({ sources: [] });
    const { pickSource } = createSourcePicker({
      BrowserWindow: fake.electron.BrowserWindow,
      desktopCapturer: fake.electron.desktopCapturer,
      getParent: () => null,
      getLanguage: () => 'en',
    });
    const result = pickSource();
    await new Promise(setImmediate);
    const picker = fake.windows.at(-1);
    assert.equal(picker.options.icon, windowIconPath());
    picker.emit('closed');
    assert.equal(await result, null);
  });
});
