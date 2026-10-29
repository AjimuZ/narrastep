// Command-line entry of the desktop launcher: `node desktop/launch.cjs`, started by start.sh after Compose begins.
'use strict';

const path = require('node:path');

const { launchDesktopWindow } = require('./src/launcher.cjs');

launchDesktopWindow({ projectDirectory: path.resolve(__dirname, '..') }).then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    // An unexpected failure: name the type only, never a stack trace or a path.
    process.stderr.write(`Narrastep: the desktop launcher failed (${error?.name ?? 'Error'}).\n`);
    process.exitCode = 1;
  },
);
