# Purpose: production image of the Next.js frontend for the homelab cluster.
# Input: the frontend/ directory as build context. NEXT_PUBLIC_* values are inlined at build time, so
#   none are set here: the image carries no domain and no key. BACKEND_URL is read at runtime.
# Output: a Next.js standalone server on port 3000 that runs as the unprivileged user 10001 and needs
#   only /tmp and /app/.next/cache writable, so it runs with a read-only root filesystem.

FROM node:22.23.5-alpine3.24 AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

FROM node:22.23.5-alpine3.24 AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=dependencies /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22.23.5-alpine3.24 AS runtime
WORKDIR /app
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1 \
    PORT=3000 \
    HOSTNAME=0.0.0.0
# Files stay owned by root and read-only for the app user; only the cache directory is writable.
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static
RUN mkdir -p .next/cache && chown 10001:10001 .next/cache
USER 10001:10001
EXPOSE 3000
CMD ["node", "server.js"]
