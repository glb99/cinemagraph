# Dev-mode only: runs Vite's own dev server with hot reload. A prod
# static-serve stage (bun build -> nginx/caddy) is still deferred: the app is
# real now (the full UI was ported here from server/ui.py, 2026-08-14), but
# serving it in production also means deciding what answers `GET /` -- the API
# still serves the old single-page UI there -- so that's one decision, not a
# Dockerfile edit. Until then `GET /` is the shipped UI and this service is
# how the SPA runs.
FROM oven/bun:1

WORKDIR /app

COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile

COPY frontend ./

EXPOSE 5173

CMD ["bun", "run", "dev", "--host", "0.0.0.0"]
