# ADR 0014: Electron desktop shell

Status: accepted (2026-10-28). Supersedes: none.

## Context

The owner wants Narrastep to open as a desktop application, the same way the owner's Symphony
project already does: `start.sh` starts the Docker services and then opens an Electron window on
the local web app. The owner cannot install programs, but host Node.js and npm are already present
(Node.js 24.22.1 and npm 11.19.1 on 2026-10-28), and Symphony's launcher works with them.

The backend (Python, FFmpeg, Whisper, `llama.cpp`) stays in Docker; a native Windows window cannot
be provided by a Linux container.

## Decision

- Add a `desktop/` folder with a small Electron shell, following Symphony's launcher pattern:
  - `start.sh` starts Compose, waits for real health, then runs `node desktop/launch.cjs`;
  - on first launch the launcher runs `npm ci --ignore-scripts` with isolated npm settings and
    the pinned `electron` version, inside `desktop/node_modules`; nothing is installed system-wide
    and no administrator right is needed;
  - `./start.sh --headless` keeps the Docker-only mode, used with a normal browser and in CI.
- The shell loads only `http://127.0.0.1:{NARRASTEP_WEB_PORT}`. It enables context isolation and
  the renderer sandbox, disables Node integration, denies navigation to other origins, popups,
  webviews and every permission except the ones below. It has no updater and no telemetry.
- Allowed host capabilities, for the loopback origin only:
  - **microphone** (`getUserMedia`) for recording;
  - **screen capture** (`getDisplayMedia`) through `session.setDisplayMediaRequestHandler`,
    with a Narrastep source picker (screens and windows from `desktopCapturer`) and, on Windows,
    `audio: 'loopback'` so system audio is captured for any chosen source, including one window;
  - **downloads** of export ZIPs and generated documents, saved with a native "Save as" dialog.
- Single instance: launching again focuses the open window. Closing the window quits the shell
  and never stops the containers or a running job.
- A local portable build (`npm --prefix desktop run package`, using `@electron/packager`) is
  optional and unsigned; it is not published as an installer.

## Alternatives

- **Installable web app (PWA):** no Node.js needed, but no source picker, no system audio for a
  single window and no native save dialog; the owner chose Electron.
- **Signed installer:** needs a paid certificate, excluded by [Licenses](../LICENSES.md).
- **Tauri:** smaller, but uses the system WebView with different capture support and adds a Rust
  toolchain; Symphony already proves the Electron pattern on this computer.
- **Browser tab only:** remains available through `--headless`.

## Consequences

- [ADR 0010](0010-in-browser-recording.md): in the desktop window, system audio works for a whole
  screen and for a single window; in a browser the browser limits still apply.
- Host requirement: Node.js 22.12 or newer with npm, only for the desktop window. The first launch
  needs Internet access to the npm registry.
- Evidence: launcher unit tests (modes, Node version, missing npm), URL and permission isolation
  tests, a smoke test that opens the window against a healthy stack, and a manual Windows check of
  recording with system audio from a single window (P07 in the [roadmap](../ROADMAP.md)).

## Implementation notes (P07.1, 2026-10-29)

The shell in `desktop/` applies this decision with these details:

- Electron is pinned to 45.2.0 (MIT, the newest stable release on 2026-10-30, `engines` Node.js 22.12 or newer) in
  `desktop/package.json` and `desktop/package-lock.json`.
- The origin is `http://127.0.0.1:{NARRASTEP_WEB_PORT}`, 4200 when the variable is unset or
  empty. `localhost` is never used. A port that is not a number from 1 to 65535 stops the shell
  with exit code 2 before anything opens.
- Loopback-only applies to every request, not only to navigation: the session cancels every
  request whose origin is not the app origin (inline `data:` excepted), so the page cannot reach
  another server, another local port or `localhost`.
- The page gets no host interface: no preload script and no IPC channel. P07.3 and P07.4 add the
  picker, the microphone and "Save as" in the main process.
- `fullscreen` is the one permission granted, to the app origin only, so the native video controls
  in Review can fill the screen; it exposes no host data. Every other permission is denied, and
  HID, serial and USB devices are refused, until P07.4 opens what it needs.
