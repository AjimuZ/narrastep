// Unit tests of the desktop launcher: health wait, port from .env, first-launch install and exit codes, with injected stand-ins.
'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const http = require('node:http');
const os = require('node:os');
const path = require('node:path');
const { afterEach, beforeEach, describe, it } = require('node:test');

const {
  EXIT,
  LauncherError,
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
  waitForHealth,
} = require('../src/launcher.cjs');

const VERSION = '45.1.0';

let root;

beforeEach(() => {
  root = fs.mkdtempSync(path.join(os.tmpdir(), 'narrastep-launcher-test-'));
});

afterEach(() => {
  fs.rmSync(root, { recursive: true, force: true });
});

/** Creates `<root>/desktop` with a manifest pinning `version`; returns the desktop directory. */
function makeDesktop(version = VERSION) {
  const desktop = path.join(root, 'desktop');
  fs.mkdirSync(desktop, { recursive: true });
  fs.writeFileSync(
    path.join(desktop, 'package.json'),
    JSON.stringify({ devDependencies: { electron: version } }),
  );
  return desktop;
}

/** Writes an installed Electron package of `version` with a binary into `desktop/node_modules`. */
function installElectron(desktop, version = VERSION, { binary = true, pathTxt = 'electron' } = {}) {
  const packageDirectory = path.join(desktop, 'node_modules', 'electron');
  fs.mkdirSync(path.join(packageDirectory, 'dist'), { recursive: true });
  fs.writeFileSync(path.join(packageDirectory, 'package.json'), JSON.stringify({ version }));
  if (pathTxt !== null) {
    fs.writeFileSync(path.join(packageDirectory, 'path.txt'), pathTxt);
  }
  if (binary) {
    fs.writeFileSync(path.join(packageDirectory, 'dist', 'electron'), '');
  }
  return path.join(packageDirectory, 'dist', 'electron');
}

/** Returns a health stand-in that answers the given states in order, then repeats the last one. */
function scriptedHealth(...states) {
  const calls = [];
  const request = async (origin) => {
    calls.push(origin);
    return states[Math.min(calls.length - 1, states.length - 1)];
  };
  return { request, calls };
}

/** Returns a controllable clock and a `sleep` that advances it. */
function fakeClock() {
  let time = 0;
  return { now: () => time, sleep: async (ms) => void (time += ms) };
}

describe('readEnvFileValue', () => {
  it('reads plain, quoted, exported and commented assignments, and the last one wins', () => {
    const text = [
      '# NARRASTEP_WEB_PORT=1',
      'OTHER=2',
      'NARRASTEP_WEB_PORT=4300',
      'export NARRASTEP_WEB_PORT = "4400" # note',
      "NARRASTEP_WEB_PORT='4500'",
      'NARRASTEP_WEB_PORT=4600 # unquoted comment',
    ];
    assert.equal(readEnvFileValue(text.slice(0, 3).join('\n'), 'NARRASTEP_WEB_PORT'), '4300');
    assert.equal(readEnvFileValue(text.slice(0, 4).join('\r\n'), 'NARRASTEP_WEB_PORT'), '4400');
    assert.equal(readEnvFileValue(text.slice(0, 5).join('\n'), 'NARRASTEP_WEB_PORT'), '4500');
    assert.equal(readEnvFileValue(text.join('\n'), 'NARRASTEP_WEB_PORT'), '4600');
  });

  it('returns undefined when the key is absent and does not match longer names', () => {
    assert.equal(
      readEnvFileValue('NARRASTEP_WEB_PORT_X=1\n# NARRASTEP_WEB_PORT=2', 'NARRASTEP_WEB_PORT'),
      undefined,
    );
    assert.equal(readEnvFileValue('', 'NARRASTEP_WEB_PORT'), undefined);
  });
});

describe('resolveShellEnvironment', () => {
  it('takes the port from .env, lets the environment win and drops ELECTRON_RUN_AS_NODE', () => {
    fs.writeFileSync(path.join(root, '.env'), 'NARRASTEP_WEB_PORT=4321\n');
    const fromFile = resolveShellEnvironment({
      env: { ELECTRON_RUN_AS_NODE: '1', PATH: 'x' },
      projectDirectory: root,
    });
    assert.equal(fromFile.NARRASTEP_WEB_PORT, '4321');
    assert.equal(fromFile.ELECTRON_RUN_AS_NODE, undefined);
    assert.equal(fromFile.PATH, 'x');
    const fromEnvironment = resolveShellEnvironment({
      env: { NARRASTEP_WEB_PORT: '5000' },
      projectDirectory: root,
    });
    assert.equal(fromEnvironment.NARRASTEP_WEB_PORT, '5000');
  });

  it('leaves the port unset without a .env file so the default applies', () => {
    const result = resolveShellEnvironment({ env: {}, projectDirectory: root });
    assert.equal(result.NARRASTEP_WEB_PORT, undefined);
  });
});

