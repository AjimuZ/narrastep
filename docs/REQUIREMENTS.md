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

Status: **Implemented** in Version 0.2, **Partial** when only the named package is delivered, or
**Planned** with its roadmap package.

| ID | Requirement | Status |
| --- | --- | --- |
| R01 | Create, list, search, edit and delete local projects | Implemented |
| R02 | Choose one of four project types (task briefing, meeting, tutorial, generic) at creation or, at the latest, when processing starts | Implemented: four types chosen with cards at creation (P02.1, P02.3) or in the Process dialog of a Not processed project, which the API requires when the project has no type (P05.4) |
| R03 | Upload one MP4, WebM, MOV or MKV video per project, validated with FFprobe | Implemented |
| R04 | Record screen, system audio and microphone in the browser; saving stores the project with an optional name and never starts processing | Planned (P05) |
| R04a | Label transcript segments of in-app recordings as You or Others, editable in review | Implemented (P05.5): loudness comparison of the kept microphone and system audio tracks, Speaker select in Review; a real call is checked in P05.6 |
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
| R15 | `start.sh` opens Narrastep in its own desktop window; `--headless` keeps browser-only use | In progress: the window exists in `desktop/` (P07.1); `start.sh` opens it after health and `--headless` skips it (P07.2, verified on the Docker stack on Windows on 2026-10-31, not on macOS or Linux desktops) ([ADR 0014](decisions/0014-desktop-app-shell.md)) |
| R16 | `start-podman.sh` runs the same stack, with the same options and desktop window, on Podman instead of Docker | Partly delivered: script and stub tests (P08.9–P08.10); not yet run on real Podman (P08.12) |
| R17 | When `.env` does not exist, the start scripts generate one from the computer's CPU, memory, free disk and free ports; an existing `.env` is never changed | Delivered in `start.sh` and `start-podman.sh` (P08.6–P08.8, P08.10); not yet run on real Podman |
| R18 | Libraries, base images and tools are pinned and updated to the latest versions that pass the checks; transcription of any accepted video does not fail on library incompatibility | Delivered for Docker (P08.1–P08.5); not yet run on Podman (P08.12) |

## Constraints

- **Local-first.** No account, telemetry, analytics, cloud storage or remote inference. The only
  allowed network request after setup is a model download started by the user
  ([ADR 0007](decisions/0007-local-first-privacy.md), [ADR 0011](decisions/0011-optional-local-generation.md)).
- **No host installs.** The user may be unable to install programs. Docker is required, or Podman once
  [ADR 0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md) is delivered (P08); host
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
