# Development

## Docker setup

The primary workflow is:

```bash
./start.sh
```

Run it from a POSIX-compatible shell, such as Git Bash or WSL on Windows. The script runs the stack
in the foreground and builds its images. Use `./start.sh -r` to reuse existing images with Compose
`--no-build`; it intentionally fails when any required image is missing. Once the stack is healthy
the script also opens the desktop window ([Desktop window](#desktop-window)); `./start.sh
--headless` skips the window and keeps the browser-only mode used in CI and on computers without
Node.js. The first build downloads
dependencies and the configured Whisper model. Subsequent runtime processing is offline. The web
application is available at `127.0.0.1:4200`, the API at `127.0.0.1:8002`, and Redis is not
published.

### Podman

`./start-podman.sh` is the Podman counterpart ([ADR 0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md)).
It sources `scripts/start-common.sh` like `start.sh`, so `-r`, `--headless`, `-h`, the `.env` step
and the desktop window behave the same. It runs `podman compose up --build` (`--no-build` with
`-r`), falls back to `podman-compose` when `podman compose version` fails, and exits 1 with advice
when Podman or both providers are missing. It never uses `sudo` or `--privileged`. Replace
`docker compose` with `podman compose` in the commands below.

Delivered: the script, its stub tests (`desktop/test/start-podman-script.test.cjs`) and
engine-neutral Compose files and Dockerfiles. Not delivered: any run on real Podman (P08.12), so
`depends_on` health conditions, rootless volume ownership and `podman machine` on Windows are
unverified.

### Generated `.env`

When `.env` does not exist, both start scripts run `scripts/generate-env.sh --engine docker|podman`
and write the result only if it succeeds; an existing `.env`, even an empty one, is never changed
(delete it to regenerate). The values come from the CPU count, memory, free disk and free ports,
read from `docker info` or `podman info`. On macOS and Windows that memory is the limit of the
engine's virtual machine, not of the computer: raise it in the Docker Desktop settings (Resources)
or with `podman machine set --memory 6144`. The scripts only report a low limit; they do not change
the machine. If `podman info` fails, `start-podman.sh` tells you to run `podman machine start`.

Inspect local services with:

```bash
docker compose ps
docker compose logs api worker
```

Stop containers without deleting projects:

```bash
docker compose down
```

Delete all Narrastep volumes only after explicit confirmation:

```bash
docker compose down -v
```

## Native frontend

Use Node 26.13.0 from `.nvmrc`:

```bash
cd apps/web
npm ci
npm start
```

The Angular development proxy targets the API on port 8002.

## Native backend

Use Python 3.14 (the image uses 3.14.10) and create an isolated environment. After activation,
install the locked packages and then the project itself (see [Backend package lock](#backend-package-lock)):

```bash
cd backend
python -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements-dev.lock
python -m pip install --no-deps --no-build-isolation --no-index -e .
python scripts/download_model.py
python -m alembic upgrade head
python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --reload
```

Set `NARRASTEP_DATA_DIR` outside synchronized folders and provide Redis separately. Native
`adaptive_v1` processing with selective OCR also requires Tesseract plus English and the desired
detected-language packs. The supported image installs `eng`, `ita`, `deu`, `fra`, and `spa` during
build; Narrastep never downloads packs at runtime. Start the worker in another terminal:

```bash
cd backend
python -m celery -A app.tasks.celery_app:celery_app worker --loglevel=INFO --concurrency=1 --prefetch-multiplier=1
```

## Desktop window

`desktop/` is the Electron shell of [ADR 0014](decisions/0014-desktop-app-shell.md) (P07.1). It
uses the host Node.js 22.12 or newer and keeps Electron inside `desktop/node_modules`.

### Launcher (P07.2)

`./start.sh` starts the window like this:

1. `start.sh` checks Docker Compose, then, unless `--headless` is given, starts a background
   watcher and replaces itself with `docker compose up` (so Compose output and `Ctrl+C` behave as in
   headless mode). When `node` or `npm` is missing the watcher is not started and the script says
   so; the stack still runs.
2. The watcher runs `node desktop/launch.cjs` (`src/launcher.cjs`). The launcher checks Node.js
   22.12 or newer and, on Linux, a graphical session (`DISPLAY` or `WAYLAND_DISPLAY`); takes
   `NARRASTEP_WEB_PORT` from the environment or `.env` (the environment wins, as in Compose; an
   invalid value exits with code 2); and polls `http://127.0.0.1:{port}/api/v1/health` through the
   web container every second. It continues when the API reports `status: "ok"` (database, Redis and
   worker), or when it has reported `degraded` for 60 seconds, because the app shows what is missing.
   It gives up after one hour; the watcher also stops it when Compose has exited, for example after
   a failed build.
3. If `desktop/node_modules/electron` is missing or not the version pinned in `package.json` (first
   launch, or after a version bump), it prints what will be downloaded and runs, in `desktop/`,
   `npm ci --ignore-scripts --no-audit --no-fund` and then `node node_modules/electron/install.js`.
   These run with isolated npm settings: the user and global `.npmrc` files are replaced by empty
   ones, every `npm_config_*` variable and every `ELECTRON_*` variable (any case) is dropped except
   `ELECTRON_GET_USE_PROXY`, `ELECTRON_GET_NO_PROGRESS` and `ELECTRON_CONFIG_CACHE`, the registry is
   `https://registry.npmjs.org/`, and audit, fund and update checks are off. The dropped variables
   are the ones Electron's install script reads to change the mirror, folder, file name, version,
   platform, architecture or checksum source of the binary, or to skip it; the kept cache folder is
   safe because a cached file is checked against the same sums. `HTTP_PROXY`, `HTTPS_PROXY` and
   `NO_PROXY` are kept. On Windows `npm` is a `.cmd` file, so it runs through `cmd.exe` with one
   fixed command line.
   Requests: the locked packages from the npm registry and the Electron binary from GitHub releases,
   checked against the SHA-256 sums in the package's `checksums.json`; nothing else.
4. It runs the Electron binary on `desktop/` (without `ELECTRON_RUN_AS_NODE`) and exits when the
   window closes: code 0 for a normal close, 2 for an invalid port, 1 for every other problem. A
   problem never stops the stack; the launcher prints the cause and the browser address, and
   `./start.sh --headless` is the way to run without a window.

Closing the window leaves the containers running. The window never outlives the stack: when
Compose ends (`Ctrl+C` in the terminal, or `docker compose down` from another terminal), the
watcher stops the launcher within about two seconds, and a launcher that receives `SIGTERM`,
`SIGINT` or `SIGHUP` while the window is open asks the window to quit with `SIGTERM`, waits for it
and prints `The stack is stopping, so the Narrastep window was closed.` A launcher that is still
waiting for health or installing simply stops. This was checked on Linux; on Windows Node.js ends
the window process at once, and whether Git Bash delivers a signal the launcher can handle is not
checked yet.

To run the shell alone against an already running stack:

```bash
cd desktop
npm ci --ignore-scripts
node node_modules/electron/install.js
npm start
```

`npm ci --ignore-scripts` installs the pinned `electron` package without running any package
script; `install.js` then downloads the Electron 45.2.0 binary for this system from the Electron
releases on GitHub and checks it against the SHA-256 sums in the package's `checksums.json`. `npm start` opens one window on
`http://127.0.0.1:${NARRASTEP_WEB_PORT:-4200}`; a second `npm start` brings that window to the
front and exits. A port that is not a number from 1 to 65535 exits with code 2. Electron's own
`npm start` command downloads a missing binary by itself; running `install.js` first keeps that
download an explicit step.

| Module | Responsibility |
| --- | --- |
| `launch.cjs`, `src/launcher.cjs` | Host command started by `start.sh`: health wait, pinned first-launch install, runs the shell |
| `src/main.cjs` | Electron entry: starts the shell or prints the configuration error |
| `src/shell.cjs` | Single instance, sandbox, guards before the first load, quit when the window closes |
| `src/policy.cjs` | The loopback origin, URL, request and permission rules and the window's web preferences |
| `src/guards.cjs` | Applies the rules to the session and to every web contents |
| `src/window.cjs` | Window size, fallback page on failure, leave dialog, keyboard zoom |
| `src/fallback-page.cjs`, `src/messages.cjs` | The shell's own page and dialog texts in English and Italian |

Tests use the Node.js test runner and need no other package:

```bash
cd desktop
npm test
```

`test/policy.test.cjs`, `test/guards.test.cjs` and `test/shell.test.cjs` run the rules and the
lifecycle against in-memory Electron stand-ins (`test/support/fake-electron.cjs`).
`test/launcher.test.cjs` covers the launcher with injected stand-ins (`.env` port, health states,
timeout, isolated install environment, install commands on Windows and elsewhere, install steps
and failures, stop signals while the window is open, exit codes), a real local HTTP server for the
health request (including an oversized and a truncated answer), and a real launcher process that
receives `SIGTERM` and must close a stand-in window process. `test/start-script.test.cjs` runs
the real `start.sh` with a fake `docker` and a fake `desktop/launch.cjs` on a POSIX shell: that it
runs directly as `./start.sh` (Git keeps it executable), `--headless`, `-r`, `-h`, unknown options,
missing Compose, missing Node.js or npm, the Compose exit code, and the watcher stopping the
launcher when Compose exits. It creates its fakes in a git-ignored `desktop/test/.start-test-*`
folder because `/tmp` may be mounted `noexec`.
`test/electron-window.test.cjs` starts the real Electron with `test/support/electron-harness.cjs`,
which runs the production shell against two local HTTP servers: a stand-in app and a second
origin that must never receive a request. It checks the web preferences, that the page has no
`require`, `process`, `module` or `Buffer`, that popups, navigation, redirects, frames, webviews
and requests to another port or to `localhost` are blocked, that the microphone and notifications
are denied and full screen is allowed, that a screen capture request opens the source picker and
is rejected when the picker window is closed, the Italian fallback page, and that a
second launch exits while the first window loads the app again. Closing the window must end the
process with code 0.

These two window tests are opt-in, like the backend's real-model test, because the Electron binary
also needs the host's graphical libraries: a plain `npm test` skips them with a message. Install the
binary with `install.js`, then set `NARRASTEP_RUN_DESKTOP_WINDOW_TESTS=1`. Once requested, a missing
binary fails the suite instead of skipping it, and an Electron that cannot start fails the tests
with its error output. In PowerShell:

```powershell
$env:NARRASTEP_RUN_DESKTOP_WINDOW_TESTS = '1'
npm test
```

On POSIX shells, run `NARRASTEP_RUN_DESKTOP_WINDOW_TESTS=1 npm test`. On Linux the window tests use
Chromium's headless platform, so no display is needed, but Electron still needs the GTK 3, NSS and
X11 client libraries. Chromium's sandbox also needs unprivileged user namespaces or a root-owned
`chrome-sandbox`; in a container without either, also set `NARRASTEP_DESKTOP_TEST_NO_SANDBOX=1` to
add `--no-sandbox` to the test launches only. The shell itself never disables the sandbox. CI runs
`npm ci --ignore-scripts` and `npm test`, so it runs the stand-in tests and skips the window tests.

Formatting follows the web app's Prettier settings (`desktop/.prettierrc.json`) and uses its
Prettier from the repository root:

```bash
apps/web/node_modules/.bin/prettier --check "desktop/src/*.cjs" "desktop/test/**/*.cjs" desktop/package.json
```

### Generated `.env` in `start.sh` (P08.8)

After the Compose check and before the desktop watcher, `start.sh` runs
`scripts/generate-env.sh --engine docker` when `.env` does not exist. The output goes to a
temporary `.env.generating.<pid>` file that is moved to `.env` only on success; on failure the
temporary file is removed, `start.sh` prints a message on standard error pointing to
`.env.example` and exits 1 without starting Compose. An existing `.env`, including an empty one,
is never read or rewritten. Because the file exists before the launcher starts, the launcher reads
the generated `NARRASTEP_WEB_PORT` as for a hand-made file. `desktop/test/start-script.test.cjs`
covers this with a stub generator and a stub `docker` in a temporary copy of the project, never
the real `.env`. Not verified: a real Docker engine and macOS.

### Manual smoke test on Docker (P07.2)

Not yet run. The build worker cannot run Docker (no root, no capabilities, no user namespaces) and
has no display, so the owner runs this check on a computer with Docker Engine or Docker Desktop
and the Compose plugin (`docker compose version` succeeds). Podman is not part of this check for
now (owner decision, 2026-10-29). The computer also needs Node.js 22.12 or newer with npm, a
desktop session (on Linux, `DISPLAY` or `WAYLAND_DISPLAY` is set) and Internet access for step 4.
Record each step as passed or failed with the date and the output of `docker version`,
`docker compose version` and `node --version`. The address below uses the default port 4200; use
`NARRASTEP_WEB_PORT` from `.env` when it is set.

1. From the repository root run `./start.sh` (or `./start.sh -r` when the images are already
   built). Compose output stays in the terminal and the launcher prints `Narrastep: Waiting for
   Narrastep to become healthy at http://127.0.0.1:4200 ...`. No window opens before the line
   `Narrastep: Opening the Narrastep window on http://127.0.0.1:4200.`; then one window opens on
   **Projects** with **Services ready** in the header. A line ending in `the worker is not ready
   yet.` means the worker did not answer for 60 seconds; note it.
2. Close the window: `docker compose ps` in another terminal still lists the running services and
   a browser on `http://127.0.0.1:4200` still shows the app. Press `Ctrl+C` to stop the stack.
3. Run `./start.sh -r` again and wait for the window. Press `Ctrl+C` in the terminal: the stack
   stops, the terminal prints `Narrastep: The stack is stopping, so the Narrastep window was
   closed.` and the window closes.
4. Delete the `desktop/node_modules` folder and run `./start.sh -r`. After the waiting line the
   terminal prints `Narrastep: First launch: installing Electron 45.2.0 into
   desktop/node_modules. ...`, then npm's output and the binary download, and the window opens on
   **Projects**. Stop with `Ctrl+C` and run `./start.sh -r` once more: the window opens without the
   install message.
5. Run `./start.sh -r --headless`: the stack starts, no `Narrastep: Waiting` line is printed and
   no window opens, and a browser on `http://127.0.0.1:4200` shows **Projects**. Stop with
   `Ctrl+C`.
6. A window that does not open is a failed step, not a pass: copy the launcher's message. On Linux,
   an Electron that stops on Chromium's sandbox is risk K13 in [Risks](RISKS.md).
7. Record the result in [Status](STATUS.md) and as dated evidence under P07.2 in the
   [roadmap](ROADMAP.md), and only then check P07.2.

## Database migrations

Create a migration only after reviewing the generated operations:

```bash
cd backend
python -m alembic revision --autogenerate -m "describe the schema change"
python -m alembic upgrade head
```

Downgrades are development tools, not a substitute for a data migration strategy.

## Tests and checks

Frontend:

```bash
cd apps/web
npm run format:check
npm run lint
npm test -- --watch=false
npm run build
npm run check:i18n
npm run typecheck:e2e
npm run e2e
```

`npm run build` first runs `node scripts/check-i18n.mjs`, and `npm run check:i18n` adds a comparison
of `src/locale/messages.en.json` with a fresh `ng extract-i18n`. Both fail when a message ID is
missing in English or Italian, when a source uses an ID without a catalog entry, when a message has
no custom ID (`@@id`), an empty text or different placeholders in the two languages. After changing
or adding English text, run `npm run extract-i18n` (rewrites the English catalog) and write the
Italian text in `src/locale/messages.it.json` with the terms of `src/locale/GLOSSARY.md`; every new
interface text needs both languages in the same change.

The browser-contract journey runs on desktop Chromium and a mobile Chromium profile. It verifies
the complete local workflow on both profiles and checks the review workspace at 320, 390, 768,
1024, 1280, and 1440 pixel widths for horizontal overflow and the expected single-pane, stacked
editor/inspector, two-column tabbed-inspector, or three-pane arrangement. The same journey runs in
English and in Italian (browser locale `it-IT`), and a separate test switches the language with the
header buttons, checks that the choice survives a reload and that nothing overflows horizontally.
Unit tests run in English; Italian rendering is covered by `core/i18n/italian.spec.ts`, which loads
the Italian catalog for that file only.

`tests/e2e/accessibility.spec.ts` is the accessibility and responsive pass (P04.5). It uses
`tests/e2e/support/fake-api.ts` (one deterministic project per scenario, no backend) and
`tests/e2e/support/contrast.ts` (reads painted colors in the browser, applies the WCAG formulas in
Node) and covers every screen in English and Italian: text, placeholder and field-frame contrast,
Tab order with a visible-focus screenshot comparison, dialog focus handling, reduced motion, and
reflow at 320 to 1440 px and at 200% and 400% zoom. `npx playwright test tests/e2e/accessibility.spec.ts`
runs it alone; [UI](UI.md#verification) lists exactly what each check asserts and what it does not.
When a new screen or dialog is added, add it to `SCREENS` or to the dialog tests there.

`tests/e2e/recorder.spec.ts` drives the recorder with Chromium's fake devices
(`--use-fake-ui-for-media-stream`, `--use-fake-device-for-media-stream` and
`--auto-select-desktop-capture-source=Entire screen`, set with `test.use` in that file): a test
pattern with a beeping system-audio track as the screen, and a beeping microphone. Its recording
routes sit on top of `fake-api.ts`, keep the size, offset and first bytes of every chunk and
record any processing request. It checks the source checks and the live level gauge, chunk
numbering and the WebM header per track, pause, stop, save with a name, the project as
**Not processed** without a processing request, the leave confirmation, an interrupted recording
opened from Projects, and contrast and 320 px reflow of the recording and save windows in English
and Italian. Recorder unit tests use the doubles in `src/app/features/recorder/fake-media.testing.ts`.

The responsive Review Studio contract covers 320, 390, 768, 1024, 1280, and 1440 pixels. The
desktop editor width is stored only in browser local storage. Keyboard checks cover save, segment
navigation, native playback, two-second seeking, search focus, merge selection, exact-frame
recovery, and focused alternative activation. Non-save shortcuts are ignored while typing or while
a dialog owns focus.

Backend:

```bash
cd backend
python -m ruff format --check .
python -m ruff check .
python -m mypy app
python -m pytest
```

Repository contracts:

```bash
python scripts/check_docs.py
python scripts/export_openapi.py --check
docker compose config
docker compose build
```

Tests use temporary SQLite databases and workspaces. The deterministic mock pipeline must be
explicitly enabled and is never a production fallback. The real-model integration check is opt-in
because it requires FFmpeg/FFprobe, `espeak-ng` or `espeak`, and an already downloaded Whisper
model. From `backend`, run it in PowerShell with:

```powershell
$env:NARRASTEP_RUN_REAL_PIPELINE_TESTS = '1'
$env:NARRASTEP_REAL_WHISPER_MODEL_PATH = 'C:\path\to\local\whisper-model'
python -m pytest -m integration tests/integration/test_real_pipeline.py
```

On POSIX shells, set the same two variables before the command. The test synthesizes its speech and
video at runtime, exercises real transcription and frame extraction, and stores no binary fixture
in the repository.

The same two variables run the media matrix of P08.1,
`tests/integration/test_transcription_stack.py`: 16 generated clips (MP4, MOV, MKV and WebM, each
with mono audio at an unusual rate, 5.1, 7.1 at 96 kHz and silence) go through validation,
extraction and transcription with the real model. It needs FFmpeg with the `flite` source (Debian's
FFmpeg has it) but not `espeak-ng`, and takes a few seconds with `tiny` or `small`:

```bash
NARRASTEP_RUN_REAL_PIPELINE_TESTS=1 NARRASTEP_REAL_WHISPER_MODEL_PATH=/path/to/model \
  python -m pytest -m integration tests/integration/test_transcription_stack.py
```

The local generation job (P06.3e) has its own opt-in test,
`tests/integration/test_real_generation.py`, with its own variable, because it needs the pinned E2B
files (3.4 GB; the job's memory check asks for about 4.2 GiB free) and takes minutes on a CPU. The
model folder holds both files with their pinned names, and the test fails when a file is missing or
a hash differs. The server is `/opt/llama.cpp` in the backend image; elsewhere, point the last variable at a folder with
the pinned server files. Add `-s` to read the generated documents. Without the first variable pytest
deselects the two real-model tests (`tests/integration/conftest.py`) and runs the checks of a
document alone, so the summary counts them as deselected and not as skipped.

```bash
NARRASTEP_RUN_REAL_GENERATION_TESTS=1 NARRASTEP_REAL_GENERATION_MODEL_DIR=/path/to/gemma-4-e2b \
  NARRASTEP_REAL_LLAMA_SERVER_DIR=/path/to/llama.cpp \
  python -m pytest -m integration tests/integration/test_real_generation.py
```

`legacy` remains the default screenshot strategy. To exercise the experimental strategy locally,
set these values before queueing a new job (an already queued job keeps its immutable snapshot):

```text
NARRASTEP_SCREENSHOT_SELECTION_STRATEGY=adaptive_v1
NARRASTEP_OCR_MODE=selective
NARRASTEP_IMAGE_BUDGET_PROFILE=balanced
NARRASTEP_TESSERACT_PATH=tesseract
```

Diagnostic JSON files appear under `projects/{projectId}/diagnostics/`. They intentionally contain
only identifiers, counters, durations, sizes, versions, and terminal state. Do not add transcript,
OCR, filename, window-title, absolute-path, or image content to these files or stage logs.

## Transcription stack check

The backend image build runs `python scripts/check_transcription_stack.py` after it downloads the
Whisper model (P08.1, risk K15). The script prints the FFmpeg version and the versions of
faster-whisper, CTranslate2, PyAV, NumPy, ONNX Runtime, `tokenizers` and `huggingface_hub`, then
generates five clips (MP4, MOV, MKV, WebM and a silent MP4) and transcribes each with the baked
model as a processing job does. It fails the build, with the error chain that names the failing
package, when a step fails or a speech clip lacks the words "settings" and "save". It needs no
network and takes about 10 s with `small` on 4 CPUs. To run it in a built image or natively:

```bash
docker run --rm localhost/narrastep-backend:0.2.0 python scripts/check_transcription_stack.py
python scripts/check_transcription_stack.py --model-path /path/to/model --ffmpeg ffmpeg --ffprobe ffprobe
```

Run it after any change to a backend package, the Python version or the base image. A deliberately
broken set fails it: faster-whisper 1.2.1 with PyAV 19 and the code before P08.1 (`TypeError: open()
got an unexpected keyword argument 'metadata_errors'`), or ONNX Runtime 1.18.1, built for NumPy 1,
next to NumPy 2.4.6 (`Applying the VAD filter requires the onnxruntime package`).
`tests/test_transcription_stack_check.py` covers the script with stand-ins in the default suite.

## Full job check

`backend/scripts/full_job_check.py` proves a running stack end to end (P08.3). Using only the
standard library and FFmpeg, it requires health `ok`, generates a 10-second MP4 with speech, uploads
it, processes it with the baked model, exports it, checks the layout of the downloaded ZIP against
[Export layout](DATA_MODEL.md#export-layout) and deletes the project. It prints numbers, never
transcript text, and returns 1 with one line on stderr at the first failure. Mock processing fails
it, because the expected words are missing. To run it in the API container:

```bash
./scripts/ralph/stack.sh exec -T api python scripts/full_job_check.py --api http://127.0.0.1:8000/api/v1
```

`--ffmpeg` and `--timeout` change the FFmpeg path and the seconds each background step may take
(default 900). `tests/test_full_job_check.py` covers the script with a stand-in API.

## Backend package lock

Every backend package, direct and transitive, is pinned with its SHA-256 hashes (P08.2,
[ADR 0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md)):

| File | Contents | Installed by |
| --- | --- | --- |
| `backend/pyproject.toml` | The direct packages (`[project].dependencies`), the development tools (`dev` extra) and the `build` group (pip and the Hatchling build backend), each pinned with `==` | Read by `uv` to resolve the locks |
| `backend/requirements.lock` | The runtime packages plus the `build` group, resolved for Python 3.14 on every platform (`--universal`), with hashes for every published wheel | `backend/Dockerfile` |
| `backend/requirements-dev.lock` | The same versions plus the development tools | CI and native development |

The image installs the lock with `--require-hashes --no-deps --only-binary=:all:`, then the project
with `--no-deps --no-build-isolation --no-index`, and runs `pip check`, so it never resolves or
downloads a package that is not in the lock and never compiles one. `tests/test_dependencies.py`
fails when a lock entry has no hash, when `pyproject.toml` and the locks disagree, when the image
or CI installs anything else, when a locked package is a pre-release or missing from [Licenses](LICENSES.md#backend-python-packages), when the Python version differs between `pyproject.toml`, the
Dockerfile, CI and the locks, or when the environment running the tests is not the development lock.

To change a package, edit its pin in `pyproject.toml` and regenerate both locks with the pinned
`uv` from the development environment, from `backend/` (the runtime lock first, because the
development lock is constrained to it):

```bash
uv pip compile pyproject.toml --group pyproject.toml:build --universal --python-version 3.14 --generate-hashes -o requirements.lock
uv pip compile pyproject.toml --extra dev --group pyproject.toml:build --universal --python-version 3.14 --generate-hashes -c requirements.lock -o requirements-dev.lock
```

`uv` keeps the existing pins of transitive packages; add `--upgrade` to the first command (or
`--upgrade-package NAME`) to move them to the newest versions the direct pins allow. Then reinstall
the development environment, run the backend checks and `scripts/check_transcription_stack.py`, and
record new packages and licenses in [Licenses](LICENSES.md). Packages kept behind their newest
release, and why, are listed there too.

## Updating dependencies

Policy: every dependency has an exact version (no ranges, no pre-releases); every package has a
free, open-source-compatible license ([ADR 0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md)).
To repeat a refresh (the last one ran from P08.2 to P08.4, 2026-10-29 and 2026-10-30):

1. **Check the newest releases.** Python: `uv pip compile` with `--upgrade` (above) and PyPI.
   Web: `npm outdated` in `apps/web` and `desktop`. Images and Node.js: the registries and the
   Node.js release schedule (newest LTS).
2. **Change the pins.** Python in `backend/pyproject.toml`, then regenerate both locks (previous
   section). Web in `apps/web/package.json` and `desktop/package.json`, then `npm install` to
   update the lockfiles. Images in the Dockerfiles and Compose files, by exact tag and digest.
   Node.js in `.nvmrc`, `apps/web/.nvmrc`, `apps/web/Dockerfile`, `engines` and the CI jobs.
3. **Check licenses.** Read the license of every new or changed package and record version,
   license and reason in [Licenses](LICENSES.md); the table must match the installed versions.
   `backend/tests/test_dependencies.py` and `backend/tests/test_container_images.py` fail when
   a locked package or image is missing from it. With the images built, run the
   [license inventory](#license-inventory-of-the-backend-image) too.
4. **Run the checks:** `check.sh backend`, `web`, `desktop`, `docs`, then `stack` or
   `fullstack` and `scripts/check_transcription_stack.py` for images and the audio path.
5. **Record the result:** the roadmap evidence, `docs/STATUS.md`, `CHANGELOG.md` and risks
   [K9 and K15](RISKS.md).

What stays pinned behind its newest release, and why, is in the "kept behind" tables of
[Licenses](LICENSES.md): a package stays back when its newest stable release conflicts with a
dependent (redis-py with Kombu, TypeScript with Angular and `typescript-eslint`), when a
platform lacks wheels (Python 3.15 and ONNX Runtime), or when it is a pre-release or an
unversioned build (`llama.cpp` `server-b*` tags). Revisit each reason on every refresh and move
the pin when it no longer holds.

### License inventory of the backend image

`backend/scripts/license_inventory.py` (standard library only, no network request) starts the
built backend image, lists the Python distributions installed in it and the Debian packages that
`backend/Dockerfile` names in `apt-get install`, and compares them with
[Licenses](LICENSES.md). It exits 1 and names each difference when an installed Python package is
missing from the document or listed at another version, when a Dockerfile package is not installed
or not in the Debian table, or when a license is outside the free ones (rules in
[Licenses](LICENSES.md#rules-for-new-dependencies); copyleft only in the Debian table). It does
not list every transitive Debian package or the Alpine packages of the web and broker images:
those are covered by the pinned image entries.

```bash
./scripts/ralph/stack.sh build api
python backend/scripts/license_inventory.py --image narrastep-ralph-backend:dev
# or, for the owner's images: --image localhost/narrastep-backend:0.2.0 [--engine podman]
```

The host needs Python 3.14 or newer to run the script (it uses unparenthesised `except` lists).
`--image` pipes the script into `python -I - --list` inside the image, so it needs no copy of the
script there and no repository path. `--list` prints the listing as JSON from inside any image,
and `--installed FILE` compares a saved listing, which is what
`backend/tests/test_license_inventory.py` does with fixed listings and edited copies of the
document; one test runs `python -I - --list` on standard input. The tests do not start a
container; `--image` is not part of `check.sh`, because it needs a built image and a container
engine on the host. The Debian check confirms that each package named in the Dockerfile is
installed and documented with a free license; it does not recheck each license text. The host-side
`--image` run against a real image is recorded in the roadmap evidence of P08.5 only as far as it
was executed.

## Bundled llama.cpp server

The backend image copies the CPU `llama.cpp` server into `/opt/llama.cpp` from the official image
pinned by digest (P06.1, [Local generation](LOCAL_GENERATION.md#server-in-the-backend-image)). The
build runs `python scripts/check_llama_server.py`, which fails when the server cannot load its
libraries or is not the pinned build. To check a built image and its size:

```bash
docker compose build api
docker run --rm localhost/narrastep-backend:0.2.0 python scripts/check_llama_server.py
docker history localhost/narrastep-backend:0.2.0
```

The `COPY /opt/llama.cpp/` line of `docker history` is the size the server adds. A native backend
has no `/opt/llama.cpp`; `tests/test_llama_server.py` covers the check with a stand-in script and
needs neither the binary nor Docker.

## Adaptive promotion benchmark

Copy [the local annotation example](benchmark-dataset.example.json), replace its placeholder paths
and annotations, and include cases for static screens, non-consecutive returns, localized text
changes, dropdowns/dialogs, brief toasts, scrolling, long silence, and UI changes without speech.
Then run the real selectors and optional real Tesseract entirely on the local machine:

```bash
cd backend
python scripts/benchmark_screenshot_pipeline.py ../path/to/local-benchmark.json
```

The JSON report contains only aggregate numeric results and sets `promoteAdaptiveByDefault` to true
only when every recall, missed-state, redundancy, persisted-PNG, batch-copy, CPU-time, and RAM gate
passes. Do not commit private videos, transcripts, OCR output, or machine-specific benchmark paths.

## Full-stack browser journey

The normal `npm run e2e` command is a deterministic UI contract suite with intercepted API
responses. The opt-in full-stack suite sends every request to Nginx, FastAPI, SQLite, Redis, and the
Celery worker. It generates an eleven-second synthetic MP4 with an audio track at test time, then
verifies creation, upload, processing, review editing, split, merge, screenshot replacement and
removal, export, download, and ZIP contents. No binary fixture is stored in the repository.

The suite uses host FFmpeg when it is on `PATH`; otherwise, it runs FFmpeg from the backend image
after Compose has built that image. Start an isolated test stack from the repository root in
PowerShell:

```powershell
Copy-Item .env.example .env
$env:NARRASTEP_ENV = 'test'
$env:NARRASTEP_ENABLE_MOCK_PROCESSING = 'true'
$env:NARRASTEP_E2E_MOCK_CONFIRMED = 'true'
$env:NARRASTEP_MIN_FREE_DISK_MB = '64'
docker compose --project-name narrastep-fullstack up --build --detach
docker compose --project-name narrastep-fullstack ps
```

Wait until the API reports SQLite, Redis, and the worker as `ok`:

```powershell
do {
  Start-Sleep -Seconds 2
  $health = Invoke-RestMethod http://127.0.0.1:8002/api/v1/health
} until ($health.database -eq 'ok' -and $health.redis -eq 'ok' -and $health.worker -eq 'ok')
```

Then run:

```powershell
Set-Location apps/web
npm ci
npx playwright install chromium
npm run e2e:fullstack
Set-Location ../..
```

The environment confirmation variable prevents accidentally running deterministic mock processing
against an ordinary local stack. The test also checks the real API health endpoint before creating
data. When finished, remove only this isolated stack and its test volumes:

```powershell
docker compose --project-name narrastep-fullstack down --volumes --remove-orphans
```

CI runs the same path with the `tiny` model baked into the image, despite mock transcription, so
the built image retains the offline model contract. Review and export screenshots plus the ZIP are
retained as Playwright artifacts for diagnosis and documentation review.

## Autonomous runner

`scripts/ralph/` can complete roadmap tasks without supervision, one fresh Claude Code process per
task, and commits and pushes each verified task on `main`. Its [guide](../scripts/ralph/README.md)
has every command, and [ADR 0019](decisions/0019-autonomous-development-runner.md) the limits. Two of
its scripts are useful on their own, because they need nothing on the computer except Docker:

```bash
./scripts/ralph/check.sh docs backend web desktop   # the project's checks, in a container
./scripts/ralph/sandbox.sh 'cd backend && python -m pytest tests/test_storage.py'
```

When a roadmap item is added, add its task to `scripts/ralph/prd.json`;
`node scripts/ralph/prd.cjs validate` fails otherwise, and so do the runner's tests.

The owner now runs development with Ajimu Autopilot, a local tool outside this repository. Its plan is
`scripts/ralph/prd.json`, and its configuration and task records in `.ajimu-autopilot/` stay on the
owner's computer and out of Git.

## Local agent skills

The owner keeps agent skills in `.claude/skills/`, recorded in `skills-lock.json`. Both paths are
Git-ignored and are not part of the repository; [CLAUDE.md](../CLAUDE.md) and these documents
restate the rules that matter. With the skills present, restore or update them with
`npx skills experimental_install` or `npx skills update -p`. The color check in [UI](UI.md) and
the commit-message check in [CLAUDE.md](../CLAUDE.md) run only where the skills exist.

| Skill | Use |
| --- | --- |
| `ajimu-conventions` | Every change: contracts in comments, docs and roadmap in the same change, commit messages |
| `ajimu-pixel-ui` | Interface work; includes `check_colors.py` and `pixel_icons.py` |
| `angular-developer` | Angular components, signals, forms, routing, testing |
| `vitest` | Frontend unit tests |
| `playwright-best-practices` | Browser-contract and full-stack journeys, fake media devices |
| `fastapi` | API routes, Pydantic models, SSE |
| `python-testing-patterns` | Pytest suites and fixtures |
| `sqlalchemy-alembic-expert-best-practices-code-review` | Models and migrations |
| `docker-patterns` | Compose services, images, networks |
| `ffmpeg` | Remuxing recordings, audio extraction |
| `gemma-dev` | Gemma 4 prompts and model behavior |
| `prompt-engineering-patterns` | Type-specific generation prompts |

`scripts/check_docs.py` ignores `.claude/` because skill documents are maintained upstream.

## Pixel icons

Icons are 16×16 `#` and `.` grids in `apps/web/icons/`; the committed sprite
`apps/web/public/icons/sprite.svg` is generated with `pixel_icons.py` from the `ajimu-pixel-ui`
skill ([UI](UI.md#icon-sprite) lists the command). Regenerate it in the same change as any grid.
`npm run test` fails when the grids, the icon name list and the sprite disagree.

## Formatting and debugging

Use Prettier/ESLint for frontend code and Ruff for Python. Avoid formatter commands that include unrelated user changes. Structured logs include request, project, and job identifiers but intentionally omit transcript content.

For a failed background job, inspect the persisted job response first, then worker logs. For SSE issues, confirm the polling endpoint returns newer `version` values and verify that reverse-proxy buffering is disabled.

## Resetting development data

Docker data is reset with `docker compose down -v`. Native data is reset by stopping API/worker processes and removing only the configured `NARRASTEP_DATA_DIR`. Verify the resolved path before deletion; never target the repository root or a computed parent directory.
