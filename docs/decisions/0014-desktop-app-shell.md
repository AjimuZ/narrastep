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

- Electron is pinned to 45.1.0 (MIT, released 2026-10-27, `engines` Node.js 22.12 or newer) in
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
  in Review can fill the screen; it exposes no host data. Every other permission and every
  screen-capture request is denied, and HID, serial and USB devices are refused, until P07.3 and
  P07.4 open what they need.
- The spell checker is off, because Chromium would download its dictionaries.
- Windows and Linux have no menu bar; the shell handles Ctrl `+`, `-` and `0` itself so browser
  zoom stays available. macOS keeps Electron's default menu for the system editing shortcuts.
- When the page's `beforeunload` asks to stay (recording, uploading, unsaved edits), a native
  dialog in English or Italian asks, because Electron would otherwise ignore the close silently.
- When the app does not answer, the window shows a local page in English or Italian with a link
  that tries again; launching the shell again also tries again.
- Downloads keep Electron's default native save dialog until P07.4 restricts them to the app
  origin.
- On Linux, Chromium's sandbox needs unprivileged user namespaces or a root-owned `chrome-sandbox`
  helper. The window tests ran on a build worker without either, with `--no-sandbox` added by the
  test only; the shell never disables the sandbox.
