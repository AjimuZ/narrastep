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
        tesseract-ocr \
        tesseract-ocr-deu \
        tesseract-ocr-eng \
        tesseract-ocr-fra \
        tesseract-ocr-ita \
        tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

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
