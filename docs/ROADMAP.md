# Roadmap

Work packages in delivery order. An item is checked only when its exit checks pass, with dated
evidence below it. Planned items must not appear as enabled actions in the interface.

## P01 — Documentation baseline

**Depends on:** none. **Areas:** documentation, tooling.

- [x] P01.1 Rewrite the documentation with the `ajimu-conventions` structure: index, requirements,
  roadmap, risks and ADRs in one template.
- [x] P01.2 Record the new direction as ADRs 0009–0013.
- [x] P01.3 Owner accepts ADRs 0009–0013, including `llama.cpp` instead of Ollama.
- [x] P01.4 Record the bilingual interface (ADR 0015), the desktop shell proposal (ADR 0014) and
  the free-license rule with a license inventory.
- [ ] P01.5 Owner chooses the remaining improvement candidates.

Exit: `python scripts/check_docs.py` passes; every planned feature is labelled planned.

P01.1–P01.4 evidence (2026-10-28): `python scripts/check_docs.py` reports valid links. The owner
accepted ADRs 0009–0013 and asked for English and Italian, a pixel title font and free licenses
only. No code behavior changed, so no other check was run. P01.5 waits for the owner.

## P02 — Project types

**Depends on:** P01.3. **Areas:** backend, frontend, migration.
Decision: [ADR 0009](decisions/0009-project-types.md).

- [x] P02.1 Replace the modes with `task_briefing`, `meeting`, `tutorial`, `general`; migrate
  `technical_walkthrough` to `task_briefing`.
- [x] P02.2 Type-specific screenshot profile, export guide and evidence prompt.
- [x] P02.3 New-project form: four type cards, optional name with a default, "More options".

Exit: migration upgrade test on a Version 0.2 database; backend and frontend suites pass;
OpenAPI regenerated.

P02.1 evidence (2026-10-29): migration `20261029_0003` rebuilds `projects` with the new CHECK
constraint and maps `technical_walkthrough` to `task_briefing`.
`test_project_type_revision_migrates_version_0_2_modes` upgrades a seeded Version 0.2 database
(revision `20260720_0002`), checks the four values, child rows, `PRAGMA foreign_key_check`,
`alembic check` and the downgrade. `python -B -m pytest` in `backend`: 90 passed, 1 skipped;
`npm run test` in `apps/web`: 35 passed; `npm run lint` and `npm run build` passed;
`python scripts/export_openapi.py --check` reports the regenerated document current.

P02.2 evidence (2026-10-29): `backend/app/services/export_guides.py` renders
`llm/generation-prompt.md`, `chatgpt-batches/README.md` and the task block of every `batch-input.md`
from the project type (sections, screenshot guidance, shared and type rules). The per-type
screenshot profile existed since P02.1; `tests/test_export_guides.py` now checks that scan profile
and export budget agree for all four types, that each prompt lists its own sections in order, that
legacy and unknown modes fall back without failing, and that the ZIP contains the type's files.
`python -B -m pytest` in `backend`: 100 passed, 1 skipped; ruff format/check and mypy passed;
`npm run test` (35), `npm run lint`, `npm run build` and `npx prettier --check .` in `apps/web`
passed; `scripts/export_openapi.py --check` and `scripts/check_docs.py` passed. No API schema,
manifest or archive layout change. Not run: a local generation model (P06) or a real ChatGPT upload.

P02.3 evidence (2026-10-29): the new-project form (`apps/web/src/app/features/new-project`) shows the
four type cards first, then an optional name whose placeholder and help text show the default
`{Type} {YYYY-MM-DD}` (local date), and a native "More options" disclosure that holds the spoken
language and the description. An empty or blank name is replaced by the default when the project is
created; an invalid language or description opens the disclosure. No API or schema change.
`npm run test` in `apps/web` (Node 22.23.1): 38 passed (4 new or rewritten component tests);
`npm run lint`, `npm run build`, `npm run typecheck:e2e` and `npx prettier --check .` passed;
`git diff --check HEAD` passed. Not run: the Playwright journeys (`npm run e2e`,
`e2e:fullstack`), because no Chromium is installed on this worker; both specs were updated to
open "More options" before filling the language and description and were only type-checked.

## P03 — Descriptive export names

**Depends on:** P02.1. **Areas:** export, API.
Decision: [ADR 0013](decisions/0013-descriptive-export-names.md).

