# Narrastep

Narrastep is a local web application that turns a screen recording with spoken explanation into a
reviewed transcript, the relevant screenshots and a package for writing the final document. It runs
entirely on your computer inside Docker.

## What it is for

| Situation | Result |
| --- | --- |
| Someone explains a task or a problem you must solve | A task briefing: problem, context, screens, expected result, open questions |
| A meeting | Summary, decisions, action items and open questions |
| A procedure | A step-by-step tutorial with one screenshot per step |
| Anything else | A structured summary with a timeline |

## Status

Version 0.2 provides the full local workflow: create a project, upload a video, transcribe and
select screenshots locally, review and edit, and export a ZIP package for an AI writing tool.

The next versions are **planned** and documented, not implemented:

- four project types matching the table above ([ADR 0009](docs/decisions/0009-project-types.md));
- descriptive export file and folder names ([ADR 0013](docs/decisions/0013-descriptive-export-names.md));
- a redesigned pixel-style interface in English and Italian ([UI](docs/UI.md));
- screen, system-audio and microphone recording in the browser ([Recording](docs/RECORDING.md));
- optional local document generation with Gemma 4, installed from the interface
  ([Local generation](docs/LOCAL_GENERATION.md));
- a desktop window opened by `start.sh`, as an Electron shell over the same local app
  ([ADR 0014](docs/decisions/0014-desktop-app-shell.md)).

The exact state of each capability is in [Status](docs/STATUS.md); the plan is in the
[Roadmap](docs/ROADMAP.md).

## Requirements

- Docker with the `docker compose` command. Nothing else is installed on the computer.
- For the planned desktop window: host Node.js 22.12 or newer with npm (Electron is then
  downloaded into the project's `desktop/` folder on first launch).
- At least 8 GB of free memory and about 5 GB of disk for images, the Whisper model and a small
  project.
- Internet access during the first build only.

## Start

1. Copy the local settings file (non-secret defaults):

   ```powershell
   Copy-Item .env.example .env
   ```

   On POSIX shells: `cp .env.example .env`.

2. Build and start the stack from Git Bash or WSL:

   ```bash
   ./start.sh
   ```

   `./start.sh -r` reuses existing images and fails instead of building when one is missing. Every
   mode shows the logs until `Ctrl+C`.

   When the stack answers its health check, `start.sh` opens Narrastep in its own desktop window.
   That needs Node.js 22.12 or newer with npm on your computer; the first launch downloads the
   pinned Electron into `desktop/node_modules` (Internet access once, no administrator rights).
   `./start.sh --headless` starts only the Docker stack, for use with a browser. If the window
   cannot open, the stack keeps running and `start.sh` says why.

3. Use the desktop window, or open `http://127.0.0.1:4200` in a browser.

The API is at `http://127.0.0.1:8002/api/v1`, with interactive documentation at
`http://127.0.0.1:8002/api/docs`. Stop the stack with `docker compose down`. Adding `-v` deletes
every project.

## How processing works

1. FFprobe validates the video; FFmpeg extracts mono 16 kHz audio.
2. faster-whisper transcribes locally with word timestamps.
3. OpenCV selects, scores and deduplicates screenshots and links them to transcript segments.
4. You review the transcript and screenshots, then export an immutable ZIP.

Every export is one folder with numbered subfolders and a `README.md` that says where to start.
Long exports also contain `04-chatgpt-batches/` folders sized for the ChatGPT web upload limit
(20 files by default, configurable). Narrastep never uploads them. Details:
[Processing pipeline](docs/PROCESSING_PIPELINE.md).

## Configuration

All settings use the `NARRASTEP_` prefix and are listed in [.env.example](.env.example).

| Setting | Default | Purpose |
| --- | ---: | --- |
| `NARRASTEP_API_PORT` | `8002` | Loopback API port |
| `NARRASTEP_WEB_PORT` | `4200` | Loopback web port |
| `NARRASTEP_MAX_UPLOAD_SIZE_MB` | `4096` | Upload size limit, also for one whole recording |
| `NARRASTEP_RECORDING_MAX_CHUNK_MB` | `64` | Largest accepted recording chunk |
| `NARRASTEP_RECORDING_INTERRUPTED_AFTER_SECONDS` | `30` | Time without chunks before a recording counts as interrupted |
| `NARRASTEP_RECORDING_CHUNK_SECONDS` | `5` | Length of each recording chunk the recorder uploads |
| `NARRASTEP_RECORDING_VIDEO_BITS_PER_SECOND` | `2500000` | Video bitrate of the recorder (about 1.1 GB per hour) |
| `NARRASTEP_RECORDING_MAX_FRAME_RATE` | `15` | Highest frame rate the recorder asks for |
| `NARRASTEP_WHISPER_MODEL` | `small` | Whisper model baked into the image at build |
| `NARRASTEP_SCREENSHOT_SELECTION_STRATEGY` | `legacy` | `legacy` or experimental `adaptive_v1` |
| `NARRASTEP_IMAGE_BUDGET_PROFILE` | `balanced` | `compact`, `balanced` or `detailed` |
| `NARRASTEP_CHATGPT_MAX_FILES_PER_BATCH` | `20` | Files per ChatGPT upload batch |

Invalid values stop startup with a readable error.

## Privacy

- Videos, transcripts and screenshots stay in the `narrastep-data` Docker volume.
- No account, telemetry, analytics, CDN, cloud storage or remote AI.
- The worker has no Internet route; the Whisper model is opened offline.
- Delete one project from the interface, or everything with `docker compose down -v`
  (irreversible).

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Port 4200 or 8002 is busy | Change `NARRASTEP_WEB_PORT` or `NARRASTEP_API_PORT` in `.env` and restart |
| Worker unavailable | `docker compose ps` must show Redis, API and worker running; then `docker compose logs worker` |
| Model missing | Rebuild the backend image with Internet access; processing never downloads it |
| FFprobe rejects a file | The file needs a readable video stream and an audio stream |
| Processing is slow | The CPU `small` model favors portability; time grows with video length |
| Disk full | Delete projects you no longer need |

## Limitations

- One source video and one background operation per project.
- Cancellation waits for the current decoding unit.
- Screenshot selection is visual and temporal; it does not understand the meaning of a screen.
- Transcript confidence is a heuristic, not a probability.

## Documentation and contributing

Start from the [documentation index](docs/README.md). Development setup and every check command are
in [Development](docs/DEVELOPMENT.md). Read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CLAUDE.md](CLAUDE.md) before changing code.

## License

[MIT](LICENSE). Narrastep is a free, non-commercial project and uses only components with free
licenses; each one keeps its own license, listed in [Licenses](docs/LICENSES.md).