describe('assertSupportedNode and displayProblem', () => {
  it('accepts Node.js 22.12 and newer and rejects older releases', () => {
    for (const ok of ['22.12.0', '22.20.1', '24.22.1', '30.0.0']) {
      assert.doesNotThrow(() => assertSupportedNode(ok), ok);
    }
    for (const old of ['22.11.9', '20.18.0', '18.0.0']) {
      assert.throws(() => assertSupportedNode(old), LauncherError, old);
    }
  });

  it('asks for a graphical session only on Linux', () => {
    assert.match(displayProblem({ platform: 'linux', env: {} }), /DISPLAY/);
    assert.equal(displayProblem({ platform: 'linux', env: { DISPLAY: ':0' } }), null);
    assert.equal(
      displayProblem({ platform: 'linux', env: { WAYLAND_DISPLAY: 'wayland-0' } }),
      null,
    );
    assert.equal(displayProblem({ platform: 'win32', env: {} }), null);
    assert.equal(displayProblem({ platform: 'darwin', env: {} }), null);
  });
});

describe('requestHealth', () => {
  /** Serves `handler` on a loopback port; returns the origin and a close function. */
  async function serve(handler) {
    const server = http.createServer(handler);
    await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
    return {
      origin: `http://127.0.0.1:${server.address().port}`,
      close: () => new Promise((resolve) => server.close(resolve)),
    };
  }

  it('reads ok and degraded, rejects other answers and survives a closed port', async () => {
    const answers = { current: [200, ''] };
    const server = await serve((request, response) => {
      assert.equal(request.url, '/api/v1/health');
      const [status, body] = answers.current;
      response.writeHead(status, { 'content-type': 'application/json' });
      response.end(body);
    });
    try {
      answers.current = [200, '{"status":"ok","worker":"ok"}'];
      assert.equal(await requestHealth(server.origin), 'ok');
      answers.current = [200, '{"status":"degraded"}'];
      assert.equal(await requestHealth(server.origin), 'degraded');
      answers.current = [200, '{"status":"starting"}'];
      assert.equal(await requestHealth(server.origin), 'unhealthy');
      answers.current = [200, '<html>nginx</html>'];
      assert.equal(await requestHealth(server.origin), 'unhealthy');
      answers.current = [502, '{"status":"ok"}'];
      assert.equal(await requestHealth(server.origin), 'unreachable');
    } finally {
      await server.close();
    }
    assert.equal(await requestHealth(server.origin), 'unreachable');
  });

  it('gives up on a server that never answers', async () => {
    const server = await serve(() => {});
    try {
      assert.equal(await requestHealth(server.origin, 100), 'unreachable');
    } finally {
      server.close();
    }
  });
});

describe('waitForHealth', () => {
  it('polls until the API reports ok and announces the wait once', async () => {
    const clock = fakeClock();
    const health = scriptedHealth('unreachable', 'unreachable', 'degraded', 'ok');
    let announced = 0;
    const state = await waitForHealth({
      origin: 'http://127.0.0.1:4200',
      request: health.request,
      ...clock,
      onWaiting: () => (announced += 1),
    });
    assert.equal(state, 'ok');
    assert.equal(health.calls.length, 4);
    assert.equal(announced, 1);
    assert.equal(clock.now(), 3000);
  });

  it('accepts a degraded API only after the grace period', async () => {
    const clock = fakeClock();
    const health = scriptedHealth('degraded');
    const state = await waitForHealth({
      origin: 'http://127.0.0.1:4200',
      request: health.request,
      degradedGraceMs: 5000,
      ...clock,
    });
    assert.equal(state, 'degraded');
    assert.equal(clock.now(), 5000);
  });

  it('restarts the grace period when the API becomes unreachable again', async () => {
    const clock = fakeClock();
    const health = scriptedHealth('degraded', 'degraded', 'unreachable', 'degraded', 'ok');
    const state = await waitForHealth({
      origin: 'http://127.0.0.1:4200',
      request: health.request,
      degradedGraceMs: 2500,
      ...clock,
    });
    assert.equal(state, 'ok');
  });

  it('fails with a LauncherError after the timeout', async () => {
    const clock = fakeClock();
    const health = scriptedHealth('unreachable');
    await assert.rejects(
      waitForHealth({
        origin: 'http://127.0.0.1:4200',
        request: health.request,
        timeoutMs: 10000,
        ...clock,
      }),
      (error) => error instanceof LauncherError && /did not become healthy/.test(error.message),
    );
    assert.equal(clock.now(), 10000);
  });
});

