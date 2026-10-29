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

Use Node 22.23.1 from `.nvmrc`:

```bash
cd apps/web
npm ci
npm start
```

The Angular development proxy targets the API on port 8002.

## Native backend

Use Python 3.12 and create an isolated environment. After activation:

```bash
cd backend
python -m pip install -e ".[dev]"
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
script; `install.js` then downloads the Electron 45.1.0 binary for this system from the Electron
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
and requests to another port or to `localhost` are blocked, that the microphone, screen capture
and notifications are denied and full screen is allowed, the Italian fallback page, and that a
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

## Bundled llama.cpp server

The backend image copies the CPU `llama.cpp` server into `/opt/llama.cpp` from the official image
pinned by digest (P06.1, [Local generation](LOCAL_GENERATION.md#server-in-the-backend-image)). The
build runs `python scripts/check_llama_server.py`, which fails when the server cannot load its
libraries or is not the pinned build. To check a built image and its size:

```bash
docker compose build api
docker run --rm narrastep-backend:0.2.0 python scripts/check_llama_server.py
docker history narrastep-backend:0.2.0
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
