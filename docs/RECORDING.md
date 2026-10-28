# Recording

Status: **planned** (P05 in the [roadmap](ROADMAP.md)). Version 0.2 accepts uploaded files only.
Decision: [ADR 0010](decisions/0010-in-browser-recording.md).

## Goal

Record the screen, the computer audio and the microphone directly in Narrastep, as an alternative
to recording with OBS and uploading the file. Nothing is installed system-wide.

## Where recording runs

| Where | How the source is chosen | System audio on Windows |
| --- | --- | --- |
| **Desktop window** (default, [ADR 0014](decisions/0014-desktop-app-shell.md)) | Narrastep's own picker lists screens and windows | Always, for a whole screen and for a single window (`audio: 'loopback'`) |
| Chrome or Edge with `--headless` | The browser picker | Only for an entire screen with "Share system audio", or a tab with "Share tab audio" |
| Firefox or Safari with `--headless` | The browser picker | None; microphone only |

## What can be recorded

| Source | Video | Typical use |
| --- | --- | --- |
| Entire screen (one monitor) | Everything on that monitor, any application | Default: Teams call, desktop apps, switching between programs |
| Window | One application window | Focus on one program without showing the rest |
| Browser tab (browser only) | One tab | Web applications, Teams on the web |

Recording continues while the user works in other applications; the Narrastep window or tab only
needs to stay open. With several monitors, the user picks one; recording several monitors at once
is not supported. When system audio is missing, the recorder says so before recording starts and
offers to continue with the microphone only.

On Windows, loopback system audio includes every application's sound, also when one window is
recorded. Notification sounds from other applications are therefore recorded too.

`127.0.0.1` is a secure context, so the capture APIs work without HTTPS.

## Flow

Recording only captures and saves. It never starts processing.

1. **Start.** "Record" in the menu opens the recorder directly; no project form comes first. The
   API creates a project named `Recording {YYYY-MM-DD HH:MM}` without a type.
2. **Checks.** The user picks the screen and allows the microphone. The recorder shows whether a
   system-audio track exists and a live microphone level gauge. A short notice reminds the user to
   get consent before recording other people.
3. **Recording.** Two `MediaRecorder` instances start together:
   - **screen track:** video plus system audio, WebM (VP9, falling back to VP8; Opus audio);
   - **microphone track:** microphone audio only, WebM Opus.

   Each emits a chunk every 5 seconds, uploaded at once with its track and sequence number. The
   recorder stores the start offset of each track so the server can align them. The screen shows
   elapsed time, uploaded size and pause, resume and stop buttons.
4. **Stop and save.** Stopping shows one optional field, the recording name, with Save and Discard.
   Save completes the session; the project appears in Projects as **Not processed**.
5. **Interruption.** If the tab closes or the network drops, uploaded chunks stay on disk. The
   project appears as **Recording interrupted** with "Save what was recorded" or "Discard".
6. **Completion (server).** The API checks that each track's chunk sequence has no gaps, joins the
   chunks, and with FFmpeg aligns the tracks by their offsets and mixes the microphone into the
   screen audio. The result is the project source video, validated with FFprobe. The microphone
   track is kept next to it as `source/microphone.webm` for speaker labels.
7. **Later.** When the user opens the project and selects Process, Narrastep asks for the project
   type ([ADR 0009](decisions/0009-project-types.md)) and starts the normal processing flow.

## Settings

| Setting | Default | Purpose |
| --- | --- | --- |
| `NARRASTEP_RECORDING_CHUNK_SECONDS` | `5` | Chunk length requested from `MediaRecorder` |
| `NARRASTEP_RECORDING_VIDEO_BITS_PER_SECOND` | `2500000` | Video bitrate; about 1.1 GB per hour |
| `NARRASTEP_RECORDING_MAX_FRAME_RATE` | `15` | Enough for screen content, keeps files small |

The existing upload size limit and free-disk reserve also apply to recordings.

## Audio quality

- Echo cancellation and noise suppression are requested for the microphone.
- With speakers, the microphone can pick up the call a second time. The recorder recommends a
  headset; speaker labels are reliable only with one.

## Speaker labels

Recordings made in Narrastep label each transcript segment **You** (*Tu*) or **Others**
(*Altri*) without a diarization model:

- During processing, the worker measures the loudness (RMS) of the microphone track and of the
  system audio over each segment's time range.
- A segment is **You** when the microphone is clearly louder, **Others** when the system audio is
  clearly louder, and has no label when both are close (people talking over each other, or echo
  from speakers). The thresholds are documented and tested in P05.
- The user can change or clear a label in Review. The raw evidence is not changed.
- Labels appear in the transcript, in the export and in the generation input, so meeting action
  items can say "You" when the user took them.
- Uploaded videos (for example from OBS) have one mixed track and get no labels.

## Planned API

| Method | Endpoint | Purpose |
| --- | --- | --- |
| POST | `/api/v1/recordings` | Create a project named after the date and open its recording session |
| PUT | `/api/v1/recordings/{recordingId}/tracks/{track}/chunks/{sequence}` | Store one chunk of `screen` or `microphone` atomically; repeating a stored sequence is a no-op |
| GET | `/api/v1/recordings/{recordingId}` | Read state, chunk counts and stored bytes per track, for resume |
| POST | `/api/v1/recordings/{recordingId}/complete` | Accept the optional name; join, align, mix, validate and save; never starts processing |
| DELETE | `/api/v1/recordings/{recordingId}` | Discard the session, its chunks and its empty project |

These endpoints are not in [openapi.json](openapi.json) until they are implemented.

## Verification

- Playwright with `--use-fake-ui-for-media-stream` and `--use-fake-device-for-media-stream` records,
  stops, saves with a name and finds the project as **Not processed**, with no job queued.
- API tests: out-of-order and repeated chunks per track, a missing chunk, an oversized session,
  resume after restart, discard, a session without a microphone track.
- Speaker-label tests on synthetic audio: microphone only, system only, both at once, silence.
- Manual test on the owner's computer with a real Teams call; the result is recorded in
  [Status](STATUS.md) with the date and browser version.
