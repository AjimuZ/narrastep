# Requirements

## Purpose

Narrastep turns a screen recording with spoken explanation into a reviewed transcript, the
screenshots that matter and a package that produces a written document. Everything runs on the
user's computer.

## Situations

| Situation | Example | Project type |
| --- | --- | --- |
| Someone explains a task or a problem the user must solve | A colleague shows a failing report and what it should do | Task briefing |
| A meeting the user must summarize | A Teams call with decisions and follow-ups | Meeting |
| A procedure to repeat or teach | How to configure a release in a tool | Tutorial |
| Anything else | A demo, a walkthrough, a recorded note | Generic |

## Functional requirements

Status: **Implemented** in Version 0.2, or **Planned** with its roadmap package.

| ID | Requirement | Status |
| --- | --- | --- |
| R01 | Create, list, search, edit and delete local projects | Implemented |
| R02 | Choose one of four project types (task briefing, meeting, tutorial, generic) at creation or, at the latest, when processing starts | Planned (P02, P05.4) |
| R03 | Upload one MP4, WebM, MOV or MKV video per project, validated with FFprobe | Implemented |
| R04 | Record screen, system audio and microphone in the browser; saving stores the project with an optional name and never starts processing | Planned (P05) |
| R04a | Label transcript segments of in-app recordings as You or Others, editable in review | Planned (P05.5) |
| R05 | Transcribe locally with word timestamps, without network access | Implemented |
| R06 | Select, deduplicate and associate screenshots with transcript segments | Implemented |
| R07 | Review and edit the transcript, split and merge segments, change screenshots | Implemented |
| R08 | Show real processing progress that survives a browser refresh | Implemented |
| R09 | Export an immutable ZIP with transcript, screenshots, prompts and upload batches | Implemented |
| R10 | Give exports and their folders descriptive names | Planned (P03) |
| R11 | Install, enable, disable and delete an optional local multimodal model from the interface | Planned (P06) |
| R12 | Generate the type-specific document locally from transcript and screenshots | Planned (P06) |
| R13 | Present every screen with the pixel design system and plain text | Planned (P04) |
| R12a | Open the video at the time of any `[mm:ss]` reference in review and in the generated document | Planned (P06.6) |
| R14 | Switch the interface between English and Italian at any time | Planned (P04) |
| R15 | `start.sh` opens Narrastep in its own desktop window; `--headless` keeps browser-only use | Planned (P07, [ADR 0014](decisions/0014-desktop-app-shell.md)) |

## Constraints

- **Local-first.** No account, telemetry, analytics, cloud storage or remote inference. The only
  allowed network request after setup is a model download started by the user
  ([ADR 0007](decisions/0007-local-first-privacy.md), [ADR 0011](decisions/0011-optional-local-generation.md)).
- **No host installs.** The user may be unable to install programs. Docker is required; host
  Node.js with npm, already present, is used only to open the desktop window, whose Electron
  runtime is downloaded into the project folder. Every other runtime dependency runs in a
  container.
- **Optional extras stay optional.** The default stack works without the generation model; a
  setting can remove the feature entirely.
- **Single user, loopback only.** Services listen on `127.0.0.1`.
- **Languages.** Code and documents are in English. The interface is in English and Italian
  ([ADR 0015](decisions/0015-english-and-italian-interface.md)). Transcripts keep their spoken
  language.
- **Free licenses only.** The project is public on GitHub and non-commercial. Every dependency,
  model, font and tool is free and allows public redistribution; no paid license, service or
  certificate ([Licenses](LICENSES.md)).
- **CPU is enough.** Every feature works without a GPU; slower is acceptable, missing is not.
- **Honest data.** The interface never shows invented values; unknown values are labelled.

## Out of scope

- Multi-user access, authentication and remote hosting.
- Video editing beyond choosing evidence frames.
- Sending data to external AI services. The user may still upload the exported package to one
  manually.

## Assumptions and open questions

Assumptions (owner hardware, browser) and open questions are tracked in [Risks](RISKS.md).
