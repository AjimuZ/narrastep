# Build-only Node.js LTS, pinned by the digest of its multi-platform index. The version must equal
# `.nvmrc` and `engines.node` (`engine-strict` stops `npm ci` otherwise), so they move together.
FROM docker.io/library/node:26.13.0-alpine@sha256:c6ad9e9c1d80f6eb607c26b4b0a132ff7a2344fad1c78ea3baa2a7d4c8de6d94 AS build

WORKDIR /workspace

COPY package.json package-lock.json ./
RUN npm ci

COPY angular.json tsconfig.json tsconfig.app.json tsconfig.spec.json ./
COPY scripts ./scripts
COPY src ./src
COPY public ./public

RUN npm run build

# Newest stable nginx line (1.30; 1.31 is mainline), pinned by the digest of its multi-platform
# index (P08.3).
FROM docker.io/library/nginx:1.30.6-alpine@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/dist/narrastep-web/browser /usr/share/nginx/html

EXPOSE 80
