# Changelog

All notable changes to Narrastep are documented in this file.

## Unreleased

### Added

- Initial Narrastep 0.2 local-first web application implementation.
- Project, upload, processing, review, split/merge, screenshot association, and export workflows.
- Docker Compose development stack, automated tests, CI, and project documentation.
- Opt-in full-stack Playwright coverage using generated legal media, the real local stack, and ZIP content verification.
- Chronological, self-contained ChatGPT upload folders for long exports, with one evidence input and up to 19 images per batch by default.
- Experimental CPU-compatible `adaptive_v1` screenshot selection with change windows, transcript-unit fusion, global medoid clustering, selective local OCR, and mode-aware image budgets.
- Project-local numeric diagnostics, configuration snapshots, WebP review thumbnails, lazy full-PNG materialization, exact-frame recovery, and export estimates.
- Searchable and sortable project library, processing stage timeline, Review Studio filters and shortcuts, persisted desktop pane sizing, and actual export metrics.
- Foreground `start.sh` launcher with an explicit `-r` mode that reuses images without building.

### Changed

- The interface styles now read every color, font, space and corner from one token file
  (`apps/web/src/styles/tokens.scss`) instead of two overlapping alias sets. Corners are now
  squarer (2 px) and the UI font no longer asks for Inter; colors and layout are otherwise the
  same.
- Export packages now use a numbered layout inside one root folder named after the date, project
  type, project name and export number (for example
  `2026-10-28_meeting_sprint-planning_export-02`): `README.md`, `01-ai-input`, `02-screenshots`
  (`step-003_00m42s.png`), `03-transcript`, `04-chatgpt-batches/batch-01-of-03` and `metadata`.
  The manifest moved to `metadata/manifest.json` with schema version 0.3.0. Earlier exports keep
  their layout.
- Each export now has a number and a descriptive ZIP name, fixed when it is queued. The file you
  download and the export history use that name (for example
  `2026-10-28_meeting_sprint-planning_export-02.zip`); renaming the project later does not rename
  existing exports. Exports made before this change still download as `narrastep-{id}.zip`.

- The new-project form now starts with the four project-type cards. The project name is optional
  and defaults to the type and the local date (for example "Meeting 2026-10-29"); the spoken
  language and the description moved under "More options".
- The export package now follows the project type: `llm/generation-prompt.md`, the ChatGPT batch
  guide and each `batch-input.md` name the type's document sections (for example Decisions and
  Action items for a meeting, numbered steps for a tutorial) and give type-specific screenshot
  guidance. Screenshot density per type is unchanged and now covered by a consistency test.
- Projects now have one of four types: Task briefing, Meeting, Tutorial or Generic. Existing
  Technical walkthrough projects become Task briefings when the database is upgraded and keep their
  screenshot density and image budget; the API no longer accepts `technical_walkthrough`.
- Reorganized the documentation: index, requirements, roadmap with exit checks, risks, and ADRs in
  one template. Added specifications for the planned project types, descriptive export names,
  pixel interface, in-browser recording and optional local generation. Accepted the English and
  Italian interface, the Pixelify Sans title font and a free-licenses-only rule with a license
  inventory. Planned recording now saves a project for later processing, labels speakers as You
  or Others, and the planned review and document screens get clickable `[mm:ss]` references.
  Narrastep is planned to open in an Electron desktop window from `start.sh`, with `--headless`
  for browser use. These features are not available yet.
- Replaced the Codex instruction file `AGENTS.md` with `CLAUDE.md`; the project no longer uses
  Codex.
- Exported every distinct primary screenshot, reused repeated visual evidence, and preserved every explicit review selection.
- Redesigned the complete dark interface with clearer hierarchy, accessible interaction states, responsive application layouts, and dedicated mobile review views.
- Refined the interface visuals within the existing palette: layered panel and button depth, consistent inline SVG icons replacing ad-hoc glyphs, status badges color-coded for every project state, project-row and history-card hover elevation, animated progress and processing-timeline cues, a brand-accented header mark and favicon, and app-wide custom scrollbars and text selection. All motion respects reduced-motion preferences.
- Reduced screenshot alternatives from four to two and introduced semantic design tokens with temporary compatibility aliases.
- Kept `legacy` as the screenshot-selection default while `adaptive_v1` completes its local benchmark gate.

### Fixed

- Kept active export progress visible after refresh and kept processing cancellation available after queueing.
- Improved keyboard focus, contextual control labels, review-operation locking, language entry, and live-region behavior across the Angular workflow.
- Ensured the production CSS loads under the container Content Security Policy, restoring button and link styling in Docker Compose.

## 0.2.0

Release preparation is pending final verification. This section will be finalized when every documented acceptance command passes.
