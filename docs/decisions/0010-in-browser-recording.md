# ADR 0010: In-browser screen recording

Status: accepted (2026-10-28). Supersedes: none. Amended (2026-10-28): the microphone is recorded
as a separate track for speaker labels, and recording saves the project without processing it.

## Context

The owner records with OBS and uploads the file. They want to record directly in Narrastep,
capturing the screen, the computer audio (for example a Microsoft Teams call) and their own
microphone. The owner cannot install programs on the computer, so the recorder cannot be a native
application.

## Decision

Record in the browser with standard Web APIs:

- `getDisplayMedia` for the screen and the system or tab audio;
- `getUserMedia` for the microphone;
- two `MediaRecorder` instances started together: screen video with system audio, and the
  microphone alone, both WebM (VP9 or VP8 video, Opus audio).

The browser uploads chunks of a few seconds per track while recording to a resumable recording
session. On completion the API joins the chunks, aligns the two tracks, mixes the microphone into
the screen audio with FFmpeg, validates the result with FFprobe and stores it as the project source
video. The microphone track is kept to label transcript segments "You" or "Others".

Recording only saves: the project is stored with an optional name and **Not processed** state.
The user starts processing later and chooses the project type then. From that point the existing
pipeline is unchanged. Details are in [Recording](../RECORDING.md).

## Alternatives

- Native recorder (OBS integration, FFmpeg screen capture on the host): rejected; requires host
  installs and per-OS code.
- Capture inside a container: rejected; a container cannot see the Windows desktop or its audio.
- Record in memory and upload at the end: rejected; a crash or closed tab loses the recording.
- One mixed audio track: simpler, but loses who spoke; rejected after the owner chose speaker
  labels (I-03).
- Process automatically after stopping: rejected by the owner, who records first and decides later
  when to process.

## Consequences

- In the Electron desktop window ([ADR 0014](0014-desktop-app-shell.md)) the shell supplies the
  source picker and Windows loopback system audio, also for a single window. In a plain browser
  (`--headless`), Chrome and Edge on Windows share system audio only for an entire screen or a tab
  with "Share system audio" or "Share tab audio" checked; Firefox and Safari do not share it, and
  the recorder records the microphone only.
- When speakers are used, the microphone can capture the call a second time. The recorder enables
  echo cancellation and recommends a headset.
- New endpoints, a recording-session table and chunk storage are needed.
- Evidence: Playwright test with fake media devices, API tests for chunk order, resume and
  completion, and a manual test with a real Teams call (P05 in the [roadmap](../ROADMAP.md)).