- Screen capture (P07.3a) is answered by `setDisplayMediaRequestHandler`: a request whose security
  origin and frame are the app origin opens the Narrastep picker, every other request is denied.
  The picker is a modal window with a script-free local page that follows the fallback page rules.
  Without IPC, its links point to a reserved `.invalid` host and the main process reads the cancelled
  navigation; the answer is an index into the list the main process holds, never an id from a URL.
  Windows loopback system audio (P07.3b): when the shell runs on `win32` and the page asked for
  audio, the grant is `{ video: source, audio: 'loopback' }` for a screen and for a single
  window; on any other platform it is `{ video: source }`. The shell passes `process.platform`
  to the handler, so tests cover all platforms with stand-ins. Because the page has no bridge,
  the recorder tells the desktop window and its system apart from the user agent (`Electron/`
  token and `Windows`); this adds no channel and changes no permission. Loopback records every
  application, so on Windows the recorder shows the notification sound notice (risk K12), and on
  other systems it says the window shares no system audio.
- The spell checker is off, because Chromium would download its dictionaries.
- Windows and Linux have no menu bar; the shell handles Ctrl `+`, `-` and `0` itself so browser
  zoom stays available. macOS keeps Electron's default menu for the system editing shortcuts.
- When the page's `beforeunload` asks to stay (recording, uploading, unsaved edits), a native
  dialog in English or Italian asks, because Electron would otherwise ignore the close silently.
- When the app does not answer, the window shows a local page in English or Italian with a link
  that tries again; launching the shell again also tries again.
- Downloads (P07.4b, 2026-10-31): the session's `will-download` handler keeps a download only when
  its URL (a `blob:` URL is read through the origin after the prefix) and the page that started it
  are the app origin. It sets no save path, so Electron opens the native "Save as" dialog, and asks
  it to confirm before overwriting. Every other download, including a `data:` URL or an unreadable
  item, is cancelled before a dialog opens. The request filter already cancels foreign requests, so
  this is a second layer. Unit tests use stand-ins; the real dialog was not run.
- On Linux, Chromium's sandbox needs unprivileged user namespaces or a root-owned `chrome-sandbox`
  helper. The window tests ran on a build worker without either, with `--no-sandbox` added by the
  test only; the shell never disables the sandbox.

## Implementation notes (P07.2, 2026-10-29)

`start.sh` and `desktop/launch.cjs` apply the launcher part of this decision:

- `start.sh` keeps Compose as its foreground process (`exec docker compose up`) and, unless
  `--headless` is given, starts a background watcher first. This keeps log output and `Ctrl+C`
  identical to the headless mode. The watcher stops the launcher when Compose exits. Git tracks
  `start.sh` as executable, so `./start.sh` works on a fresh Linux or macOS clone.
- "Real health" is `GET http://127.0.0.1:{NARRASTEP_WEB_PORT}/api/v1/health` through the `web`
  container, with `status: "ok"` (database, Redis and worker). `degraded` is accepted after 60
  seconds so the window can show what is missing; one hour without an answer stops the launcher.
  The check uses the host Node.js, so no `curl` is required.
- `NARRASTEP_WEB_PORT` comes from the environment, then from `.env`, then 4200, the order Compose
  uses to publish the port. The launcher passes the result to the shell.
- First-launch install: `npm ci --ignore-scripts --no-audit --no-fund` and then Electron's
  `install.js` in `desktop/`, when the installed `electron` is not exactly the pinned version or has
  no downloaded binary. The environment is isolated: empty user and global npm configuration files
  (npm rejects one file for both), no `npm_config_*` variables and no `ELECTRON_*` variables except
  `ELECTRON_GET_USE_PROXY`, `ELECTRON_GET_NO_PROGRESS` and the cache folder `ELECTRON_CONFIG_CACHE`
  (cached files are checked against the pinned sums), the public npm registry, audit, fund and
  update checks off, proxy variables kept. The launcher announces the
  download before it starts. A custom registry or mirror is deliberately not supported, so a
  configuration file or variable cannot redirect where executable code is fetched from.
- Failures (Node.js too old, no `npm`, no display on Linux, no Internet, a shell that exits with an
  error, such as Chromium's sandbox on some Linux systems) leave the stack running; the message
  names the browser address and `./start.sh --headless`. Exit code 2 of the shell means an invalid
  port.
- Closing the window ends only the launcher. The window never outlives the stack: when Compose
  ends, the watcher sends `SIGTERM` to the launcher, and a launcher that receives `SIGTERM`,
  `SIGINT` (`Ctrl+C`) or `SIGHUP` while the window is open asks the shell to quit with `SIGTERM`
  and waits for it. Before this, a window left behind by a stopped stack kept showing the last
  service state ("Services ready") with nothing behind it. A launcher still waiting for health or
  installing simply stops.
- Not covered: Windows PowerShell (candidate I-01), a Docker-based smoke test on this build worker
  (no Docker), and the Windows and macOS behavior of the launcher.
