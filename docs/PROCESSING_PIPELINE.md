# Processing Pipeline

## Status legend

- **Implemented:** production code is present and covered by automated tests.
- **Experimental:** available only through an explicit development setting.
- **Planned:** no working behavior is claimed.

## Implemented stages

### 1. Validation and metadata

Input is the project source video. FFprobe runs through a bounded argument array and returns JSON. Narrastep requires a readable video stream, an audio stream, positive duration, and a supported extension. File extension and client MIME type are never trusted alone.

Output is normalized media metadata stored in SQLite. Failure removes partial upload data and returns a stable video or audio validation code.

### 2. Audio extraction

FFmpeg writes PCM signed 16-bit, mono, 16 kHz WAV to the project audio directory. Output is written to a temporary path and promoted after a successful process exit and file validation.

### 3. Transcription

faster-whisper uses the locally stored `small` model with CPU/int8 defaults, automatic or user-selected language, VAD filtering, and word timestamps. Runtime model downloads are disabled. The worker reads the extracted WAV and passes its samples (float32, mono, 16 kHz) to the model, so FFmpeg is the only media decoder: given a file path, faster-whisper decodes it again with PyAV, and faster-whisper 1.2.1 calls PyAV with an argument that PyAV 19.0.0 removed, which made every transcription fail in images built after 2026-09-29 (P08.1, risk K15). A WAV that is not mono 16 kHz 16-bit PCM, or holds no samples, fails with `TRANSCRIPTION_INPUT_INVALID` before the model loads. Raw segments and words are written atomically to `raw-transcript.json`; reviewed text initially equals raw text.

The backend image build runs `scripts/check_transcription_stack.py` (P08.1): it generates one
clip per accepted container with FFmpeg (spoken test sentence from the `flite` source, or
silence; mono, stereo and 5.1 audio at 8, 22.05, 44.1 and 48 kHz), runs validation, extraction
and transcription exactly as a job does with the baked model, and fails the build when a speech
clip does not contain "settings" and "save" or any step fails. It prints the FFmpeg version and
the versions of faster-whisper, CTranslate2, PyAV, NumPy, ONNX Runtime, `tokenizers` and
`huggingface_hub`, and on failure the error chain that names the package at fault.

Progress advances as generated segment end times move through the source duration. Confidence is `exp(avg_logprob)` clamped to `[0, 1]` and is explicitly a heuristic rather than a calibrated probability.

#### Glossary hints (P09.1)

The project's glossary (names and terms saved with the project, see [API](API.md#glossary-p091))
is passed to `transcribe_audio` and from there to faster-whisper as `hotwords`
(`backend/app/services/glossary.py`). faster-whisper puts hotwords in front of the previous text
of every decoding window, so the terms keep their effect across a long video, while an
`initial_prompt` would only seed the first window. The hint is the terms joined with `, ` and no
other words, so the same text serves English, Italian or any other spoken language and does not
push the model towards a language. Whisper reads at most 223 prompt tokens and faster-whisper keeps
the beginning of a longer hotword text, so the hint is limited to about 500 characters: whole terms
are added in the saved order and the first term that would pass the limit ends the list (a long
glossary therefore loses its last terms, and the forms tell the user to put the important ones
first). An empty glossary sends no `hotwords` option, so decoding is unchanged. The hint only
biases the model: a term is not guaranteed to appear, and the raw transcript is stored as Whisper
returned it. The mock transcription used by tests ignores the glossary. Job metrics record only
`glossaryTermCount`; the terms themselves are user text and never go to logs or diagnostics.

#### Speaker labels (in-app recordings, P05.5)

