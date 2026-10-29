# Recording

Status: **in progress** (P05 in the [roadmap](ROADMAP.md)). The recording session API with
per-track chunk storage, resume and discard (P05.1), the completion into a validated source video
with the kept microphone track (P05.2) and the recorder screen (P05.3) are implemented; speaker
labels (P05.5) and the manual test with a real call (P05.6) are planned. The desktop window's own
source picker and Windows loopback audio arrive with the desktop shell (P07); until then the
recorder uses the browser picker.
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
| `NARRASTEP_RECORDING_CHUNK_SECONDS` | `5` | Chunk length requested from `MediaRecorder`, 1 to 60 (implemented, P05.3) |
| `NARRASTEP_RECORDING_VIDEO_BITS_PER_SECOND` | `2500000` | Video bitrate; about 1.1 GB per hour (implemented, P05.3) |
| `NARRASTEP_RECORDING_MAX_FRAME_RATE` | `15` | Enough for screen content, keeps files small, 1 to 60 (implemented, P05.3) |
| `NARRASTEP_RECORDING_MAX_CHUNK_MB` | `64` | Largest accepted chunk (implemented, P05.1) |
| `NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS` | `30` | Silence after which an open session is reported as interrupted (implemented, P05.1) |

The API sends the first three to the recorder in the `capture` field of every session response,
so changing them needs no new web image.

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

## Recorder screen (P05.3)

`/record` (`apps/web/src/app/features/recorder/`), **Record** in the menu and on the Projects
screen. Every text is in English and Italian.

1. **Checks.** Nothing is asked from the browser until the user selects a button.
   - **Choose screen** calls `getDisplayMedia` with `audio: true`, `systemAudio: 'include'` (Chrome
     and Edge then offer "Share system audio" for a whole screen) and a frame rate of at most 15.
     The row says Entire screen, Window or Browser tab from the track's `displaySurface`.
   - **System audio** is Included when the share has an audio track, else Not shared with the
     instructions to include it. Without it, the recording keeps the microphone only.
   - **Use microphone** calls `getUserMedia` with echo cancellation and noise suppression. Once
     on, a device list appears when there is more than one microphone, and a 20-cell gauge shows
     the level: the RMS of the last 2048 samples in dBFS, from −60 dBFS (empty) to 0 dBFS (full),
     read every 100 ms with a fall of at most 8 points per reading so short sounds stay visible.
     The audio is analysed in the page and never stored or sent. Blocked, missing and busy
     microphones each get a sentence saying what to do.
   - A consent reminder and a headset recommendation are always shown.
   - **Start recording** needs a screen and at least one sound source (system audio or the
     microphone), because a recording without sound cannot be transcribed.
2. **Start.** The recorder creates the session with the name `Recording YYYY-MM-DD HH:MM`
   (`Registrazione …` in Italian, local time), applies the session's frame-rate limit to the screen
   track and starts one `MediaRecorder` per track with the session's chunk length: the screen as
   `video/webm;codecs=vp9,opus` (then VP8, then plain WebM; without system audio the same without
   Opus) at the session bitrate, the microphone as `audio/webm;codecs=opus`. Each track's start
   offset is the time from the common start to that recorder's `start` event and goes with every
   chunk. If no format is supported, the empty session is discarded again.
3. **Recording.** The screen shows the state word (Recording or Paused), the length without
   pauses, the uploaded size, the sources and the microphone gauge. Chunks are numbered per track
   from 0 (empty ones skipped) and kept in browser memory only until the API acknowledges them.
   Each track uploads one chunk at a time, both tracks in parallel. A failure that may heal (no
   answer, 408, 429, 5xx other than 507) is retried after 1, 2, 5, 10 and then every 15 seconds,
   with a notice; any other failure (full disk, size limit, conflict, missing session) ends the
   recording with its message, because later chunks would fail the same way. Pause and Resume act
   on both recorders. Ending the share with the browser's own "Stop sharing" button stops the
   recording like Stop.
