# ADR 0011: Optional local multimodal generation

Status: accepted (2026-10-28). Amends: [ADR 0007](0007-local-first-privacy.md).
Amended (2026-10-29, P06.1): pinned server release and measured image growth, listed under
Consequences.

## Context

Today the user downloads the evidence package and gives it to an external AI to write the final
document. The owner wants an optional local alternative with these constraints:

- multimodal, because screenshots matter as much as the transcript;
- a non-Chinese model, Google Gemma 4, in the lightest variant that fits the task;
- no host installs: everything runs inside the Docker stack;
- not installed by default: Narrastep starts without the model, and the user installs and enables
  it from the interface, because some environments forbid it.

## Decision

- **Model:** Gemma 4 E2B instruction-tuned, `Q4_0` GGUF weights plus the `Q8_0` vision projector
  from `ggml-org/gemma-4-E2B-it-GGUF` (about 3.4 GB). Gemma 4 E4B (about 5.2 GB) is offered as a
  higher-quality option. Gemma 4 is released under Apache 2.0.
- **Runtime:** the CPU build of the `llama.cpp` server, copied into the backend image from an
  official image pinned by digest. The binary is inert until a model exists. No model weights are
  part of any image.
- **Installation:** the user starts the download from Settings after seeing the size and license.
  The API downloads from a pinned repository revision, resumes interrupted transfers, verifies the
  SHA-256 of every file and promotes it atomically into `/data/models`. The user can delete it.
- **Execution:** generation is a Celery job. The worker starts `llama-server` as a subprocess on
  loopback inside its container, sends the evidence, writes the result and stops the server. The
  worker network keeps no Internet route.
- **Kill switch:** `NARRASTEP_LOCAL_GENERATION=disabled` hides the feature and makes the API
  refuse any model download. The default is `available` (shown, not installed).
- The only new network request is the model download, started explicitly by the user. It sends
  no project data.

## Alternatives

- Ollama container: rejected for the default stack; its image is about 3.4 GB compressed (it ships
  GPU libraries) and would be downloaded even by users who never enable the feature.
- A separate Compose profile started from the command line: rejected; the owner wants to install
  and enable the feature from the interface.
- Giving the API the Docker socket to start containers: rejected; it grants host-level control.
- Bundling the weights in the image: rejected; the default install must not contain the model.
- Remote AI APIs: rejected by [ADR 0007](0007-local-first-privacy.md).
- Gemma 4 audio input instead of Whisper: rejected for now; audio input is limited to short clips
  and Whisper already works offline.

## Consequences

- ADR 0007 gains one exception: an explicit, user-started model download from a pinned source.
- The backend image grows by the size of the `llama.cpp` CPU binaries. P06.1 pins
  `ghcr.io/ggml-org/llama.cpp:server-v0.5.0` (build b11146) by its index digest, keeps only
  `llama-server` and the libraries it loads in `/opt/llama.cpp`, and measured 40.1 MB uncompressed
  (16.5 MB compressed) on amd64 and 28.0 MB on arm64 from the extracted files. The binaries need
  glibc 2.38 or newer, which the Debian 13 base of `python:3.12.13-slim` provides; the image build
  runs `llama-server --version` and fails when they do not load. Details:
  [Local generation](../LOCAL_GENERATION.md#server-in-the-backend-image).
- CPU generation speed and memory are unknown on the owner's hardware; P06.5 measures them before
  the feature is marked implemented.
- Corporate proxies can block `huggingface.co`; manual import of the same verified files is an open
  question in [Risks](../RISKS.md).
- Full behavior: [Local generation](../LOCAL_GENERATION.md).
