# ADR 0009: Four purpose-based project types

Status: accepted (2026-10-28). Supersedes: the `mode` values of Version 0.2.

## Context

Version 0.2 asks for a `mode` (`tutorial`, `meeting`, `technical_walkthrough`, `general`) whose
difference from the user's point of view is unclear. The owner records three recurring situations
and asked to keep tutorials:

1. someone explains a task or a problem that the user must solve later;
2. a meeting whose decisions and key points must be summarized;
3. a step-by-step tutorial;
4. anything else.

## Decision

Replace the four modes with four project types that describe the purpose of the recording:

| Type | Value | Result the package is built for |
| --- | --- | --- |
| Task briefing | `task_briefing` | Problem statement, context, involved screens, expected result, open questions |
| Meeting | `meeting` | Summary, decisions, action items, open questions |
| Tutorial | `tutorial` | Numbered procedure with one screenshot per step |
| Generic | `general` | Structured summary and timeline |

- The type drives screenshot density, the export guide and the generation prompt.
- An Alembic migration maps `technical_walkthrough` to `task_briefing`. Other values keep their
  meaning. `task_briefing` inherits the `technical_walkthrough` sampling and budget profile.
- The API keeps the field name `mode` for compatibility and documents the new values.
- The type may stay empty until processing starts, because an in-app recording is saved with a
  name only ([ADR 0010](0010-in-browser-recording.md)). Starting processing requires a type; it can
  be changed until then. Upload-created projects still choose it in the new-project form.

## Alternatives

- Keep the four Version 0.2 modes: rejected; `technical_walkthrough` and `tutorial` overlap and
  neither describes the owner's main case, a task explanation.
- Free-text purpose: rejected; it cannot select prompts and budgets reliably.

## Consequences

- Existing projects keep working after the migration; their exports are not rewritten.
- Each type needs its own prompt and export guide; see [Local generation](../LOCAL_GENERATION.md).
- Evidence: migration upgrade test, API schema test and UI test for the four choices (P02 in the
  [roadmap](../ROADMAP.md)).
