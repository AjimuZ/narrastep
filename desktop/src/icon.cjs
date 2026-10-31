// Narrastep window icon: picks the logo file that the shell's windows show instead of Electron's default.
'use strict';

const path = require('node:path');

/** Folder of the logo files, next to `src/`; the packager keeps it in the packaged application. */
const ASSETS_DIR = path.join(__dirname, '..', 'assets');

/**
 * Returns the absolute path of the logo for `platform` (defaults to the running platform):
 * `narrastep.ico` on Windows, which holds 16 to 256 px for the title bar and the taskbar, and the
 * 512 px PNG elsewhere. macOS has no `.icns` file, so its windows and packaged build keep the
 * default icon there. The path is resolved from the shell's own folder, never from the current
 * directory, so it is the same whatever way the shell starts.
 */
function windowIconPath(platform = process.platform) {
  return path.join(ASSETS_DIR, platform === 'win32' ? 'narrastep.ico' : 'narrastep-512.png');
}

module.exports = { ASSETS_DIR, windowIconPath };
