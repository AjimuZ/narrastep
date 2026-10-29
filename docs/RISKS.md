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
| K9 | A dependency upgrade moves to a non-free license (Redis 7.4 and later did) | Public repository can no longer depend on it | Pin versions; check [Licenses](LICENSES.md) on every upgrade; Valkey replaces Redis if needed |
| K10 | The npm registry or the Electron download is blocked on first launch | No desktop window | The launcher says so; `./start.sh --headless` keeps Narrastep usable in a browser |
| K12 | Loopback system audio records every application, including notification sounds | Unwanted audio in the recording | Recorder notice; the user can mute notifications while recording |
| K11 | Italian strings are longer and break layouts | Truncated labels | 320 px and 200% zoom checks run in both languages (P04.5) |
| K13 | On Linux, Chromium's sandbox needs unprivileged user namespaces or a root-owned `chrome-sandbox`; without either Electron refuses to start (seen on the build worker in P07.1) | No desktop window on such Linux systems | The shell never turns the sandbox off; the window tests add `--no-sandbox` only with `NARRASTEP_DESKTOP_TEST_NO_SANDBOX=1`; Windows is not affected; `./start.sh --headless` (P07.2) keeps the browser mode |
| K14 | The web container sends `Permissions-Policy: camera=(), microphone=(), geolocation=()` with the app page (`apps/web/nginx.conf`, inherited by `location /`), and Chromium then refuses `getUserMedia` for the microphone (reproduced on 2026-10-29 with a stand-in server in headless Chromium; not run against the nginx container) | The recorder cannot use the microphone on the Docker stack, in a browser and in the desktop window | Allow the microphone for the app's own origin (`microphone=(self)`) before the P05.6 manual test and P07.4; tests so far used the development server, which does not send the header |

## Open questions

| ID | Question | Needed by |
| --- | --- | --- |
| Q2 | Which remaining improvement candidates to schedule? | P02 |
| Q5 | When `huggingface.co` is blocked, allow importing the same verified model files from disk? | P06.2 |
| Q6 | Default model: E2B (lighter) or E4B (better)? Decide after the P06.5 benchmark | P06.5 |

## Resolved

| ID | Question | Answer (2026-10-28) |
| --- | --- | --- |
| Q1 | Accept ADRs 0009–0013? | Accepted, including `llama.cpp` instead of Ollama |
| Q3 | Use a pixel title font? | Yes: Pixelify Sans (SIL OFL 1.1), titles only ([UI](UI.md#typography)) |
| Q4 | Interface language? | English and Italian with a switch ([ADR 0015](decisions/0015-english-and-italian-interface.md)) |
| Q7 | Desktop app: PWA or Electron? | Electron shell launched by `start.sh`, as in Symphony ([ADR 0014](decisions/0014-desktop-app-shell.md)) |
| Q8 | Which licenses are acceptable? | Free licenses only; public, non-commercial repository ([Licenses](LICENSES.md)) |

## Blockers

None.