describe('isolatedInstallEnvironment', () => {
  it('drops npm settings and download redirects, sets safe ones and keeps proxies', () => {
    const result = isolatedInstallEnvironment(
      {
        PATH: '/bin',
        HTTPS_PROXY: 'http://proxy:3128',
        NPM_CONFIG_REGISTRY: 'https://private.example/',
        'npm_config_//registry.npmjs.org/:_authToken': 'secret',
        npm_package_name: 'x',
        npm_config_electron_mirror: 'https://mirror.example/',
        ELECTRON_MIRROR: 'https://mirror.example/',
        electron_custom_dir: 'x',
        ELECTRON_RUN_AS_NODE: '1',
        electron_config_cache: '/cache',
      },
      '/tmp/user-npmrc',
      '/tmp/global-npmrc',
    );
    assert.equal(result.PATH, '/bin');
    assert.equal(result.HTTPS_PROXY, 'http://proxy:3128');
    assert.equal(result.electron_config_cache, '/cache');
    assert.equal(result.NPM_CONFIG_REGISTRY, undefined);
    assert.equal(result.ELECTRON_MIRROR, undefined);
    assert.equal(result.electron_custom_dir, undefined);
    assert.equal(result['npm_config_//registry.npmjs.org/:_authToken'], undefined);
    assert.equal(result.npm_package_name, undefined);
    assert.equal(result.npm_config_electron_mirror, undefined);
    assert.equal(result.npm_config_userconfig, '/tmp/user-npmrc');
    assert.equal(result.npm_config_globalconfig, '/tmp/global-npmrc');
    assert.equal(result.npm_config_registry, 'https://registry.npmjs.org/');
    assert.equal(result.npm_config_audit, 'false');
    assert.equal(result.npm_config_fund, 'false');
    assert.equal(result.npm_config_update_notifier, 'false');
    assert.equal(result.npm_config_ignore_scripts, 'true');
  });
});

describe('pinnedElectronVersion and findInstalledElectron', () => {
  it('reads the exact pin and rejects ranges and unreadable manifests', () => {
    const desktop = makeDesktop();
    assert.equal(pinnedElectronVersion(desktop), VERSION);
    makeDesktop('^45.1.0');
    assert.throws(() => pinnedElectronVersion(desktop), /exact Electron version/);
    fs.rmSync(path.join(desktop, 'package.json'));
    assert.throws(() => pinnedElectronVersion(desktop), /cannot be read/);
  });

  it('finds the binary only for the pinned version with a downloaded executable', () => {
    const desktop = makeDesktop();
    assert.equal(findInstalledElectron(desktop, VERSION), null);
    const executable = installElectron(desktop);
    assert.equal(findInstalledElectron(desktop, VERSION), executable);
    assert.equal(findInstalledElectron(desktop, '45.2.0'), null);
  });

  it('treats a missing binary, missing path.txt and an escaping path as not installed', () => {
    const desktop = makeDesktop();
    installElectron(desktop, VERSION, { binary: false });
    assert.equal(findInstalledElectron(desktop, VERSION), null);
    fs.rmSync(path.join(desktop, 'node_modules'), { recursive: true });
    installElectron(desktop, VERSION, { pathTxt: null });
    assert.equal(findInstalledElectron(desktop, VERSION), null);
    fs.rmSync(path.join(desktop, 'node_modules'), { recursive: true });
    installElectron(desktop, VERSION, { pathTxt: '../package.json' });
    assert.equal(findInstalledElectron(desktop, VERSION), null);
  });
});

