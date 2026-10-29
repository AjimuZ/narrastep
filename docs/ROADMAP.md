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

- [x] P04.1 Token file and non-color tokens; remove duplicated aliases.
  Evidence (2026-10-29): `apps/web/src/styles/tokens.scss` (loaded by `styles.scss` with `@use`) holds every palette value, the roles of [UI](UI.md#tokens) and the non-color tokens (`--px`, `--radius`, `--quick`, `--font-ui`, `--font-mono`, `--font-pixel`, text and spacing scales); all `--dk-*` and the duplicated `--color-surface*`, `--color-*-text`, `--font-sans`, `--motion-fast` and `--motion-slow` aliases were removed and every component stylesheet and inline style migrated (no `--dk-` left in `src` or in the built CSS); the 64 rgba and hex literals outside the token file became `color-mix()` of tokens. `check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss` (from the pixel-ui skill, run from `/app/skills`): "clean: no color literals outside token definitions" (it reported 45 outside `styles.scss` before). Every `var(--x)` in `src` resolves to a defined token. `npm run build`, `npm run test` (38), `npm run lint`, `npm run typecheck:e2e` and `npx prettier --check .` in `apps/web` passed. Not run: Playwright journeys (no Chromium) and a visual comparison; corners are now 2 px, the shadows and gradients remain until P04.3.
- [x] P04.2 Shared primitives, pixel icon sprite and bundled Pixelify Sans title font with its
  notice.
  Evidence (2026-10-29): shared primitives in `apps/web/src/app/shared/ui/` (window, tabs, segmented gauge, status pill, activity line, pixel icon; `index.ts`) with button, field and menu-entry classes and all primitive styles in `apps/web/src/styles/primitives.scss`, tokens only; 21 icons as 16×16 grids in `apps/web/icons/` and the sprite `apps/web/public/icons/sprite.svg` generated by `pixel_icons.py` from the pixel-ui skill ("wrote 21 icons"); the unmodified Pixelify Sans variable TTF at `apps/web/public/fonts/PixelifySans.ttf` with `OFL.txt` beside it, declared by `@font-face` in `styles.scss` (built CSS and `dist/.../fonts` contain it), and `THIRD_PARTY_NOTICES.md` records version 1.000, SHA-256, copyright and license. `ui.spec.ts` (11 tests: window naming, icon `use` href and `aria-hidden`, status word, gauge fill and rounding, activity line without a value, tab keyboard model, grid/name/sprite agreement, font hash against the notice, license text) passed. `npm run build`, `npm run test` (49), `npm run lint`, `npm run typecheck:e2e` and `npx prettier --check .` in `apps/web` passed; `check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`: "clean: no color literals outside token definitions"; backend `python -B -m pytest` passed; `scripts/check_docs.py` and `git diff --check HEAD` passed. Deviation: the font is bundled as the unmodified TTF instead of a WOFF2 subset (no font tooling on the worker, and the license asks for an unmodified file); the upstream commit could not be verified offline, so the notice relies on the version and hash. Not run: a browser render of the icons and the font (no Chromium) and the Playwright journeys; no screen uses the primitives until P04.3.
- [x] P04.3 Shell with vertical menu; restyle every screen; rewrite copy.
  Evidence (2026-10-29): shell in `apps/web/src/app/app.component.ts` (product name, health pill from `GET /api/v1/health` at start and every 30 s, vertical menu Projects and New project with `aria-current`, top bar below 768 px); Projects, New project, Processing, Review, Export, Not found and the confirm, split and image-preview dialogs use only the P04.2 primitives plus the helper classes in `primitives.scss` (form rows, notices, small buttons, striped placeholder, `.ns-row`); the Version 0.2 classes (`.panel`, `.button*`, `.field*`, `.notice*`, `.status-badge`, `.skeleton`, `.eyebrow`, `.icon`), the page gradients, shadows, blur and the shadow tokens are gone (a class sweep of `src/app` finds none). State words come from `shared/status-labels.ts`, type labels from `shared/project-types.ts`; the gauge gained an unknown (striped) state and a danger tone; copy rewritten to the [UI](UI.md#copy-rules) rules (fixed terms, verb buttons, "package"). `npm run test` in `apps/web`: 66 passed (new: shell spec, split-dialog spec, unknown gauge, type filter, processing gauges, new-project steps, export unknown estimate); `npm run build`, `npm run lint`, `npm run typecheck:e2e`, `npx prettier --check .` passed; `check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`: "clean: no color literals outside token definitions"; `npx playwright test` (browser-contract journey, desktop and mobile Chromium 149 headless shell from a worker-local toolchain, including the 320–1440 px review layout matrix and no horizontal overflow): 2 passed; screenshots of every screen at 1440, 1280 and 375 px checked by eye (Pixelify Sans and the sprite icons render). Backend `python -B -m pytest`: 148 passed, 1 skipped; `scripts/check_docs.py` and `git diff --check HEAD` passed. Not run: the opt-in full-stack journey (no Docker on this worker; its selectors were updated and type-checked), 200% zoom, contrast and keyboard passes (P04.5). Italian copy, the language switch, Record and Settings are not part of this item.
- [x] P04.4 English and Italian catalogs, language switch, error-code translations and the
  missing-message check.
  Evidence (2026-10-29): `@angular/localize` 22.0.6 added (MIT, recorded in `docs/LICENSES.md`); catalogs `apps/web/src/locale/messages.en.json` and `messages.it.json` hold 382 messages with custom IDs, `GLOSSARY.md` fixes the terms, and `core/i18n/` holds the startup loader (`init.ts`, first import of `main.ts`, `$localize` runtime as a build polyfill), the stored/browser language choice, `LanguageService` (stores `narrastep.language`, reloads, runs the unsaved-edits check that Review registers), `format.ts` (sizes, decimals and spoken-language names by locale) and `error-text.ts` (one Italian message per stable API error code; English keeps the API message, unknown codes fall back to it). Every screen, dialog, state word, project type and processing setting word in `apps/web/src/app` is a message; the header has the `EN` / `IT` switch with `aria-current`. `node apps/web/scripts/check-i18n.mjs` (run by `npm run build`) and `npm run check:i18n` (adds a comparison with `ng extract-i18n`) fail when an ID is in one language only, a source uses an ID missing from a catalog, a message has no custom ID or an empty text, or placeholders differ: "i18n check passed: 382 messages in en and it". New tests: catalog parity and error-code coverage, language detection and switch (including the refusal path), Italian rendering of the shell, state words, errors and sizes, and Review's unsaved-edits check before a switch (`npm run test` in `apps/web`: 84 passed, was 66); the browser-contract journey now runs in English and Italian (Chromium and mobile Chromium) plus a language-switch test with a reload: `npx playwright test`, 6 passed, no horizontal overflow from 320 to 1440 px in both languages. Italian screens at 1280, 375 and 320 px reviewed by eye; two layout fixes came out of it (the header scrolls away below 768 px instead of covering content, and the Review view switcher keeps "Trascrizione" whole at 320 px). `npm run build`, `npm run lint`, `npm run typecheck:e2e`, `npx prettier --check .` passed; `check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`: "clean: no color literals outside token definitions"; backend `python -B -m pytest`: 148 passed, 1 skipped; `scripts/check_docs.py` and `git diff --check HEAD` passed. Not run: the opt-in full-stack journey (no Docker on this worker; it runs in English and was only type-checked), 200% zoom, contrast and keyboard passes in both languages (P04.5), the switch in Settings (P06.4), and the export package README in the interface language (ADR 0015; belongs with the export text work, not this item). ADR 0015 amended.
- [x] P04.5 Accessibility and responsive verification in both languages.
  Evidence (2026-10-29): new `apps/web/tests/e2e/accessibility.spec.ts` with `support/fake-api.ts` and `support/contrast.ts` runs all seven screens (Projects, New project, Processing, Processing failed, Review, Export, Not found) and the confirm, split and screenshot-preview dialogs in English and Italian: text, placeholder, hover-state, field-frame and focus-ring contrast (WCAG formulas on painted colors); Tab order with every visible control reached once, an accessible name, not covered, and a screenshot comparison proving each focus is visible; dialog focus trap, focus return and Escape; radio groups with arrow keys; `prefers-reduced-motion` (no motion left, and the check fails when the processing screen is allowed to animate); no horizontal scroll, cut-off control or clipped text at 320, 390, 768, 1024 and 1440 px, at 200% zoom (640 px at scale 2) and 400% zoom (320 px at scale 4); 24 × 24 px targets at 390 px and below; every media and container query in rem. Negative controls (injected low-contrast color, removed focus ring, 900 px wide body) made the matching checks fail. Real defects found and fixed: danger button text 3.58:1 (now on the deep surface, elevated on hover), placeholder text 3.24:1, current wizard step number 3.59:1, field frames 1.48:1 and project-type radio frames 1.40:1 (new `--color-field-border` token, 3:1), project-type radios had no shared `name` (each was its own Tab stop with no arrow keys), `aria-modal` was false on every dialog (`DEFAULT_DIALOG_CONFIG` now sets it), confirm and split dialogs ignored Escape, "More options" was 21 px high. `npx playwright test`: 314 passed (308 accessibility checks across desktop and mobile Chromium, 6 contract tests), twice in a row; `npm run test` in `apps/web`: 87 passed (was 84; new confirm-dialog spec and a split-dialog Escape test); `npm run build` (i18n check: "382 messages in en and it"), `npm run check:i18n`, `npm run lint`, `npm run typecheck:e2e`, `npx prettier --check .` passed; `check_colors.py apps/web/src --tokens apps/web/src/styles/tokens.scss`: "clean: no color literals outside token definitions"; backend `python -B -m pytest`: 148 passed, 1 skipped; `scripts/check_docs.py` and `git diff --check HEAD` passed. Limits: headless Chromium here ignores the default-font-size preference, so a 200% font-size setting is covered by the rem breakpoint guard plus the 640 px case; no screen reader was used; the opt-in full-stack journey was not run (no Docker); button frames are not measured for 3:1 because their text identifies them.

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