Right after transcription, a project whose video was recorded in Narrastep with both a microphone
and system audio gets a You / Others label per segment (`backend/app/services/speaker_labels.py`).
The worker decodes the kept `source/microphone.webm` and `source/system.webm` to 8 kHz mono PCM
under `.processing/{jobId}/speakers/`, keeps the mean square level per 50 ms frame, removes the
WAV files and compares the two RMS levels over each segment's time range: **You** when the
microphone is at least 10 dB louder, **Others** when the system audio is at least 10 dB louder,
no label when the levels are closer or the louder one is below −50 dBFS. Labels are stored with
`speakerSource: automatic`. Uploaded videos and recordings with one audio source are skipped. A
track that cannot be decoded does not fail processing: every segment stays unlabelled and the job
metrics carry `speakerLabelFailed: 1`; otherwise they carry the counts `speakerLabelYouCount`,
`speakerLabelOthersCount` and `speakerLabelUnlabelledCount` and the `speaker_labels` stage time.
The step runs inside the transcription progress range and is checked for cancellation after it.
Details and measurements: [Recording](RECORDING.md#speaker-labels).

### 4. Legacy candidate extraction and quality

Candidate timestamps are the union of:

- regular `NARRASTEP_FRAME_INTERVAL_SECONDS` samples;
- 10%, 50%, and 90% timestamps inside every transcript segment.

Near-identical timestamps are collapsed. OpenCV samples the target plus the available frames at
`-0.2` and `+0.2` seconds by default. The clearest neighbor is chosen with 60% normalized Laplacian
sharpness and 40% inverse exposure penalty. Exposure treats pixels at or below 10 as clipped black,
pixels at or above 245 as clipped white, and also penalizes severe mean-brightness imbalance.

Stability is the mean of consecutive grayscale-histogram correlations after each correlation is
mapped from `[-1, 1]` to `[0, 1]`. The final candidate quality is 35% stability, 40% normalized
Laplacian sharpness, and 25% inverse exposure penalty. Sharpness is normalized as
`variance / (variance + 100)`.

### 5. Legacy visual deduplication

Each candidate receives a 64-bit perceptual hash by resizing grayscale pixels to 32 x 32, applying
an OpenCV DCT, taking the top-left 8 x 8 coefficients, and comparing them with their median.
Similarity is `1 - hammingDistance / 64`. Consecutive candidates at or above the configured
threshold form a cluster, and the highest-quality member is retained with timestamp tie-breaking.
A later return to the same screen starts a new cluster, preserving temporal context.

The production extractor compares each candidate with the immediately preceding candidate and
writes only a completed cluster representative. It therefore keeps only the current candidate and
cluster representative in decoded image memory; retained screenshots are staged as PNG files.

### 6. Segment association

Candidates are ranked with 70% temporal proximity to the segment midpoint and 30% quality.
Temporal proximity is `max(0, 1 - distanceFromMidpoint / halfSegmentDuration)`. Candidates inside
the segment are preferred; all project candidates are considered only when the segment contains no
candidate. The top candidate becomes primary and up to two distinct candidates become
alternatives. The same screenshot can support more than one segment.

Split and merge reuse the existing project candidates. A user-selected split primary is retained
for the child whose interval contains it. A merge retains the user-selected candidate closest to
the merged midpoint. Remaining associations are ranked again.

### 7. Adaptive selection (`adaptive_v1`, experimental)

The adaptive service is selected only when the queued job snapshot contains `adaptive_v1`.
`legacy` remains the default and its extraction behavior is unchanged.

The scan streams grayscale frames downscaled to a 320-pixel long edge. Sampling is 1 fps for
`tutorial` and `task_briefing`, 0.75 fps for `general`, and 0.5 fps for `meeting`. It
combines structural, histogram, edge, and localized deltas with weights 35%, 25%, 25%, and 15%.
Connected difference regions covering at most 0.15% of the frame are suppressed only in the
localized channel; the unmasked structural, histogram, and edge channels remain available.

The event threshold is `clamp(rolling median + 3 * MAD, 0.12, 0.45)`. A three-second timestamp
ring supplies pre-change context. A window closes after two stable samples, and overlapping or
one-second-adjacent windows merge without losing the strongest peak.

Transient document units are derived from segment and word timestamps. Pauses of at least one
second split a unit. Units shorter than two seconds merge across gaps of at most 0.4 seconds only
when their combined duration is at most ten seconds. Units without an overlapping visual event get
one midpoint fallback anchor. Visual windows fine-decode pre-change, peak, and post-stable anchors;
each anchor uses the existing `-0.2`, exact, and `+0.2` neighbor burst.

Fine quality is 35% normalized Laplacian sharpness, 20% inverse exposure penalty, 30% post-change
stability, and 15% border/crop safety. Global clustering compares every candidate with cluster
representatives, so non-consecutive returns can reuse evidence. pHash similarity at or above 0.94
is a direct duplicate unless either state is mandatory. Similarity from 0.84 through 0.94 requires
downscaled SSIM of at least 0.96. When selective OCR evaluates the pair, normalized token Jaccard
must also reach 0.85. The medoid uses total pHash centrality, quality, then timestamp for stable
ties.

Tesseract is invoked only for borderline pairs and mandatory transient/error evaluation. The
detected installed language pack is used for English, Italian, German, French, or Spanish;
otherwise English is selected and a numeric fallback counter is recorded. TSV text and normalized
tokens remain in memory and are never logged or persisted. Only `textChangeScore` can be stored.

Greedy utility is 30% uncovered-unit coverage, 20% visual novelty, 20% text novelty, 15% transcript
relevance, and 15% quality. Mandatory transient/error states and the final stable state bypass the
numeric budget. Explicit manual evidence is preserved independently during review and export.
Balanced automatic target/hard-cap rates per video minute are:

| Project type | Target | Hard cap |
| --- | ---: | ---: |
| Tutorial | 1.2 | 2.0 |
| Task briefing | 1.5 | 2.2 |
| Generic (`general`) | 0.8 | 1.5 |
| Meeting | 0.4 | 1.0 |

Compact and detailed multiply the target by 0.75 and 1.25. The hard cap never increases. Selected
representatives plus at most two useful alternatives per document unit are persisted, and the same
row can support several segment associations.

### 8. Persistence and late materialization

The legacy worker saves full PNGs. Adaptive review normally saves 640-pixel long-edge WebP
thumbnails at quality 82 and records `thumbnail_only`. Full PNGs are encoded atomically only for
full-size access, explicit recovery or selection, or immutable export. Concurrent encoders use
unique partial paths before replacing the same deterministic destination.

The worker saves screenshots, associations, active transcript segments, and metadata. SQLite is
committed before terminal progress is published. Each job also saves its immutable effective
configuration and versioned numeric metrics. Per-job diagnostics are atomic and content-free.

#### After the original video is removed (P09.2)

Removing the original video ([ADR 0018](decisions/0018-remove-original-video-after-export.md))
deletes the source of every later decode. Thumbnails and any full PNG already saved keep working.
A screenshot that exists only as a position in the video (`thumbnail_only` evidence that was never
opened at full size) can no longer be rebuilt: `services/screenshot_materialization.py` raises
`SOURCE_VIDEO_REMOVED` instead of `PROJECT_ASSET_MISSING`, exact-frame recovery is refused, and
the export request is checked before it is queued: a package with `includeOriginalVideo`, or with
a selected screenshot that has no saved full image, answers `409 SOURCE_VIDEO_REMOVED`, so the
worker never fails late on a file it cannot read. The `legacy` strategy saves full PNGs while
processing, so for those projects a new package stays possible after the removal; `adaptive_v1`
projects can export again only for screenshots opened at full size before. Speaker labels are
computed during processing, so the removed microphone and system-audio tracks are not needed
afterwards. A removed video cannot be reprocessed: removal needs a `completed` project, which
cannot start processing.

### 9. Export screenshot selection

Review retains the complete screenshot candidate set. When a new evidence package is exported, the
worker groups primary associations that use the same source screenshot and writes that image only
once in the canonical `screenshots` directory. Manually selected primary screenshots are always
included. Automatic primary screenshots are ranked by the number of segments they support, their
best segment-association score, visual quality, and transcript order.

Automatic screenshots are globally compared with the configured perceptual-hash threshold. A
visually similar screenshot reuses the strongest already selected representative. Every remaining
distinct primary screenshot is exported under `legacy`. Under `adaptive_v1`, the same mode/profile
budget used by estimation limits automatic groups. Mandatory and manual groups are never merged
away or removed and may exceed both target and hard cap.

The worker then walks reviewed segments in transcript order to create self-contained ChatGPT upload
batches. Each batch reserves one file for `batch-input.md`, so its image capacity is
`NARRASTEP_CHATGPT_MAX_FILES_PER_BATCH - 1`. A new batch starts before a previously unseen image
would exceed that capacity. Repeated evidence is copied once inside a batch and copied again only
when a later batch also needs it. Steps without an image remain in chronological order without
consuming an image slot, and an export with no images still receives one Markdown-only batch.
Canonical screenshots and the existing complete LLM inputs remain unchanged in content. Existing
immutable export archives are never rewritten and keep the Version 0.2 layout.

The project type also selects the wording of three package files
(`backend/app/services/export_guides.py`, [ADR 0009](decisions/0009-project-types.md)):

| File | Type-specific content |
| --- | --- |
| `01-ai-input/generation-prompt.md` | Type label, purpose, the document sections in order, screenshot guidance, the shared evidence-only rules and the type's own rules |
| `04-chatgpt-batches/README.md` | Type label and purpose, plus the section list to assemble after the last batch |
| `04-chatgpt-batches/batch-NN-of-NN/batch-input.md` | A task block before the evidence: the type, whether the batch opens or closes the recording, and notes to write under the type's section names |

Labelled segments (P05.5) carry their speaker into the package: `**Speaker:** You` in
`reviewed-transcript.md`, a `You:` or `Others:` prefix in `subtitles.srt`, and a `Speaker:` line
in `llm-input.md` and `batch-input.md`, which also explain the labels once when any step has one.

The sections are those of the "Output per project type" table in
[Local generation](LOCAL_GENERATION.md). `01-ai-input/llm-input.md` and the screenshots keep their
content; P03.2 moved them into the numbered archive layout and raised the manifest schema to
`0.3.0` (see "Export layout" in [Data model](DATA_MODEL.md)). A legacy `technical_walkthrough` value read from a job snapshot is
treated as `task_briefing`; an unknown value falls back to `general` without failing the export.
The scan rate and the image budgets above are the type-specific screenshot profile; a unit test
checks that the analysis profile and the export budget use the same rates for every type.

When the user includes a generation (P06.4c), the worker adds one step after the transcript files:
it resolves the chosen generation of the same project, checks that it is `completed` and that its
`document.md` is a file inside the data root, and copies that file unchanged to
`05-generated-document/document.md`. The manifest then lists `paths.generatedDocument` and
`includedGeneration` (id, model, prompt version, language and completion time); without a choice
both are `null` and the folder is absent. A missing or unfinished generation fails the export with
`PROJECT_ASSET_MISSING` and leaves no ZIP, the same as a missing screenshot. The document is not
rewritten: the screenshot file names it cites come from the same selector (`select_package_screenshots`)
as `02-screenshots/`, so they match while the review is unchanged. The step does not touch earlier
archives.

The estimate endpoint and immutable generator call the same selector. An unmaterialized PNG is
estimated conservatively as `max(thumbnailBytes, width * height * 0.75)` and the response marks the
byte total approximate. Export materializes thumbnail-only representatives inside its temporary
workspace. Actual canonical-image, batch, batch-copy, byte, duration, and memory metrics are stored
with immutable export history.

## Diagnostics

The pipeline measures candidate timestamps, decoded frames, provisional candidates, cluster
representatives, OCR calls, persisted WebPs/PNGs, associations, canonical export images, batches,
batch copies, bytes, stage durations, and peak resident memory when the platform exposes it. Stage
logs and diagnostic JSON never contain transcript/OCR text, filenames, absolute paths, window
titles, or image content.

## Rollout gate

`adaptive_v1` remains experimental until an annotated, entirely local benchmark demonstrates all
of the following against `legacy`: critical-state recall at least 0.98; missed-critical-state rate
at most 0.02; redundancy reduction at least 30%; persisted PNG reduction at least 40%; export batch
copy reduction at least 25%; balanced CPU processing-time increase no more than 15%; and peak RAM
within the documented machine budget. Failure of any gate leaves `legacy` as the default. After a
successful default flip, `legacy` remains available as a configurable fallback for one release.

## Progress ranges

| Stage | Range |
| --- | ---: |
| Validation and metadata | 0–10% |
| Audio extraction | 10–20% |
| Transcription | 20–60% |
| Frame extraction | 60–75% |
| Deduplication | 75–83% |
| Association | 83–92% |
| Persistence | 92–100% |

Cancellation is checked between bounded units. A single decoder or FFmpeg operation may complete before cancellation is observed.

## Experimental

`adaptive_v1` is available only through its explicit strategy setting until the rollout gates pass.
Deterministic mock processing is available only when `NARRASTEP_ENABLE_MOCK_PROCESSING=true`. It
exists for repeatable API and browser tests and is never selected automatically after a real
failure.

## Planned

- Optional local generation runs after review as a separate job
  ([Local generation](LOCAL_GENERATION.md)).
- Persisted OCR indexing, speaker labelling of uploaded videos (diarization), sensitive-data
  masking, screenshot annotation and
  GPU-specific tuning are candidates in the [roadmap](ROADMAP.md).
- PaddleOCR, CLIP/SigLIP and TransNetV2 stay deferred.

## Performance and limitations

CPU transcription is typically the dominant cost. Candidate count scales with video duration and segment count; the default three-second interval is deliberately conservative. Visual hashing detects appearance, not business meaning, and can retain an unhelpful stable screen or discard a subtle but important change. Human review remains required.