- [x] P03.1 Slug and file-name builder with edge-case tests.
  Evidence (2026-10-29): `backend/app/services/export_names.py` (`slugify_project_name`, `export_archive_name`, `export_zip_file_name`, `screenshot_file_name`) is pure and not yet wired into the export; `backend/tests/test_export_names.py` (32 cases: accents, emoji, empty, 200-character, Windows reserved names, path syntax, ADR pattern, screenshot names) passed; backend suite 100 passed plus the new tests, ruff, ruff format and mypy clean.
- [x] P03.2 New archive layout and manifest schema version.
  Evidence (2026-10-29): `build_export_package` writes one root folder named `{date}_{type}_{slug}_export-{nn}` (`ExportRequest.archive_name`, built in `perform_export` with the P03.1 builders) with `README.md`, `01-ai-input`, `02-screenshots/step-{nnn}_{mm}m{ss}s.png`, `03-transcript`, `04-chatgpt-batches/batch-{nn}-of-{total}` and `metadata/manifest.json`, `source.json`; manifest `schemaVersion` is `0.3.0` with `archiveName` and root-relative `paths`. New tests: layout and every path (`test_archive_has_one_named_root_and_numbered_layout`), default name, unsafe names, batch folder names, the root name and export number through `perform_export`, and a Version 0.2 ZIP that still downloads byte for byte. `python -B -m pytest` in `backend`: 145 passed, 1 skipped; ruff check/format, mypy, `scripts/export_openapi.py --check`, `scripts/check_docs.py`, `git diff --check HEAD` passed; `npm run test` (38), `npm run lint`, `npm run build`, `npm run typecheck:e2e` and `npx prettier --check .` in `apps/web` passed (Node v24.22.1 on this worker). Not run: the opt-in Playwright journeys (no Chromium here); the full-stack spec was updated to the new layout and only type-checked. The download file name is still `narrastep-{UUID}.zip` and the stored name and number are P03.3.
- [x] P03.3 Download uses the stored name; history shows it.
  Evidence (2026-10-29): migration `20261029_0004` adds `project_exports.sequence` (unique per project, backfilled by creation order) and `download_name`; `_queue_export` stores both via `assign_export_name`, the worker names the root folder after the stored name, `download_export` serves it in `Content-Disposition` and falls back to `narrastep-{id}.zip` for exports without one; export responses and `docs/openapi.json` gain `sequence` and `downloadName`; the export history card is titled with the name. New tests: `test_export_stores_its_number_and_name_and_download_uses_them` (name, numbers 1 and 2, rename after export, header, ZIP root folder), `test_worker_names_an_export_queued_without_a_stored_name`, the migration test, and the Version 0.2 download now also checks its header and history name. `python -B -m pytest` in `backend`: 148 passed, 1 skipped; ruff check/format, mypy, `scripts/export_openapi.py --check`, `scripts/check_docs.py`, `git diff --check HEAD` passed; `npm run test` (38), `npm run lint`, `npm run build`, `npm run typecheck:e2e`, `npx prettier --check .` in `apps/web` passed (Node v24.22.1 on this worker). Not run: the opt-in Playwright journeys (no Chromium here); the browser-contract and full-stack specs were updated and only type-checked. ADR 0013 amended: Version 0.2 exports keep their original download name.

Exit: export test checks the ZIP name and every path in the new layout; old exports still download.

## P04 — Pixel interface

**Depends on:** P02.3. **Areas:** frontend.
Decisions: [ADR 0012](decisions/0012-pixel-ui-design-system.md),
[ADR 0015](decisions/0015-english-and-italian-interface.md). Specification: [UI](UI.md).

- [ ] P04.1 Token file and non-color tokens; remove duplicated aliases.
- [ ] P04.2 Shared primitives, pixel icon sprite and bundled Pixelify Sans title font with its
  notice.
- [ ] P04.3 Shell with vertical menu; restyle every screen; rewrite copy.
- [ ] P04.4 English and Italian catalogs, language switch, error-code translations and the
  missing-message check.
- [ ] P04.5 Accessibility and responsive verification in both languages.

Exit: color check clean; contrast, keyboard, reduced motion, 320 px and 200% zoom verified in
English and Italian; no message ID missing in either language; browser-contract journey passes.

## P05 — In-app recording

**Depends on:** P04.2. **Areas:** frontend, API, media.
Decision: [ADR 0010](decisions/0010-in-browser-recording.md). Specification: [Recording](RECORDING.md).