describe('ensureElectron', () => {
  it('returns the installed binary without running anything or printing', async () => {
    const desktop = makeDesktop();
    const executable = installElectron(desktop);
    const runner = async () => assert.fail('nothing should be installed');
    const messages = [];
    const result = await ensureElectron({
      desktopDirectory: desktop,
      env: {},
      log: (message) => messages.push(message),
      runner,
    });
    assert.equal(result, executable);
    assert.deepEqual(messages, []);
  });

  it('runs npm ci without scripts, then the Electron download, in desktop with isolated settings', async () => {
    const desktop = makeDesktop();
    const calls = [];
    const messages = [];
    const result = await ensureElectron({
      desktopDirectory: desktop,
      env: {
        PATH: '/bin',
        NPM_CONFIG_REGISTRY: 'https://private.example/',
        ELECTRON_MIRROR: 'https://m.example/',
      },
      platform: 'linux',
      log: (message) => messages.push(message),
      runner: async (call) => {
        const user = call.env.npm_config_userconfig;
        const global = call.env.npm_config_globalconfig;
        calls.push({
          ...call,
          distinct: user !== global,
          configExists: fs.existsSync(user) && fs.existsSync(global),
          configSize: fs.statSync(user).size + fs.statSync(global).size,
        });
        if (call.args.includes('ci')) {
          installElectron(desktop, VERSION, { binary: false });
        } else {
          fs.writeFileSync(path.join(desktop, 'node_modules', 'electron', 'dist', 'electron'), '');
        }
        return 0;
      },
    });
    assert.equal(result, path.join(desktop, 'node_modules', 'electron', 'dist', 'electron'));
    assert.equal(calls.length, 2);
    assert.equal(calls[0].command, 'npm');
    assert.deepEqual(calls[0].args, ['ci', '--ignore-scripts', '--no-audit', '--no-fund']);
    assert.equal(calls[1].command, process.execPath);
    assert.deepEqual(calls[1].args, [path.join('node_modules', 'electron', 'install.js')]);
    for (const call of calls) {
      assert.equal(call.cwd, desktop);
      assert.equal(call.env.NPM_CONFIG_REGISTRY, undefined);
      assert.equal(call.env.ELECTRON_MIRROR, undefined);
      assert.equal(call.env.npm_config_registry, 'https://registry.npmjs.org/');
      assert.equal(call.distinct, true);
      assert.equal(call.configExists, true);
      assert.equal(call.configSize, 0);
    }
    assert.equal(messages.length, 1);
    assert.match(messages[0], /First launch: installing Electron 45\.1\.0/);
    assert.match(messages[0], /registry\.npmjs\.org/);
    assert.match(messages[0], /GitHub/);
    // The temporary empty config file is removed afterwards.
    assert.equal(fs.existsSync(path.dirname(calls[0].env.npm_config_userconfig)), false);
  });

  it('reinstalls when the installed version differs from the pin', async () => {
    const desktop = makeDesktop('45.2.0');
    installElectron(desktop, '45.1.0');
    const steps = [];
    await ensureElectron({
      desktopDirectory: desktop,
      env: {},
      log: () => {},
      runner: async (call) => {
        steps.push(call.args[0]);
        installElectron(desktop, '45.2.0');
        return 0;
      },
    });
    assert.deepEqual(steps, ['ci', path.join('node_modules', 'electron', 'install.js')]);
  });

  it('stops at the first failing step and names it', async () => {
    const desktop = makeDesktop();
    const steps = [];
    await assert.rejects(
      ensureElectron({
        desktopDirectory: desktop,
        env: {},
        log: () => {},
        runner: async (call) => {
          steps.push(call.args[0]);
          return 1;
        },
      }),
      (error) => error instanceof LauncherError && /npm ci \(exit code 1\)/.test(error.message),
    );
    assert.deepEqual(steps, ['ci']);
  });

  it('fails when the binary is still missing after the commands succeed', async () => {
    const desktop = makeDesktop();
    await assert.rejects(
      ensureElectron({ desktopDirectory: desktop, env: {}, log: () => {}, runner: async () => 0 }),
      /still missing/,
    );
  });
});

