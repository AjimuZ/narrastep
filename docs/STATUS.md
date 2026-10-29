# Implementation status

This matrix describes what exists in the code today (Version 0.2). A capability is
**Implemented** only when code and automated tests exist; **Verified** adds a recorded manual or
environment check. **Planned** names its [roadmap](ROADMAP.md) package.

## Version 0.2

| Capability | Status | Notes |
| --- | --- | --- |
| Project CRUD and persistence | Implemented | SQLite metadata and UUID workspaces |
| Confirmed project deletion | Implemented | Busy projects are protected |
| Video upload and validation | Implemented | MP4, WebM, MOV, MKV; FFprobe required |
| Byte-range video playback | Implemented | Single byte ranges for HTML5 seeking |
| Local transcription | Implemented | faster-whisper, CPU/int8, model baked into the image |
| Legacy screenshot extraction | Implemented | Default; temporal anchors, quality score, consecutive pHash deduplication |
| Adaptive screenshot selection | Experimental | `adaptive_v1`; not the default until the benchmark gate passes |
| Late PNG materialization | Implemented | WebP review thumbnails; PNG on demand or at export |
| Content-free diagnostics | Implemented | Numeric SQLite summaries and project-local JSON |
| Durable processing progress | Implemented | SQLite, SSE and polling fallback |
| Cooperative cancellation and retry | Implemented | Checked between bounded units |
| Transcript review | Implemented | Raw evidence preserved |
| Segment split and merge | Implemented | Archived sources and lineage |
| Screenshot reassignment and recovery | Implemented | Primary and alternatives; exact-frame recovery |
| ZIP export | Implemented | Numbered archive layout with a root folder named `{date}_{type}_{slug}_export-{nn}`, manifest schema 0.3.0 (P03.2); the file on disk stays `narrastep-{UUID}.zip`. The number and ZIP name are stored when the export is queued (P03.3, migration `20261029_0004`) and the download and history use them; exports without a stored name download as `narrastep-{UUID}.zip` |
| Project types | Implemented | `task_briefing`, `meeting`, `tutorial`, `general` (P02.1); migration `20261029_0003` maps `technical_walkthrough` to `task_briefing`. Type-specific screenshot profile, export guide, generation prompt and batch evidence prompts (P02.2); new-project form with type cards, optional default name and "More options" (P02.3). The type is optional until processing: a recording has none, `POST /projects/{id}/process` takes `{mode?, sourceLanguage?}` and answers `409 PROJECT_TYPE_REQUIRED` without a type, and the Process dialog asks for it (P05.4, migration `20261029_0006`) |
| Export name builders | Implemented | Slug, archive and screenshot names (P03.1); used by the archive layout (P03.2) and the stored download name (P03.3) |
| Design token file | Implemented | `apps/web/src/styles/tokens.scss` holds every color and the non-color scales; the `--dk-*` aliases are gone and the color check reports no literal outside it (P04.1). The shadow tokens left with the restyle (P04.3) |
| Shared pixel primitives, icon sprite and Pixelify Sans | Implemented | Window, tabs, gauge, status pill, activity line and icon components in `apps/web/src/app/shared/ui/`, button, field and menu-entry classes in `styles/primitives.scss`, 21 icons in a 16×16 sprite, and the unmodified Pixelify Sans TTF with its notice in `THIRD_PARTY_NOTICES.md` (P04.2). Every screen uses them since P04.3; the font and icons were seen rendering in headless Chromium on 2026-10-29 |
| Pixel shell and restyled screens | Implemented | Header with the product name and a service health pill, vertical menu (Projects, Record since P05.3, New project) that becomes a top bar below 768 px, and every screen and dialog on the shared primitives with rewritten English copy (P04.3). The browser-contract journey passed in Chromium on 2026-10-29. Settings is not there yet |
| English and Italian interface | Implemented | `EN` / `IT` switch in the header that stores the choice and reloads (after the unsaved-edits confirmation in Review); catalogs `messages.en.json` and `messages.it.json` with 485 messages and custom IDs (382 at P04.4, plus two recording error texts in P05.1, one in P05.2, the recorder screen in P05.3, the Process dialog in P05.4 and the Review speaker select in P05.5); first visit follows an Italian browser; dates, numbers, sizes and spoken-language names follow the language; stable API error codes are translated; `npm run build` and `npm run check:i18n` fail on a message missing in either language (P04.4). The switch in Settings arrives with P06.4; the accessibility pass in both languages is in the row below (P04.5) |
| Recording session API | Implemented | `POST/GET/DELETE /api/v1/recordings`, chunk `PUT` per `screen` and `microphone` track with SHA-256 repeat check, gap report for resume, start offsets, chunk and session limits, startup cleanup of incomplete writes, discard with the project (P05.1, migration `20261029_0005`); `capture` settings in session responses (P05.3) |
| Recording completion into the source video | Implemented | `POST /api/v1/recordings/{id}/complete`: gap check (or `stopAtGap`), chunks read in place through FFmpeg `concatf`, microphone aligned by the start offsets and added to the system audio, video copied, FFprobe validation as for uploads, `source/microphone.webm` kept on the same timeline (and `source/system.webm` with system audio since P05.5), chunks deleted, project `uploaded` with no job (P05.2). Tested with FFmpeg 7.1 on generated WebM tracks on 2026-10-29, and with recordings from headless Chromium's fake devices through the recorder (P05.3); not yet with a real call (P05.6) |
| Recorder screen | Implemented | `/record` and **Record** in the menu and on Projects: screen, system-audio and microphone checks with a live level gauge, consent and headset notices, two `MediaRecorder` tracks uploading chunks with retries, pause, resume, stop, save with an optional name or discard; saving never starts processing; open sessions show as Recording or Recording interrupted in Projects and open in the recorder to save what was recorded (P05.3). Fake-device Playwright tests pass in desktop and mobile Chromium (2026-10-29); a real-API run with fake devices saved 10 s and 60 s recordings as Not processed with no job. Not yet tested on Windows with a real call and headset (P05.6) or in the desktop window (P07) |
| You / Others speaker labels | Implemented | Recordings with a microphone and system audio keep `source/system.webm` beside `source/microphone.webm`; after transcription the worker compares the two tracks' RMS level per segment (You at ≥10 dB louder microphone, Others at ≥10 dB louder system audio, no label when closer or below −50 dBFS), stored with `speakerSource: automatic` (migration `20261029_0007`). `PATCH /segments/{id}` sets or clears `speakerLabel` (`speakerSource: user`); the Review **Speaker** select saves it at once; labels appear in the transcript files and the AI input of the package (P05.5). Tested on synthetic Opus tracks with FFmpeg 7.1 and through a recorded, saved and processed project on 2026-10-29; not yet with a real call (P05.6). Uploaded videos and recordings saved before P05.5 get no automatic labels |
| Docker local stack | Implemented | Web, API, single worker, Redis, named volumes |
| Desktop shell (`desktop/`) | Implemented | Electron 45.1.0 window, opened by `start.sh` once the stack is healthy (P07.2) or started alone with `npm --prefix desktop start`. Loads only `http://127.0.0.1:{NARRASTEP_WEB_PORT}` and cancels every other request, navigation, redirect, popup and webview; context isolation, renderer sandbox, no Node.js, no preload; only full screen is granted, microphone and screen capture are denied until P07.3–P07.4; one instance, a second launch focuses the window; closing it leaves Docker alone; English and Italian fallback page and leave dialog; keyboard zoom (P07.1). 76 desktop tests by default and 2 opt-in window tests in the real Electron passed on 2026-10-29, the window tests on Linux headless with `--no-sandbox` (no user namespaces on the worker); the real web build rendered in the window against a stub API. Launcher (P07.2, 2026-10-29): `start.sh` starts it in the background while Compose stays in the foreground; it waits for `/api/v1/health`, installs the pinned Electron on the first launch with isolated npm settings (run for real in a clean copy: `npm ci` 13 packages, then the binary) and opens the window; `--headless` skips all of it; a missing Node.js, npm, display or Internet leaves the stack running with a message; when the stack stops, the window closes too. Smoke-tested on 2026-10-29 with the real `start.sh`, launcher and Electron against the real API, worker and web build behind a stand-in for Docker Compose, nginx and Redis (first launch from the npm registry and GitHub, window on Projects, `Ctrl+C`, Compose exit, `--headless`); not yet with the Docker stack, on Windows or macOS |
| `llama.cpp` CPU server in the backend image | Implemented, build not yet run | `/opt/llama.cpp` holds `llama-server` v0.5.0 (build b11146) and its libraries from the official image pinned by digest; the image build runs `scripts/check_llama_server.py` (`llama-server --version`, pinned build and commit). Nothing starts the server yet (P06.3). Growth measured on 2026-10-29 from the extracted files: 40.1 MB uncompressed, 16.5 MB compressed on amd64; 28.0 MB on arm64. The libraries and symbol versions were checked against the Debian 13 libraries of `python:3.12.13-slim` without running the binary; `docker compose build` and the in-image check were not run (no Docker on the worker), so P06.1 stays open (P06.1) |
| Automated unit, API and UI tests | Implemented | Last recorded floor (2026-10-29, P06.1): 220 backend passed, 1 opt-in integration skipped with FFmpeg on `PATH` (without it 210 passed and the 7 completion and 3 speaker-label media tests skip too); 161 frontend passed; 408 Playwright tests passed (P05.5, not re-run in P06.1); 127 desktop tests passed by default (76 shell, 39 launcher, 12 `start.sh`; P07.2) plus 2 opt-in window tests in the real Electron (P07.1) |
| Browser contract journey | Implemented | Desktop and mobile Chromium, six responsive widths, in English and in Italian, plus a language switch test; last passed 2026-10-29 (P04.4) |
| Accessibility and responsive check in both languages | Verified (2026-10-29) | `apps/web/tests/e2e/accessibility.spec.ts`: contrast, keyboard and visible focus, dialogs, reduced motion, 320 to 1440 px, 200% and 400% zoom on all screens in English and Italian; 308 checks (154 per Chromium project) pass with the 6 contract tests (P04.5); with the recorder screen added (P05.3) 348 checks (174 per Chromium project). Not run: a browser with a 200% default font size (headless Chromium ignores the setting), screen readers, and the opt-in full-stack journey |
| Full-stack browser journey | Implemented | Opt-in; real mock-enabled Compose stack; inspects the ZIP |
| Docker Compose smoke test | Verified (2026-08-05) | Images build; health, CSS/CSP rendering and full-stack export passed |
| Selective OCR comparison | Experimental | Tokens stay in memory; only numeric scores persist |
| Adaptive benchmark gate | Pending verification | `legacy` stays default |
| Portable `project.json` snapshot | Not implemented | SQLite is authoritative; no import consumer exists |

## Planned

| Capability | Status | Package |
| --- | --- | --- |
| Language switch in Settings | Planned | P06.4 |
| Real Teams call with a headset on Windows | Planned | P05.6 |
| Desktop window smoke test on the Docker stack, run by the owner ([procedure](DEVELOPMENT.md#manual-smoke-test-on-docker-p072)) | Planned | P07.2 |
| Optional local Gemma 4 generation | Planned | P06 |
| Clickable `[mm:ss]` references in Review and Document | Planned | P06.6 |
| Source picker, microphone and native "Save as" in the desktop window | Planned | P07.3–P07.4 |
| Other improvements (launcher for PowerShell, glossary, masking, search and more) | Candidate | [Roadmap](ROADMAP.md#improvement-candidates) |

## Last documentation check

2026-10-29 (P07.1): `python scripts/check_docs.py` reported valid links after the desktop shell
documentation; the backend, web and desktop suites passed in the same change (see the P07.1
evidence in the [roadmap](ROADMAP.md)).

2026-10-28: `python scripts/check_docs.py` passed after the documentation rewrite. The test
suites were not re-run because no application code changed.
