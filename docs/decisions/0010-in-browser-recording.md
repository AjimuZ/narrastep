# ADR 0010: In-browser screen recording

Status: accepted (2026-10-28). Supersedes: none. Amended (2026-10-28): the microphone is recorded
as a separate track for speaker labels, and recording saves the project without processing it.
Amended (2026-10-29, P05.1): implementation choices for the session API, listed under
Consequences. Amended (2026-10-29, P05.2): completion choices, listed under Consequences.
Amended (2026-10-29, P05.3): recorder screen choices, listed under Consequences.

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
The user starts processing later and chooses the project type then: the type is optional until processing, the API refuses to process a project without one (`PROJECT_TYPE_REQUIRED`), and the Process dialog asks for it (P05.4). From that point the existing
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
- Session API choices (P05.1). A recorder project stays a `draft` with a `recording` summary
  instead of gaining a project state, so no CHECK constraint is rebuilt and the existing
  screens keep working. Chunks are raw request bodies (not multipart) stored one file per track
  and sequence; SQLite lists them with a SHA-256, so a repeat is compared, not stored twice.
  Chunk uploads are serialized by SQLite's write lock instead of the project operation lock,
  which would refuse the second track while the first uploads. "Interrupted" is derived on read
  from the time since the last chunk, because a closed tab sends nothing the server could store.
- Completion choices (P05.2). Completion runs in the API request, not as a worker job: the
  screen video is copied, only audio is encoded, and the recorder needs the project before it
  can show it as saved. FFmpeg reads the chunks in place through its `concatf` protocol instead
  of a joined copy, which would need the recording's size again on disk; the alternative of a
  `concat:` argument fails on long recordings because one argument is limited to 128 KB on Linux.
  The microphone is aligned to the screen track, the source video's time 0, by the difference of
  the start offsets (0 when the recorder sent none), and mixed without level normalization so
  neither side is halved; loud overlaps may clip, which matters less than a quiet voice for
  transcription. The kept `microphone.webm` is re-encoded on the same timeline rather than
  stored raw with an offset, so speaker labels need no time arithmetic. Missing chunks refuse the
  save unless the caller asks to stop at the first gap; decoding across a hole is not reliable.
  A recording with neither system audio nor microphone is refused rather than given a silent
  track, since it cannot be transcribed. Chunks are deleted after the save to avoid keeping the
  recording twice. Video that WebM cannot hold (H.264) is copied into Matroska.
- Recorder choices (P05.3). The session is created only when the user starts recording, so
  checking sources never leaves an empty project; the browser picker and the microphone are asked
  only after a button press. Chunks stay in browser memory until the API acknowledges them and are
  uploaded one at a time per track, so a track never has a gap unless an upload fails for good;
  failures that may heal are retried without limit while the page is open, and other failures end
  the recording, since later chunks would fail the same way. The capture settings travel in the
  session response rather than in the web image, so they follow the server configuration. A
  recording needs a screen and at least one sound source; one without sound could not be
  transcribed. Leaving the recorder while it records asks first, and the uploaded part is kept as
  an interrupted recording rather than discarded, because discarding is not reversible. An
  interrupted recording is offered only "Save what was recorded" (`stopAtGap`), because the browser
  that held the missing chunks is gone. The microphone level is computed in the page with Web
  Audio and never stored. The browser picker is used until the desktop window supplies its own
  (P07).
- Speaker-label choices (P05.5). Completion keeps the system audio alone as `system.webm`
  next to `microphone.webm` when both exist, because the mixed source video cannot be split back
  into its two sources reliably after lossy encoding; it costs about 0.7 MB per minute. Labels
  come from the two tracks' RMS level over each segment, not from a diarization model, so nothing
  new is installed. A label needs a 10 dB lead (about three times the amplitude) and an audible
  louder track (−50 dBFS); closer levels stay unlabelled, because a wrong label is worse than
  none and speakers can bring the call into the microphone. Recordings with one audio source get
  no labels: without system audio the call heard through speakers would be "You", and without a
  microphone there is no "You". Labelling is an aid, so a track that cannot be decoded leaves
  the segments unlabelled instead of failing processing. A label changed in Review is stored as a
  user choice (`speakerSource: user`), which merges keep.
- Evidence: Playwright test with fake media devices, API tests for chunk order, resume and
  completion, and a manual test with a real Teams call (P05 in the [roadmap](../ROADMAP.md)).
