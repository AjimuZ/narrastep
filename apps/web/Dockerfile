# Build-only Node.js, pinned by the digest of its multi-platform index. The version must equal
# `.nvmrc` and `engines.node` (`engine-strict` stops `npm ci` otherwise), so P08.4 moves them
# together.
FROM node:22.23.1-alpine@sha256:16e22a550f3863206a3f701448c45f7912c6896a62de43add43bb9c86130c3e2 AS build

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
FROM nginx:1.30.6-alpine@sha256:0e52bd054126cdc040b1276c0af347aeb02a41b9cfe305d6094767ac30e97949

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /workspace/dist/narrastep-web/browser /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget --quiet --tries=1 --spider http://127.0.0.1/ || exit 1
