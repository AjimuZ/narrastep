# Optional local generation (ADR 0011, P06.1): the CPU llama.cpp server from the official image,
# pinned by the digest of its amd64/arm64 index (release v0.5.0, build b11146, commit 7fe450e19).
# v0.5.0 is still the newest `server-v*` release (checked 2026-10-29, P08.3); the `server-b*` tags
# are untagged rolling builds and are not adopted (docs/LOCAL_GENERATION.md).
# Only the server and the shared libraries it loads are kept; the other tools are left behind.
# `cp -P` keeps the library symlinks, so each library is stored once. Nothing runs until a model
# is installed; no model is part of the image.
FROM ghcr.io/ggml-org/llama.cpp:server-v0.5.0@sha256:a94b642b3e2620749bf2ff5672df922c23a6202710e54a8f7667e58df70aba5a AS llama-server

RUN mkdir /opt/llama.cpp \
    && cd /app \
    && cp -P llama-server libllama-server-impl.so libllama-common.so* libllama.so* libmtmd.so* \
        libggml*.so* /opt/llama.cpp/

# Python 3.14 is the newest release with wheels for every locked package; ONNX Runtime has none for
# 3.15 yet (P08.2, docs/LICENSES.md). The tag names the Debian 13 base whose FFmpeg the
# transcription check below exercises, and the digest of its multi-platform index fixes the exact
# base (Debian 13.7, published 2026-10-28; P08.3). The apt packages below come from Debian 13 and
# its security updates at build time: Debian removes superseded versions, so exact apt pins would
# break later builds, and the two build checks below fail on an incompatible update instead.
FROM python:3.14.10-slim-trixie@sha256:3cabfd7d7aaf394e6040e79fa1eec6ca1c52b74ed9188650472695d9a9326999 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1

ARG NARRASTEP_WHISPER_MODEL=small

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ffmpeg \
        ca-certificates \
        libgomp1 \
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        tesseract-ocr-fra \
        tesseract-ocr-ita \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

# The server also needs libstdc++6, libgcc-s1 and libssl3t64 (in the base image) and libgomp1
# (listed above, although FFmpeg and Tesseract already install it). See docs/LOCAL_GENERATION.md.
COPY --from=llama-server /opt/llama.cpp/ /opt/llama.cpp/
COPY third_party/llama.cpp/LICENSE /opt/llama.cpp/LICENSE

WORKDIR /app
COPY pyproject.toml requirements.lock ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

# Installs exactly the packages in requirements.lock, direct and transitive (pip and the build
# backend included), each checked against its SHA-256 hash and only as a prebuilt wheel. The project
# goes last without dependencies, build isolation or an index, so nothing outside the lock is
# downloaded, and `pip check` fails the build on an inconsistent set (P08.2).
RUN python -m pip install --require-hashes --no-deps --only-binary=:all: -r requirements.lock \
    && python -m pip install --no-deps --no-build-isolation --no-index . \
    && python -m pip check \
    && mkdir -p "/opt/models/${NARRASTEP_WHISPER_MODEL}" /data \
    && python scripts/download_model.py --model "${NARRASTEP_WHISPER_MODEL}" "/opt/models/${NARRASTEP_WHISPER_MODEL}" \
    && useradd --create-home --uid 10001 narrastep \
    && chown -R narrastep:narrastep /app /data /opt/models

# Fails the build when the copied server cannot load its libraries on this base system or is not
# the pinned build (risk K6).
RUN python scripts/check_llama_server.py

# Fails the build when this image's FFmpeg and Whisper packages cannot turn generated clips (every
# accepted container; mono, 5.1, odd rates, silence) into the expected transcript with the baked
# model (P08.1, risk K15). Takes about 10 s with `small` on 4 CPUs and needs no network.
RUN HF_HUB_OFFLINE=1 python scripts/check_transcription_stack.py \
        --model-path "/opt/models/${NARRASTEP_WHISPER_MODEL}"

ENV HF_HUB_OFFLINE=1 \
    HF_HUB_DISABLE_TELEMETRY=1 \
    DO_NOT_TRACK=1 \
    TRANSFORMERS_OFFLINE=1 \
    NARRASTEP_DATA_DIR=/data \
    NARRASTEP_WHISPER_MODEL=${NARRASTEP_WHISPER_MODEL} \
    NARRASTEP_WHISPER_MODEL_PATH=/opt/models/${NARRASTEP_WHISPER_MODEL}

USER narrastep
EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
