# Risks and open questions

Owner: the repository owner unless stated. Review this file when a roadmap package starts.

## Assumptions

| ID | Assumption | How it is confirmed |
| --- | --- | --- |
| A1 | The owner records on Windows with Chrome or Edge | P05.4 manual test |
| A2 | The owner's computer has at least 16 GB RAM and Docker Desktop can use at least 8 GB | P06.5 benchmark |
| A3 | `huggingface.co` is reachable when the user installs the model | First install in P06.2 |
| A4 | Host Node.js 22.12+ with npm stays available and may run Electron from the project folder, as for Symphony | P07 smoke test |

## Risks

| ID | Risk | Impact | Mitigation |
| --- | --- | --- | --- |
| K1 | System audio is unavailable when a window, not the whole screen, is shared | Call audio missing | Pre-recording check that names the missing track and the fix |
| K2 | Speakers make the microphone record the call twice | Echo in the transcript and wrong You / Others labels | Echo cancellation; headset recommendation; a label needs the microphone or the system audio to be at least 10 dB louder, so segments with close loudness stay unlabelled (P05.5); the user can change any label in Review. Measured with a real call in P05.6 |
| K3 | CPU generation with E2B is too slow for long meetings | Feature unusable on long recordings | Chronological parts with progress; E4B only when hardware allows; P06.5 benchmark before release |
| K4 | E2B output quality is too low for task briefings | Wrong or vague documents | Evidence-only prompts with citations; review screen; E4B option |
| K5 | Docker Desktop memory limit is lower than model plus context | Worker killed during generation | Check free memory before the job; readable `GENERATION_OUT_OF_MEMORY` error |
| K6 | The `llama.cpp` binaries copied into the backend image are incompatible with its base system | Build or runtime failure | Pinned by digest; the image build runs `llama-server --version` and fails when a library is missing or incompatible (P06.1); the binaries need glibc 2.38, so the base must stay on Debian 13 or newer; fall back to building from source in a build stage |
| K7 | A meeting is recorded without the participants' consent | Legal exposure for the user | Consent notice before every recording; Narrastep never uploads recordings |
| K8 | `adaptive_v1` benchmark gates never pass | Legacy strategy remains | Already handled: `legacy` stays default ([ADR 0008](decisions/0008-adaptive-screenshot-selection-and-late-materialization.md)) |
| K9 | A dependency upgrade moves to a non-free license (Redis 7.4 and later did) | Public repository can no longer depend on it | Pin versions; every container image has an exact tag and digest recorded in [Licenses](LICENSES.md#container-images), checked by `backend/tests/test_container_images.py` (P08.3); check licenses on every upgrade; Valkey (BSD-3-Clause) replaced Redis in P08.3 ([ADR 0017](decisions/0017-valkey-broker.md)). Verified on 2026-10-30 with Docker: all images built without cache from those digests, the stack reported health `ok` on Valkey 9.1.3 and a generated video passed from upload to export; reading an existing Redis 7.2 volume is untested |
| K10 | The npm registry or the Electron download is blocked on first launch | No desktop window | The launcher says so; `./start.sh --headless` keeps Narrastep usable in a browser |
| K12 | Loopback system audio records every application, including notification sounds | Unwanted audio in the recording | Recorder notice; the user can mute notifications while recording |
| K11 | Italian strings are longer and break layouts | Truncated labels | 320 px and 200% zoom checks run in both languages (P04.5) |
| K13 | On Linux, Chromium's sandbox needs unprivileged user namespaces or a root-owned `chrome-sandbox`; without either Electron refuses to start (seen on the build worker in P07.1) | No desktop window on such Linux systems | The shell never turns the sandbox off; the window tests add `--no-sandbox` only with `NARRASTEP_DESKTOP_TEST_NO_SANDBOX=1`; Windows is not affected; `./start.sh --headless` (P07.2) keeps the browser mode |
| K14 | The web container sends `Permissions-Policy: camera=(), microphone=(), geolocation=()` with the app page (`apps/web/nginx.conf`, inherited by `location /`), and Chromium then refuses `getUserMedia` for the microphone (reproduced on 2026-10-29 with a stand-in server in headless Chromium; not run against the nginx container) | The recorder cannot use the microphone on the Docker stack, in a browser and in the desktop window | Allow the microphone for the app's own origin (`microphone=(self)`) before the P05.6 manual test and P07.4; tests so far used the development server, which does not send the header |
| K15 | Transcription fails while the audio is extracted or decoded because backend packages (only the direct ones were pinned until P08.2) or the unpinned Debian FFmpeg are incompatible with each other. Reported by the owner on 2026-10-29. Reproduced on 2026-10-29 (P08.1): faster-whisper 1.2.1 decodes a file path with PyAV and passes `metadata_errors` to `av.open`; PyAV 19.0.0 (2026-09-29) removed it, so every transcription in an image built since then fails with `TypeError` (PyAV 18.1.0 works). FFmpeg 7.1.5 and 316 container, codec, rate and channel combinations extracted correctly | Processing fails for every video in an affected build | Mitigated in P08.1: Whisper receives the samples FFmpeg extracted, so PyAV is not used at runtime, and the image build runs `scripts/check_transcription_stack.py`, which fails on this and on other broken package sets. P08.2 (2026-10-29) locked every package, direct and transitive, with hashes in `backend/requirements.lock`; the image installs only from it, so a new upstream release can no longer change a build. P08.3 pins the base image |
| K16 | Podman differs from Docker: Compose provider, `depends_on` health conditions, rootless volume ownership and user ids, short image names, image format dropping `HEALTHCHECK` | The stack does not start or starts in the wrong order | Engine-neutral Compose and Dockerfiles (P08.9); `start-podman.sh` checks the provider and rootless mode (P08.10); owner smoke test on real Podman (P08.12) |
| K17 | A Podman machine (macOS, Windows) or Docker Desktop defaults to little memory, so the model chosen from the host's memory does not fit | Worker killed during transcription | The generator measures the engine's limits, not the host's, and prints how to raise them (P08.6) |
| K18 | An autonomous agent commits something wrong, or goes beyond its task, and the runner publishes it at once on the public `main` ([ADR 0019](decisions/0019-autonomous-development-runner.md)) | A public fix or revert, with the mistake visible in the history | The agent cannot commit, push or change task state; every task runs all the suites and may not weaken tests; each push checks every waiting commit for forbidden paths, secrets and authorship words, without force; `.env`, `data/` and `.git` are out of reach in the sandbox. No person reads a commit first. Not a security boundary: allowed scripts run containers with network access, the desktop smoke test runs repository code on the host, and shell permission patterns are best-effort |

## Open questions

None. Q5 and Q6 were answered by the owner on 2026-10-29 and are listed below; what they decide is
still to be built or measured in the packages named there.

## Resolved

| ID | Question | Answer (2026-10-28) |
| --- | --- | --- |
| Q1 | Accept ADRs 0009–0013? | Accepted, including `llama.cpp` instead of Ollama |
| Q2 | Which remaining improvement candidates to schedule? | Decided 2026-10-29 (P01.5): I-15 in P08.5, I-05 in P06.4, I-02 as P09.1, I-07 as P09.2; I-01 (`start.ps1`) waits until after P08.12; optional candidates stay candidates ([Roadmap](ROADMAP.md#improvement-candidates)) |
| Q3 | Use a pixel title font? | Yes: Pixelify Sans (SIL OFL 1.1), titles only ([UI](UI.md#typography)) |
| Q4 | Interface language? | English and Italian with a switch ([ADR 0015](decisions/0015-english-and-italian-interface.md)) |
| Q7 | Desktop app: PWA or Electron? | Electron shell launched by `start.sh`, as in Symphony ([ADR 0014](decisions/0014-desktop-app-shell.md)) |
| Q8 | Which licenses are acceptable? | Free licenses only; public, non-commercial repository ([Licenses](LICENSES.md)) |
| Q9 | Does the newest Python release that `ctranslate2` and `onnxruntime` publish wheels for pass the suite? | Python 3.14.10: the backend suite, the transcription stack check and a full job pass on it. Python 3.15 cannot be used yet because ONNX Runtime 1.31.1 publishes no 3.15 wheels; move when it does (2026-10-29, P08.2, [Licenses](LICENSES.md#kept-behind-their-newest-release)) |
| Q10 | Keep Redis 7.2 (the last BSD release) or move to Valkey (BSD-3-Clause) when the dependency refresh reaches it (K9)? | Valkey 9.1.3, keeping the `redis` service name, URLs and volume (2026-10-29, P08.3, [ADR 0017](decisions/0017-valkey-broker.md)); the image build, stack health and a full job passed on Docker on 2026-10-30 |
| Q11 | Run Narrastep with Podman, and generate a missing `.env` from the computer? | Yes, with a dedicated `start-podman.sh` and a generator that never overwrites an existing `.env` (2026-10-29, [ADR 0016](decisions/0016-podman-generated-environment-and-dependency-refresh.md)) |
| Q5 | When `huggingface.co` is blocked, allow importing the same verified model files from disk? | Yes, from a mounted read-only folder, accepting only files whose name, size and SHA-256 match the pinned revision; no arbitrary paths and no browser upload (2026-10-29, P06.2, [Local generation](LOCAL_GENERATION.md#import-from-disk)) |
| Q6 | Default model: E2B (lighter) or E4B (better)? | E2B stays the default. E4B becomes the default only if the P06.5 benchmark on the owner's computer shows a 60-minute meeting generated in at most 30 minutes with a memory peak under 6 GB, inside Docker's 8 GB limit (A2); E4B is always a manual choice in Settings. The thresholds are fixed before the benchmark (2026-10-29, [Local generation](LOCAL_GENERATION.md#default-model-rule)) |

## Blockers

None.
