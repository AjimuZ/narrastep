# Optional local generation (ADR 0011, P06.1): the CPU llama.cpp server from the official image,
# pinned by the digest of its amd64/arm64 index (release v0.5.0, build b11146, commit 7fe450e19).
# Only the server and the shared libraries it loads are kept; the other tools are left behind.
# `cp -P` keeps the library symlinks, so each library is stored once. Nothing runs until a model
# is installed; no model is part of the image.
FROM ghcr.io/ggml-org/llama.cpp:server-v0.5.0@sha256:a94b642b3e2620749bf2ff5672df922c23a6202710e54a8f7667e58df70aba5a AS llama-server

RUN mkdir /opt/llama.cpp \
    && cd /app \
    && cp -P llama-server libllama-server-impl.so libllama-common.so* libllama.so* libmtmd.so* \
        libggml*.so* /opt/llama.cpp/

FROM python:3.12.13-slim AS runtime

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
COPY pyproject.toml ./
COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./
COPY scripts ./scripts

RUN python -m pip install --upgrade pip \
    && python -m pip install . \
    && mkdir -p "/opt/models/${NARRASTEP_WHISPER_MODEL}" /data \
    && python scripts/download_model.py --model "${NARRASTEP_WHISPER_MODEL}" "/opt/models/${NARRASTEP_WHISPER_MODEL}" \
    && useradd --create-home --uid 10001 narrastep \
    && chown -R narrastep:narrastep /app /data /opt/models

# Fails the build when the copied server cannot load its libraries on this base system or is not
# the pinned build (risk K6).
RUN python scripts/check_llama_server.py

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
