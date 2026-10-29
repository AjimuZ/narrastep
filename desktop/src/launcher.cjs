// Host-side launcher of the desktop window: waits for a healthy stack, installs the pinned Electron once and opens the shell (ADR 0014).
'use strict';

const childProcess = require('node:child_process');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');

const { DesktopConfigurationError, LOOPBACK_HOST, resolveAppOrigin } = require('./policy.cjs');

/** Oldest Node.js release the launcher and the pinned Electron support (`engines` in `package.json`). */
const MINIMUM_NODE = Object.freeze({ major: 22, minor: 12 });

/** Exit codes of `launch.cjs`; `CONFIGURATION` is also the shell's code for an invalid port. */
const EXIT = Object.freeze({ OK: 0, FAILED: 1, CONFIGURATION: 2 });

/** Upper bound for the wait for health; a first start builds images, so it is generous. */
const DEFAULT_HEALTH_TIMEOUT_MS = 60 * 60 * 1000;

/** Time the API may answer `degraded` (for example no worker yet) before the window opens anyway. */
const DEFAULT_DEGRADED_GRACE_MS = 60 * 1000;

/** Pause between two health requests. */
const HEALTH_POLL_MS = 1000;

/** Socket timeout of one health request. */
const HEALTH_REQUEST_TIMEOUT_MS = 3000;

/** Signals that stop the launcher while the window is open; each also closes the window. */
const STOP_SIGNALS = Object.freeze(['SIGTERM', 'SIGINT', 'SIGHUP']);

/** Largest health response body read; a longer answer is not Narrastep's health object. */
const HEALTH_BODY_LIMIT = 65536;

/**
 * The only `ELECTRON_*` variables (any case) kept for the install: the proxy switch and progress
 * setting of `@electron/get`, and its cache folder, whose files are checked against the pinned
 * `checksums.json` before use. Every other one is dropped, because Electron's install script reads
 * them to change the mirror, folder, file name, version, platform, architecture or checksum source
 * of the binary, or to skip the download.
 */
const KEPT_ELECTRON_VARIABLES = Object.freeze([
  'ELECTRON_GET_USE_PROXY',
  'ELECTRON_GET_NO_PROGRESS',
  'ELECTRON_CONFIG_CACHE',
]);

/** Raised for a problem the user can fix; its English message is printed as is, with a headless hint. */
class LauncherError extends Error {
  /** Creates the error with the message to print and the exit code of `launch.cjs`. */
  constructor(message, exitCode = EXIT.FAILED) {
    super(message);
    this.name = 'LauncherError';
    this.exitCode = exitCode;
  }
}

/** Resolves after `milliseconds`. */
function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

/**
 * Returns the value of `key` in the text of an `.env` file, or undefined. Follows the subset of
 * Compose's rules the stack uses: `KEY=value` lines, an optional `export ` prefix, `#` comment
 * lines, single or double quotes around the value, and ` #` starting a comment after an unquoted
 * value. The last assignment wins. Never throws.
 */
