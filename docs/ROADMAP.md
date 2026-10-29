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
- [ ] P02.3 New-project form: four type cards, optional name with a default, "More options".

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

## P03 — Descriptive export names

**Depends on:** P02.1. **Areas:** export, API.
Decision: [ADR 0013](decisions/0013-descriptive-export-names.md).

- [ ] P03.1 Slug and file-name builder with edge-case tests.
- [ ] P03.2 New archive layout and manifest schema version.
- [ ] P03.3 Download uses the stored name; history shows it.

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