describe('launchDesktopWindow', () => {
  /** Runs the launcher with stand-ins; returns the exit code, the log lines and the calls. */
  async function launch(overrides = {}) {
    const logs = [];
    const calls = { ensure: 0, shell: [] };
    const code = await launchDesktopWindow({
      projectDirectory: root,
      env: { DISPLAY: ':0' },
      platform: 'linux',
      nodeVersion: '24.22.1',
      log: (message) => logs.push(message),
      healthTimeoutMs: 3000,
      degradedGraceMs: 1000,
      ...fakeClock(),
      request: scriptedHealth('unreachable', 'ok').request,
      ensure: async () => {
        calls.ensure += 1;
        return '/electron';
      },
      shell: async (call) => {
        calls.shell.push(call);
        return 0;
      },
      ...overrides,
    });
    return { code, logs, calls };
  }

  it('waits for health, then opens the shell with the port from .env and exits 0 on close', async () => {
    fs.writeFileSync(path.join(root, '.env'), 'NARRASTEP_WEB_PORT=4777\n');
    const health = scriptedHealth('unreachable', 'ok');
    const { code, logs, calls } = await launch({ request: health.request });
    assert.equal(code, EXIT.OK);
    assert.deepEqual(health.calls, ['http://127.0.0.1:4777', 'http://127.0.0.1:4777']);
    assert.equal(calls.ensure, 1);
    assert.equal(calls.shell.length, 1);
    assert.equal(calls.shell[0].executable, '/electron');
    assert.equal(calls.shell[0].desktopDirectory, path.join(root, 'desktop'));
    assert.equal(calls.shell[0].env.NARRASTEP_WEB_PORT, '4777');
    assert.match(
      logs.join('\n'),
      /Waiting for Narrastep to become healthy at http:\/\/127\.0\.0\.1:4777/,
    );
    assert.match(logs.join('\n'), /Opening the Narrastep window on http:\/\/127\.0\.0\.1:4777/);
  });

  it('opens the window without waiting messages when the stack is already healthy', async () => {
    const { code, logs } = await launch({ request: scriptedHealth('ok').request });
    assert.equal(code, EXIT.OK);
    assert.equal(logs.length, 1);
    assert.match(logs[0], /http:\/\/127\.0\.0\.1:4200/);
  });

  it('does not install Electron before the stack is healthy', async () => {
    const order = [];
    const { code } = await launch({
      request: async () => {
        order.push('health');
        return order.length < 3 ? 'unreachable' : 'ok';
      },
      ensure: async () => {
        order.push('ensure');
        return '/electron';
      },
    });
    assert.equal(code, EXIT.OK);
    assert.deepEqual(order, ['health', 'health', 'health', 'ensure']);
  });

  it('reports an invalid port with exit code 2 before any health request', async () => {
    const health = scriptedHealth('ok');
    const { code, logs, calls } = await launch({
      env: { DISPLAY: ':0', NARRASTEP_WEB_PORT: 'abc' },
      request: health.request,
    });
    assert.equal(code, EXIT.CONFIGURATION);
    assert.equal(health.calls.length, 0);
    assert.equal(calls.shell.length, 0);
    assert.match(logs.join('\n'), /NARRASTEP_WEB_PORT must be a port number/);
  });

  it('maps exit code 2 of the shell to a configuration failure', async () => {
    const { code, logs } = await launch({ shell: async () => 2 });
    assert.equal(code, EXIT.CONFIGURATION);
    assert.match(logs.join('\n'), /refused its configuration/);
  });

  it('reports an old Node.js, a missing display and a timeout with the browser hint and exit 1', async () => {
    const old = await launch({ nodeVersion: '20.1.0' });
    assert.equal(old.code, EXIT.FAILED);
    assert.match(old.logs.join('\n'), /Node\.js 22\.12 or newer is required/);
    assert.match(old.logs.join('\n'), /--headless/);
    assert.match(old.logs.join('\n'), /http:\/\/127\.0\.0\.1:4200/);

    const headlessLinux = await launch({ env: {} });
    assert.equal(headlessLinux.code, EXIT.FAILED);
    assert.match(headlessLinux.logs.join('\n'), /No graphical session/);
    assert.equal(headlessLinux.calls.shell.length, 0);

    const stuck = await launch({ request: scriptedHealth('unreachable').request });
    assert.equal(stuck.code, EXIT.FAILED);
    assert.match(stuck.logs.join('\n'), /did not become healthy/);
    assert.equal(stuck.calls.ensure, 0);
  });

  it('reports a failed install and a shell that stops with an error', async () => {
    const install = await launch({
      ensure: async () => {
        throw new LauncherError('The desktop window install failed in npm ci (exit code 1).');
      },
    });
    assert.equal(install.code, EXIT.FAILED);
    assert.match(install.logs.join('\n'), /install failed/);
    assert.equal(install.calls.shell.length, 0);

    const crashed = await launch({ shell: async () => 1 });
    assert.equal(crashed.code, EXIT.FAILED);
    assert.match(crashed.logs.join('\n'), /exit code 1/);
    assert.match(crashed.logs.join('\n'), /sandbox/);

    const killed = await launch({ shell: async () => null });
    assert.equal(killed.code, EXIT.FAILED);
    assert.match(killed.logs.join('\n'), /a signal/);
  });

  it('opens the window with a note when the API stays degraded', async () => {
    const { code, logs } = await launch({ request: scriptedHealth('degraded').request });
    assert.equal(code, EXIT.OK);
    assert.match(logs.join('\n'), /the worker is not ready yet/);
  });

  it('lets an unexpected error propagate', async () => {
    await assert.rejects(
      launch({
        ensure: async () => {
          throw new TypeError('boom');
        },
      }),
      TypeError,
    );
  });
});