function readEnvFileValue(text, key) {
  let found;
  for (const rawLine of String(text).split(/\r?\n/)) {
    const line = rawLine.trim();
    if (line === '' || line.startsWith('#')) {
      continue;
    }
    const match = /^(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*=\s*(.*)$/.exec(line);
    if (!match || match[1] !== key) {
      continue;
    }
    let value = match[2];
    const quote = value[0];
    if ((quote === '"' || quote === "'") && value.indexOf(quote, 1) > 0) {
      value = value.slice(1, value.indexOf(quote, 1));
    } else {
      const comment = value.search(/\s#/);
      value = (comment === -1 ? value : value.slice(0, comment)).trim();
    }
    found = value;
  }
  return found;
}

/**
 * Returns the environment for the shell with the web port Compose publishes: the process
 * environment wins over the project's `.env` file, as in Compose. `NARRASTEP_WEB_PORT` is only
 * set when one of them defines it; validation stays with `resolveAppOrigin`. A missing or
 * unreadable `.env` file is not an error.
 */
function resolveShellEnvironment({ env, projectDirectory }) {
  const result = { ...env };
  if (result.NARRASTEP_WEB_PORT === undefined) {
    try {
      const fromFile = readEnvFileValue(
        fs.readFileSync(path.join(projectDirectory, '.env'), 'utf8'),
        'NARRASTEP_WEB_PORT',
      );
      if (fromFile !== undefined) {
        result.NARRASTEP_WEB_PORT = fromFile;
      }
    } catch {
      // No .env file: the default port of Compose applies.
    }
  }
  // Electron would run as plain Node.js, without a window, when this is inherited from an editor.
  delete result.ELECTRON_RUN_AS_NODE;
  return result;
}

/**
 * Throws a `LauncherError` when `version` (such as `process.versions.node`) is older than Node.js
 * 22.12, which the pinned Electron package and its install script require.
 */
function assertSupportedNode(version) {
  const [major, minor] = String(version).split('.').map(Number);
  const supported =
    major > MINIMUM_NODE.major || (major === MINIMUM_NODE.major && minor >= MINIMUM_NODE.minor);
  if (!supported) {
    throw new LauncherError(
      `Node.js ${MINIMUM_NODE.major}.${MINIMUM_NODE.minor} or newer is required for the desktop ` +
        `window; this is Node.js ${version}.`,
    );
  }
}

/**
 * Returns the reason there is no place to open a window, or null. On Linux a window needs a
 * display server; Windows and macOS always have one.
 */
function displayProblem({ platform, env }) {
  if (platform === 'linux' && !env.DISPLAY && !env.WAYLAND_DISPLAY) {
    return 'No graphical session was found (neither DISPLAY nor WAYLAND_DISPLAY is set).';
  }
  return null;
}

/**
 * Reads one health response: returns `'ok'` or `'degraded'` for a 200 JSON body with that
 * `status`, `'unreachable'` for a connection error, timeout or other HTTP status, and
 * `'unhealthy'` for an answer that is not Narrastep's health object. Never rejects.
 */
function requestHealth(origin, timeoutMs = HEALTH_REQUEST_TIMEOUT_MS) {
  return new Promise((resolve) => {
    const request = http.get(`${origin}/api/v1/health`, { timeout: timeoutMs }, (response) => {
      if (response.statusCode !== 200) {
        response.resume();
        resolve('unreachable');
        return;
      }
      let body = '';
      response.setEncoding('utf8');
      response.on('data', (chunk) => {
        body += chunk;
        if (body.length > HEALTH_BODY_LIMIT) {
          // A destroyed response emits neither 'end' nor 'error', so settle before destroying it.
          resolve('unhealthy');
          response.destroy();
        }
      });
      response.on('error', () => resolve('unreachable'));
      response.on('end', () => {
        try {
          const status = JSON.parse(body)?.status;
          resolve(status === 'ok' || status === 'degraded' ? status : 'unhealthy');
        } catch {
          resolve('unhealthy');
        }
      });
      // 'close' follows 'end' or 'error'; on its own it means the answer stopped early.
      response.on('close', () => resolve('unreachable'));
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve('unreachable'));
  });
}

/**
 * Waits until the web container proxies a healthy API at `origin`: `status: "ok"` (database,
 * Redis and a worker all answer). An API that answers `degraded` for longer than `degradedGraceMs`
 * is accepted too, because the app itself shows what is missing. Polls every `pollMs`.
 * Rejects with a `LauncherError` after `timeoutMs`. `request`, `sleep` and `now` are injectable
 * for tests. Returns `'ok'` or `'degraded'`.
 */
async function waitForHealth({
  origin,
  timeoutMs = DEFAULT_HEALTH_TIMEOUT_MS,
  degradedGraceMs = DEFAULT_DEGRADED_GRACE_MS,
  pollMs = HEALTH_POLL_MS,
  request = requestHealth,
  sleep = delay,
  now = Date.now,
  onWaiting = () => {},
}) {
  const startedAt = now();
  let degradedSince = null;
  let announced = false;
  for (;;) {
    const state = await request(origin);
    if (state === 'ok') {
      return 'ok';
    }
    if (state === 'degraded') {
      degradedSince ??= now();
      if (now() - degradedSince >= degradedGraceMs) {
        return 'degraded';
      }
    } else {
      degradedSince = null;
    }
    if (now() - startedAt >= timeoutMs) {
      throw new LauncherError(
        `Narrastep did not become healthy at ${origin} within ${Math.round(timeoutMs / 60000)} minutes.`,
      );
    }
    if (!announced) {
      announced = true;
      onWaiting();
    }
    await sleep(pollMs);
  }
}

/**
 * Returns the environment of the install commands: the caller's environment without any npm
 * setting (user and global `.npmrc` files, tokens, registries and `npm_config_*` variables) and
 * without the `ELECTRON_*` variables that change the Electron download (all but
 * `KEPT_ELECTRON_VARIABLES`). npm reads the empty files `userConfigFile` and `globalConfigFile`
 * (they must be two different paths) as its user and global configuration, uses the public npm
 * registry only and neither audits nor checks for updates, so the install makes no request beyond
 * fetching the locked packages and the Electron binary. Proxy variables such as `HTTPS_PROXY` are
 * kept.
 */
function isolatedInstallEnvironment(env, userConfigFile, globalConfigFile) {
  const clean = {};
  for (const [name, value] of Object.entries(env)) {
    const lower = name.toLowerCase();
    if (lower.startsWith('npm_config_') || lower.startsWith('npm_package_')) {
      continue;
    }
    if (lower.startsWith('electron_') && !KEPT_ELECTRON_VARIABLES.includes(name.toUpperCase())) {
      continue;
    }
    clean[name] = value;
  }
  return {
    ...clean,
    npm_config_userconfig: userConfigFile,
    npm_config_globalconfig: globalConfigFile,
    npm_config_registry: 'https://registry.npmjs.org/',
    npm_config_audit: 'false',
    npm_config_fund: 'false',
    npm_config_update_notifier: 'false',
    npm_config_ignore_scripts: 'true',
  };
}

/**
 * Returns the pinned Electron version from `package.json` in `desktopDirectory`, or throws a
 * `LauncherError` when the manifest is unreadable or pins no exact version.
 */
function pinnedElectronVersion(desktopDirectory) {
  let manifest;
  try {
    manifest = JSON.parse(fs.readFileSync(path.join(desktopDirectory, 'package.json'), 'utf8'));
  } catch {
    throw new LauncherError('desktop/package.json cannot be read.');
  }
  const version = manifest?.devDependencies?.electron;
  if (typeof version !== 'string' || !/^\d+\.\d+\.\d+$/.test(version)) {
    throw new LauncherError('desktop/package.json must pin an exact Electron version.');
  }
  return version;
}

/**
 * Returns the path of the installed Electron executable when `desktop/node_modules` holds exactly
 * the pinned version with its binary downloaded, otherwise null. It reads `path.txt`, written by
 * Electron's install script, and never `require`s the package: that would download a missing
 * binary without telling the user.
 */
function findInstalledElectron(desktopDirectory, pinnedVersion) {
  const packageDirectory = path.join(desktopDirectory, 'node_modules', 'electron');
  try {
    const installed = JSON.parse(
      fs.readFileSync(path.join(packageDirectory, 'package.json'), 'utf8'),
    );
    if (installed.version !== pinnedVersion) {
      return null;
    }
    const relative = fs.readFileSync(path.join(packageDirectory, 'path.txt'), 'utf8').trim();
    const executable = path.resolve(packageDirectory, 'dist', relative);
    const insideDist = path.relative(path.join(packageDirectory, 'dist'), executable);
    if (relative === '' || insideDist.startsWith('..') || path.isAbsolute(insideDist)) {
      return null;
    }
    return fs.statSync(executable).isFile() ? executable : null;
  } catch {
    return null;
  }
}

/**
 * Runs `command` with `args` in `cwd`, showing its output, and resolves with the exit code
 * (null when a signal ended it). Rejects with a `LauncherError` when it cannot start, for
 * example when `npm` is not installed. On Windows `npm` is a `.cmd` shim, which Node.js starts
 * only through a shell; it gets one command line joined from the fixed arguments, because Node.js
 * 24 warns (DEP0190) when an argument list is passed with a shell.
 */
function runCommand({
  command,
  args,
  cwd,
  env,
  platform = process.platform,
  spawn = childProcess.spawn,
}) {
  return new Promise((resolve, reject) => {
    const options = { cwd, env, stdio: 'inherit' };
    const child =
      platform === 'win32' && command === 'npm'
        ? spawn([command, ...args].join(' '), { ...options, shell: true })
        : spawn(command, args, options);
    child.on('error', (error) => {
      reject(
        new LauncherError(
          error.code === 'ENOENT'
            ? `${command} was not found. Install Node.js ${MINIMUM_NODE.major}.${MINIMUM_NODE.minor} or newer with npm.`
            : `${command} could not start (${error.code ?? 'error'}).`,
        ),
      );
    });
    child.on('exit', (code) => resolve(code));
  });
}

/**
 * Makes sure `desktop/node_modules` holds the pinned Electron with its binary and returns the
 * executable path. On the first launch (or after the pin changed) it runs, in `desktopDirectory`
 * with isolated settings, `npm ci` with scripts off and then Electron's own `install.js`, after
 * saying that the Internet is used once. Throws a `LauncherError` when a step fails; the project
 * and the running stack are untouched. `runner`, `makeTempDirectory` and `log` are injectable.
 */
async function ensureElectron({
  desktopDirectory,
  env,
  platform = process.platform,
  log,
  runner = runCommand,
  makeTempDirectory = (prefix) => fs.mkdtempSync(path.join(os.tmpdir(), prefix)),
}) {
  const version = pinnedElectronVersion(desktopDirectory);
  const existing = findInstalledElectron(desktopDirectory, version);
  if (existing) {
    return existing;
  }

  log(
    `First launch: installing Electron ${version} into desktop/node_modules. This downloads the ` +
      'locked npm packages from registry.npmjs.org and the Electron binary from GitHub releases once; ' +
      'it needs Internet access and no administrator rights.',
  );
  const scratch = makeTempDirectory('narrastep-npm-');
  try {
    // npm refuses to load one file as both its user and its global configuration.
    const userConfigFile = path.join(scratch, 'user-npmrc');
    const globalConfigFile = path.join(scratch, 'global-npmrc');
    fs.writeFileSync(userConfigFile, '');
    fs.writeFileSync(globalConfigFile, '');
    const installEnv = isolatedInstallEnvironment(env, userConfigFile, globalConfigFile);
    const steps = [
      {
        command: 'npm',
        args: ['ci', '--ignore-scripts', '--no-audit', '--no-fund'],
        label: 'npm ci',
      },
      {
        command: process.execPath,
        args: [path.join('node_modules', 'electron', 'install.js')],
        label: 'Electron download',
      },
    ];
    for (const step of steps) {
      const code = await runner({
        command: step.command,
        args: step.args,
        cwd: desktopDirectory,
        env: installEnv,
        platform,
      });
      if (code !== 0) {
        throw new LauncherError(
          `The desktop window install failed in ${step.label} (exit code ${code}). ` +
            'Check the Internet connection and the messages above.',
        );
      }
    }
  } finally {
    fs.rmSync(scratch, { recursive: true, force: true });
  }

  const installed = findInstalledElectron(desktopDirectory, version);
  if (!installed) {
    throw new LauncherError(`Electron ${version} is still missing after the install.`);
  }
  return installed;
}

/**
 * Starts the shell `executable` on `desktopDirectory` with `env` and resolves once it has exited
 * with `{ code, stoppedBy }`: its exit code (null for a signal) and the stop signal the launcher
 * received meanwhile, or null. While the shell runs, a `STOP_SIGNALS` signal for the launcher (the
 * start.sh watcher after Compose exited, or Ctrl+C) asks the shell to quit with SIGTERM instead of
 * ending the launcher at once, so the window never outlives the stack it shows; the launcher exits
 * after the shell. Rejects with a `LauncherError` when the shell cannot start. `spawn` and
 * `signals` (the emitter of process signals) are injectable for tests.
 */
function runShell({
  executable,
  desktopDirectory,
  env,
  spawn = childProcess.spawn,
  signals = process,
}) {
  return new Promise((resolve, reject) => {
    const child = spawn(executable, [desktopDirectory], { env, stdio: 'inherit' });
    let stoppedBy = null;
    const stop = (signal) => {
      stoppedBy ??= signal;
      child.kill('SIGTERM');
    };
    const release = () => STOP_SIGNALS.forEach((signal) => signals.off(signal, stop));
    STOP_SIGNALS.forEach((signal) => signals.on(signal, stop));
    child.on('error', (error) => {
      release();
      reject(new LauncherError(`The Electron shell could not start (${error.code ?? 'error'}).`));
    });
    child.on('exit', (code) => {
      release();
      resolve({ code, stoppedBy });
    });
  });
}

/**
 * Opens the Narrastep window for the stack that Compose runs in `projectDirectory`, in order:
 * checks Node.js and the display, validates the web port, waits for health, makes sure Electron
 * is installed, runs the shell and waits for the window to close. Returns the exit code of
 * `launch.cjs`: 0 after a normal close or after a stop signal closed the window, 2 for an invalid
 * `NARRASTEP_WEB_PORT`, 1 for every problem that leaves the stack usable in a browser; each
 * problem is explained on `log`. Closing the window never stops containers. Everything outside
 * `env`, `platform` and the directories is injectable for tests.
 */
async function launchDesktopWindow({
  projectDirectory,
  env = process.env,
  platform = process.platform,
  nodeVersion = process.versions.node,
  log = (message) => process.stdout.write(`Narrastep: ${message}\n`),
  healthTimeoutMs = DEFAULT_HEALTH_TIMEOUT_MS,
  degradedGraceMs = DEFAULT_DEGRADED_GRACE_MS,
  pollMs = HEALTH_POLL_MS,
  request = requestHealth,
  sleep = delay,
  now = Date.now,
  ensure = ensureElectron,
  shell = runShell,
}) {
  const desktopDirectory = path.join(projectDirectory, 'desktop');
  try {
    assertSupportedNode(nodeVersion);
    const problem = displayProblem({ platform, env });
    if (problem) {
      throw new LauncherError(problem);
    }
    const shellEnv = resolveShellEnvironment({ env, projectDirectory });
    const origin = resolveAppOrigin(shellEnv);

    const state = await waitForHealth({
      origin,
      timeoutMs: healthTimeoutMs,
      degradedGraceMs,
      pollMs,
      request,
      sleep,
      now,
      onWaiting: () => log(`Waiting for Narrastep to become healthy at ${origin} ...`),
    });
    const executable = await ensure({ desktopDirectory, env, platform, log });
    log(
      state === 'ok'
        ? `Opening the Narrastep window on ${origin}.`
        : `Opening the Narrastep window on ${origin}; the worker is not ready yet.`,
    );
    const { code, stoppedBy } = await shell({ executable, desktopDirectory, env: shellEnv });
    if (stoppedBy) {
      log('The stack is stopping, so the Narrastep window was closed.');
      return EXIT.OK;
    }
    if (code === EXIT.CONFIGURATION) {
      throw new LauncherError(
        'The desktop window refused its configuration (NARRASTEP_WEB_PORT).',
        EXIT.CONFIGURATION,
      );
    }
    if (code !== 0) {
      throw new LauncherError(
        `The desktop window stopped with ${code === null ? 'a signal' : `exit code ${code}`}. ` +
          'On Linux this can mean that Chromium cannot start its sandbox.',
      );
    }
    return EXIT.OK;
  } catch (error) {
    if (error instanceof DesktopConfigurationError) {
      log(`${error.message} Fix it in .env or the environment.`);
      return EXIT.CONFIGURATION;
    }
    if (error instanceof LauncherError) {
      log(
        `${error.message} Narrastep keeps running; open http://${LOOPBACK_HOST}:` +
          `${safePort(env, projectDirectory)} in a browser or restart with ./start.sh --headless.`,
      );
      return error.exitCode;
    }
    throw error;
  }
}

/** Returns the web port to show in a hint, falling back to the default when it is invalid. */
function safePort(env, projectDirectory) {
  try {
    return (
      new URL(resolveAppOrigin(resolveShellEnvironment({ env, projectDirectory }))).port || '80'
    );
  } catch {
    return '4200';
  }
}

module.exports = {
  DEFAULT_DEGRADED_GRACE_MS,
  DEFAULT_HEALTH_TIMEOUT_MS,
  EXIT,
  LauncherError,
  MINIMUM_NODE,
  assertSupportedNode,
  displayProblem,
  ensureElectron,
  findInstalledElectron,
  isolatedInstallEnvironment,
  launchDesktopWindow,
  pinnedElectronVersion,
  readEnvFileValue,
  requestHealth,
  resolveShellEnvironment,
  runCommand,
  runShell,
  waitForHealth,
};
