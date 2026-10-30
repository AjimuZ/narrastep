# Image of the isolated check environment: the project's Python and Node.js versions, FFmpeg and
# the system libraries of headless Chromium. Development only; no product image is built from it.
# sandbox.sh passes the versions it reads from backend/Dockerfile, .nvmrc and apps/web/package.json,
# so a dependency refresh moves this image with the project.
ARG PYTHON_VERSION=3.14.10
ARG NODE_VERSION=22.23.1

FROM node:${NODE_VERSION}-trixie-slim AS node

FROM python:${PYTHON_VERSION}-slim-trixie

ARG PLAYWRIGHT_VERSION=latest

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY --from=node /usr/local/bin/node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        ca-certificates \
        curl \
        ffmpeg \
        git \
        tesseract-ocr \
        tesseract-ocr-eng \
    && npx --yes "playwright@${PLAYWRIGHT_VERSION}" install-deps chromium \
    && rm -rf /var/lib/apt/lists/* /root/.npm \
    && git config --system --add safe.directory /work