- [ ] P05.1 Recording session API with per-track chunk storage, resume and discard.
- [ ] P05.2 Join, align, mix and validate into the source video; keep the microphone track.
- [ ] P05.3 Recorder screen with source checks, level gauge, pause, stop, and save with an
  optional name. Saving never starts processing (former I-12, changed by the owner).
- [ ] P05.4 Project type optional until processing; Process dialog asks for it.
- [ ] P05.5 Speaker labels You / Others from microphone and system loudness, editable in Review
  (I-03).
- [ ] P05.6 Manual test with a real Teams call on Windows, with a headset.

Exit: API tests, speaker-label tests and fake-device Playwright test pass; a saved recording has
no queued job; manual test recorded in [Status](STATUS.md).

## P06 — Optional local generation

**Depends on:** P02.2, P04.2. **Areas:** Docker, API, worker, frontend.
Decision: [ADR 0011](decisions/0011-optional-local-generation.md). Specification:
[Local generation](LOCAL_GENERATION.md).

- [ ] P06.1 Pinned `llama.cpp` CPU server in the backend image; measure the image growth.
- [ ] P06.2 Model manager: download, resume, verify, enable, delete, kill switch.
- [ ] P06.3 Generation job with type-specific prompts and chronological parts.
- [ ] P06.4 Settings and Document screens; optional inclusion in the package.
- [ ] P06.5 Benchmark time and peak memory on the owner's computer (10 and 60 minutes, E2B).
- [ ] P06.6 Clickable `[mm:ss]` references in Review and Document that move the video to that
  time (I-04).

Exit: unit and opt-in integration tests pass; benchmark recorded; the stack starts and works with
the feature not installed and with `NARRASTEP_LOCAL_GENERATION=disabled`.

## P07 — Desktop window

**Depends on:** P04.3. **Areas:** desktop shell, launcher.
Decision: [ADR 0014](decisions/0014-desktop-app-shell.md).

- [ ] P07.1 `desktop/` Electron shell with loopback-only loading, isolation and single instance.
- [ ] P07.2 `start.sh` opens the window after health; `--headless` keeps browser-only mode;
  first launch installs the pinned Electron into `desktop/node_modules`.
- [ ] P07.3 Screen and window source picker with Windows loopback system audio for P05.
- [ ] P07.4 Microphone permission and native "Save as" for downloads, loopback origin only.
- [ ] P07.5 Optional unsigned local portable build.

Exit: launcher, isolation and permission tests pass; smoke test opens the window on a healthy
stack; manual Windows check records one window with system audio; `--headless` still works.

## Improvement candidates

Proposed on 2026-10-28 (P01.5). **Recommended** items give the most value for their cost;
**Optional** items can wait.

Scheduled by the owner on 2026-10-28:

- I-03 separate microphone track and You / Others labels → P05.5;
- I-04 clickable `[mm:ss]` references → P06.6;
- I-12 changed: a recording is saved without processing → P05.3–P05.4;
- desktop app: Electron shell as in Symphony, PWA dropped → P07.

Waiting for the owner's choice:

| ID | Improvement | Why | Priority | After |
| --- | --- | --- | --- | --- |
| I-01 | `start.ps1` launcher for Windows PowerShell | `start.sh` needs Git Bash or WSL; PowerShell is always present | Recommended | P01 |
| I-02 | Glossary per project passed to Whisper as hints | Names, products and jargon are the most common transcription errors | Recommended | P02 |
| I-05 | Copy result as Markdown, single-file Markdown with images | Paste straight into Jira, Confluence, Teams or a ticket | Recommended | P06 |
| I-07 | Storage view and cleanup after export | Videos are large; free space without losing the package | Recommended | P03 |
| I-15 | Automated license inventory inside the containers | Keeps [Licenses](LICENSES.md) true as dependencies change | Recommended | P01 |
| I-13 | Editable prompt templates per project type | Adapt the task-briefing or meeting output to the owner's team format | Optional | P06 |
| I-06 | Search across all projects (SQLite FTS5) | Find which recording mentioned a topic | Optional | P04 |
| I-08 | Sensitive-data blur before export | Recordings show e-mails, names and tokens | Optional | P04 |
| I-09 | Optional larger Whisper model installed from Settings | Better accuracy for Italian; reuses the P06 model manager | Optional | P06 |
| I-10 | Project backup and restore archive | Move projects between computers | Optional | P03 |

## Later

- Promote `adaptive_v1` once every local benchmark gate passes; keep `legacy` for one release.
- Extend selective OCR only after a privacy review allows new persisted data.
- Screenshot crop, highlight and callout annotations.
- GPU configuration without changing CPU support.