4. **Stop and save.** Stop waits for each recorder's last chunk, then releases the screen and the
   microphone. The save window shows the length and uploaded size, the parts still uploading (Save
   waits for them), one optional name field whose placeholder is the date name, Save and Discard
   (with a confirmation). Save calls `complete` with the trimmed name (none when empty) and
   `stopAtGap: false`. On `RECORDING_INCOMPLETE` it shows the error and offers **Save what was
   recorded** (`stopAtGap: true`); an `FFMPEG_FAILED` gets a recorder wording ("could not be joined
   into a video"). The saved window says the project is in Projects as **Not processed** and offers
   Open projects and Record again. Nothing calls the processing endpoint.
5. **Leaving.** While recording, stopping or uploading, leaving the screen and the language switch
   ask first ("Stop recording and leave?") and the browser asks before closing or reloading the
   tab. Leaving stops the recorders and gives up chunks not yet uploaded; the stored ones stay.
6. **Interrupted recordings.** Projects shows an open session as **Recording** (still receiving
   chunks) or **Recording interrupted**, not as "No video". Open leads to
   `/record?recording={id}`, which shows the uploaded size, the name field, **Save what was
   recorded** (`stopAtGap: true`) and Discard. A recording that is already saved or discarded
   says so.

Measured on 2026-10-29 on the development worker, through the real API (uvicorn, SQLite, FFmpeg
7.1) and the development server with headless Chromium 149 and its fake devices: a 10-second
recording (with a 3-second pause) uploaded 4 chunks, saved in 0.9 s and became a 9.94 s
VP9/Opus `source.webm` with `microphone.webm`, project **Not processed**, no job, no chunk rows
left; a 60-second recording uploaded 24 chunks, saved in 0.8 s and became a 60.02 s source.

## API

| Method | Endpoint | Purpose | Status |
| --- | --- | --- | --- |
| POST | `/api/v1/recordings` | Create a project named after the date and open its recording session | Implemented (P05.1) |
| PUT | `/api/v1/recordings/{recordingId}/tracks/{track}/chunks/{sequence}` | Store one chunk of `screen` or `microphone` atomically; repeating a stored sequence is a no-op | Implemented (P05.1) |
| GET | `/api/v1/recordings/{recordingId}` | Read state, chunk counts, gaps and stored bytes per track, for resume | Implemented (P05.1) |
| POST | `/api/v1/recordings/{recordingId}/complete` | Accept the optional name; join, align, mix, validate and save; never starts processing | Implemented (P05.2) |
| DELETE | `/api/v1/recordings/{recordingId}` | Discard the session, its chunks and its project | Implemented (P05.1) |

All endpoints are in [openapi.json](openapi.json); request and response fields are in
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

### Completion rules (P05.2)

`POST .../complete` runs in the request, under the project's operation lock, in
`backend/app/services/recording_completion.py`:

1. **Plan.** Each track's stored chunks must form one run from sequence 0. A gap answers
   `409 RECORDING_INCOMPLETE` with the track, the first missing sequence and the missing count;
   with `stopAtGap: true` the track ends before its first gap instead, because a WebM stream cannot
   be decoded reliably across a hole. The screen track needs at least chunk 0. A microphone track
   with no chunk (or, with `stopAtGap`, no chunk 0) is left out.
2. **Disk.** Free space must cover the stored bytes once more plus the free-disk reserve
   (`507 INSUFFICIENT_DISK_SPACE`); the outputs are about the size of the chunks.
3. **Join.** Chunks are not copied into a joined file. FFmpeg reads each track through the
   `concatf` protocol from a list of `file:` URLs, in sequence order; only `concatf` and `file`
   are allowed. A byte concatenation is the stream `MediaRecorder` wrote.
4. **Probe.** FFprobe reads the joined tracks. The screen track needs a video stream, the
   microphone track an audio stream (`422 VIDEO_VALIDATION_FAILED`), and at least one of system
   audio or microphone must exist (`422 VIDEO_AUDIO_TRACK_MISSING`).
5. **Align.** The source video starts with the screen track. The microphone moves by
   `microphone startOffsetMs − screen startOffsetMs`: a later microphone gets leading silence
   (`adelay`), an earlier one loses its head (`atrim`). A track without an offset counts as 0, so
   two tracks without offsets start together. The microphone's own first timestamp is reset to 0
   first, and `aresample=async=1` fills dropped audio with silence so no track drifts.
6. **Mix.** The screen video is copied, never re-encoded. With both audio sources the microphone
   is added to the system audio without lowering either (`amix ... normalize=0`), encoded as Opus
   at 128 kbit/s. With the microphone only, it is the soundtrack; with system audio only, that
   audio is copied. The mix is padded with silence and the output ends with the video, so the
   source duration is the screen duration. VP8, VP9 and AV1 video goes into `source/source.webm`;
   other video (for example H.264 from `video/webm;codecs=h264`) into `source/source.mkv`.
7. **Keep the microphone.** The aligned microphone alone is written to `source/microphone.webm`
   (Opus, 96 kbit/s). Time `t` in it is time `t` in the source video, which speaker labels (P05.5)
   rely on. The asset's `metadataJson.recording` records the chunk counts, the applied shift,
   whether system audio existed and whether the microphone was kept.
8. **Validate and save.** The source video passes the same FFprobe validation as an upload
   (container, video and audio stream, duration, size). Then one transaction marks the session
   `completed` as its first write (which waits for and then blocks chunk uploads), refuses with
   `409 RECORDING_INCOMPLETE` if a chunk was stored meanwhile, deletes the chunk rows, creates the
   media asset, renames the project when a name was sent and sets it to `uploaded`. The outputs are
   renamed into place inside that transaction and removed if it fails. After the commit the
   `recording/` folder is deleted; if that fails, startup removes chunk files without rows.

Measured on 2026-10-29 (development worker, FFmpeg 7.1): a 10-minute synthetic recording
(640×360 VP8 at 15 fps with Opus system audio, 34 MB, plus a 7.7 MB microphone track, 120 chunks
each, microphone 250 ms late) completed in 4.7 s; the source video lasts 600.008 s. Real browser
recordings are measured with P05.6.

Temporary files (`.upload-{token}.partial*` in `source/`) are removed on every failure and at
startup. FFmpeg output is never logged. A failure leaves the session open with every chunk, so the
recorder can upload what is missing and save again, or discard.

## Verification

- Playwright with `--use-fake-ui-for-media-stream` and `--use-fake-device-for-media-stream` records,
  stops, saves with a name and finds the project as **Not processed**, with no job queued
  (`apps/web/tests/e2e/recorder.spec.ts`, P05.3, against the deterministic fake API; the
  real-API run above was a manual check with a scratch script).
- Recorder unit tests (`apps/web/src/app/features/recorder/*.spec.ts`, P05.3) with doubles for
  `MediaRecorder` and the capture API: start offsets, chunk numbering, ordered uploads, retry and
  fatal errors, pause, stop and abort; source checks, start, save with and without a name, missing
  parts, join failure, waiting uploads, ended share, leaving, discard and interrupted sessions.
- API tests: out-of-order and repeated chunks per track, a missing chunk, an oversized session,
  resume after restart, discard, a session without a microphone track.
- Completion tests (`backend/tests/test_recording_completion.py`, P05.2) build WebM tracks with
  FFmpeg at test time, written to a pipe like `MediaRecorder` output, split them at arbitrary byte
  offsets and measure tones in the result: a later and an earlier microphone, mix levels, the kept
  microphone, system audio only, no audio, `stopAtGap`, H.264 into Matroska, a chunk stored during
  the conversion and FFprobe failure. They are skipped when FFmpeg or FFprobe is not on `PATH`.
- Speaker-label tests on synthetic audio: microphone only, system only, both at once, silence.
- Manual test on the owner's computer with a real Teams call; the result is recorded in
  [Status](STATUS.md) with the date and browser version.
