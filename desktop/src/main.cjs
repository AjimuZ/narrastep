// Electron entry point of the Narrastep desktop window: starts the shell or exits with a configuration error.
'use strict';

const electron = require('electron');

const { DesktopConfigurationError } = require('./policy.cjs');
const { startShell } = require('./shell.cjs');

try {
  startShell({ electron, env: process.env, platform: process.platform });
} catch (error) {
  if (!(error instanceof DesktopConfigurationError)) {
    throw error;
  }
  // Exit code 2 marks a configuration error for the launcher (P07.2); nothing was opened.
  process.stderr.write(`Narrastep desktop: ${error.message}\n`);
  electron.app.exit(2);
}
