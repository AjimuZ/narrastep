# ADR 0013: Descriptive export names

Status: accepted (2026-10-28). Supersedes: none.

## Context

A Version 0.2 export downloads as `narrastep-{UUID}.zip` and contains generic folders (`llm`,
`metadata`, `screenshots`). Several downloaded packages cannot be told apart, and the folder names
do not say which file to open first.

## Decision

- **ZIP name:** `{date}_{type}_{project-slug}_export-{nn}.zip`, for example
  `2026-10-28_meeting_sprint-planning_export-02.zip`.
  - `date` is the export creation date (`YYYY-MM-DD`, UTC).
  - `type` is the project type value from [ADR 0009](0009-project-types.md).
  - `project-slug` is the project name transliterated to ASCII, lowercase, hyphen-separated and
    cut to 48 characters; it falls back to `project`.
  - `nn` is the per-project export sequence number.
- **Archive layout:** one root folder with the same name as the ZIP, then numbered folders in
  reading order, described in [Data model](../DATA_MODEL.md#export-layout).
- **Screenshots:** `step-{nnn}_{mm}m{ss}s.png`, so the file name gives the step and video time.
- The name is computed once at export creation and stored with the export record. Workspace
  storage keeps UUID paths ([ADR 0005](0005-filesystem-media-storage.md)).
- Version 0.2 archives are not rewritten. They keep the download name they always had,
  `narrastep-{UUID}.zip` (amended in P03.3: their creation-time name was never stored and a
  new-pattern name would promise a root folder their content does not have); their content keeps
  the old layout.

## Alternatives

- Use the project name only: rejected; repeated exports of one project would collide.
- Rename workspace folders too: rejected; it reintroduces collision and path-safety risks.

## Consequences

- `manifest.json` gets a new schema version (`0.3.0`, implemented in P03.2) and the export guide
  lists the new paths. The manifest moves to `metadata/manifest.json` and its `paths` are relative
  to the archive root folder.
- Evidence: unit tests for slug edge cases (accents, emoji, empty, very long, reserved Windows
  names) and an export test that checks the ZIP name and layout (P03 in the
  [roadmap](../ROADMAP.md)).
