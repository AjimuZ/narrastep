# Recording

Status: **in progress** (P05 in the [roadmap](ROADMAP.md)). The recording session API with
per-track chunk storage, resume and discard is implemented (P05.1); completion into a source video
(P05.2), the recorder screen (P05.3) and speaker labels (P05.5) are planned. Until P05.3 Narrastep
offers no way to record from the interface.
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
| `NARRASTEP_RECORDING_MAX_CHUNK_MB` | `64` | Largest accepted chunk (implemented, P05.1) |
| `NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS` | `30` | Silence after which an open session is reported as interrupted (implemented, P05.1) |

The first three settings are planned with the recorder screen (P05.3).

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

## API

| Method | Endpoint | Purpose | Status |
| --- | --- | --- | --- |
| POST | `/api/v1/recordings` | Create a project named after the date and open its recording session | Implemented (P05.1) |
| PUT | `/api/v1/recordings/{recordingId}/tracks/{track}/chunks/{sequence}` | Store one chunk of `screen` or `microphone` atomically; repeating a stored sequence is a no-op | Implemented (P05.1) |
| GET | `/api/v1/recordings/{recordingId}` | Read state, chunk counts, gaps and stored bytes per track, for resume | Implemented (P05.1) |
| POST | `/api/v1/recordings/{recordingId}/complete` | Accept the optional name; join, align, mix, validate and save; never starts processing | Planned (P05.2) |
| DELETE | `/api/v1/recordings/{recordingId}` | Discard the session, its chunks and its project | Implemented (P05.1) |

The implemented endpoints are in [openapi.json](openapi.json); request and response fields are in
[API](API.md#recording-sessions).

### Session rules (P05.1)

- **Create.** The body is optional: `name` (the recorder sends it in the interface language and
  local time, for example `Registrazione 2026-10-29 14:05`) and `sourceLanguage`. Without a name the
  API uses `Recording {YYYY-MM-DD HH:MM}` in the server's local time, which is UTC in the
  container. The project is a `draft` without a video. Until the type becomes optional (P05.4) it
  gets the type `general`.
- **Chunks.** The body is the raw `MediaRecorder` blob (`video/webm`, `audio/webm` or
  `application/octet-stream`), not multipart. `sequence` counts from 0 per track, up to 999999.
  Sequence 0 must start with the WebM (EBML) header; later chunks continue the same stream and
  cannot be checked on their own, so FFprobe validates the joined tracks at completion (P05.2).
  Chunks of both tracks and in any order are accepted in parallel. A repeated sequence with the
  same bytes (same SHA-256 and size) answers `200` with `duplicate: true`; different bytes answer
  `409 RECORDING_CHUNK_CONFLICT` and keep the stored chunk.
- **Start offsets.** Each chunk may carry `startOffsetMs`: milliseconds from the session's common
  start to the first media of that track. The first value sent for a track is stored; the same
  value again is accepted, a different one is `409 RECORDING_CHUNK_CONFLICT`. The recorder sends it
  with every chunk so it reaches the server whichever chunk arrives first.
- **Limits.** One chunk may hold up to `NARRASTEP_RECORDING_MAX_CHUNK_MB` (default 64 MB; a 5 s
  chunk at the default bitrate is about 1.6 MB). Both tracks together may hold up to
  `NARRASTEP_MAX_UPLOAD_SIZE_MB`, the upload limit, and every write keeps the free-disk reserve.
  Exceeding a limit answers `413 UPLOAD_TOO_LARGE` or `507 INSUFFICIENT_DISK_SPACE` and stores
  nothing.
- **Resume.** `GET` lists both tracks, also one with nothing stored, with `chunkCount`,
  `storedBytes`, `nextSequence` (one past the highest stored), `missingSequences` (gaps below it,
  at most 100 listed) and `missingCount`. A recorder that lost the connection uploads again the
  chunks it still holds; stored ones are no-ops. Everything stored survives an API restart; at
  startup, partial files and chunk files that SQLite does not list are removed.
- **Interrupted.** A session still `recording` without a chunk for
  `NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS` (default 30, six chunk lengths) is reported with
  `interrupted: true`, also in the `recording` summary of project responses. A paused recorder
  sends no chunks, so a long pause reads as interrupted too; the recorder screen does not rely on
  the flag. Chunks are still accepted, so a recorder that comes back can continue.
- **Discard.** `DELETE` removes the project, its workspace with every chunk, and the session.
  Deleting the project does the same. A completed recording cannot be discarded; delete its
  project instead.
- **Guards.** While a session is open, uploading a video to its project answers
  `409 INVALID_PROJECT_STATE`, and processing answers `409 PROJECT_ASSET_MISSING` because there is
  no source video yet. A completed session refuses chunks with `409 INVALID_PROJECT_STATE`.

## Verification

- Playwright with `--use-fake-ui-for-media-stream` and `--use-fake-device-for-media-stream` records,
  stops, saves with a name and finds the project as **Not processed**, with no job queued.
- API tests: out-of-order and repeated chunks per track, a missing chunk, an oversized session,
  resume after restart, discard, a session without a microphone track.
- Speaker-label tests on synthetic audio: microphone only, system only, both at once, silence.
- Manual test on the owner's computer with a real Teams call; the result is recorded in
  [Status](STATUS.md) with the date and browser version.
