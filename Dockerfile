# Core image only: CLI + API + the built web UI, opencv/numpy/click/fastapi/uvicorn/httpx/
# google-genai. Never torch/transformers -- those live entirely in the
# separate machine-learning/ project (own pyproject.toml, own image, see
# machine-learning/README.md), kept out of this image so it stays small and
# fast to build/pull. `generation` extra (google-genai, a thin API client,
# not a multi-GB local model -- see docs/DESIGN.md sec 3.2) is included
# unconditionally alongside `server`; GeminiAdapter only actually registers
# at runtime if GEMINI_API_KEY is set (server/app.py), so this costs nothing
# for deployments that never configure it.
#
# The web UI ships inside this image too, built in a throwaway first stage:
# only frontend/dist survives into the runtime image, so bun and
# node_modules cost nothing at runtime. GET / serves that directory
# (server/app.py + CINEMAGRAPH_FRONTEND_DIR below); without this stage the
# API would come up fine but every UI route would report "not built yet".
FROM oven/bun:1 AS frontend-build

WORKDIR /app/frontend

# Manifests first so the dependency install layer survives source-only edits.
COPY frontend/package.json frontend/bun.lock ./
RUN bun install --frozen-lockfile

COPY frontend ./
# `vite build && tsc -b`, in that order: the TanStack Router plugin generates
# src/routeTree.gen.ts during the Vite build, and that file is gitignored, so
# a type-check first would fail on a clean checkout -- exactly what a Docker
# build always is. See frontend/package.json.
RUN bun run build


FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install dependencies first (separate layer, cached across source-only changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra server --extra generation

# Then the source (cinemagraph + server both live under src/), and install the
# project itself.
COPY src ./src
RUN uv sync --frozen --extra server --extra generation

# Last, since the UI changes more often than the Python deps above it.
COPY --from=frontend-build /app/frontend/dist ./frontend/dist

ENV PATH="/app/.venv/bin:$PATH"
ENV CINEMAGRAPH_DATA_DIR=/data
# Explicit rather than relying on config.py's repo-relative default: /app has
# no repo layout, only what the COPYs above put there.
ENV CINEMAGRAPH_FRONTEND_DIR=/app/frontend/dist
VOLUME ["/data"]

EXPOSE 8000

# CLI usage overrides this, e.g.:
#   docker run -v ./data:/data <image> cinemagraph make /data/in.mp4 /data/out.mp4
# `cinemagraph` is already on PATH via the venv above -- no `uv run` prefix needed.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
