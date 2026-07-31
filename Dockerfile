# Core image only: CLI + API, opencv/numpy/click/fastapi/uvicorn/httpx/
# google-genai. Never torch/transformers -- those live entirely in the
# separate machine-learning/ project (own pyproject.toml, own image, see
# machine-learning/README.md), kept out of this image so it stays small and
# fast to build/pull. `generation` extra (google-genai, a thin API client,
# not a multi-GB local model -- see docs/DESIGN.md sec 3.2) is included
# unconditionally alongside `server`; GeminiAdapter only actually registers
# at runtime if GEMINI_API_KEY is set (server/app.py), so this costs nothing
# for deployments that never configure it.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install dependencies first (separate layer, cached across source-only changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra server --extra generation

# Then the source (cinemagraph + server both live under src/), and install the
# project itself.
COPY src ./src
RUN uv sync --frozen --extra server --extra generation

ENV PATH="/app/.venv/bin:$PATH"
ENV CINEMAGRAPH_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

# CLI usage overrides this, e.g.:
#   docker run -v ./data:/data <image> cinemagraph make /data/in.mp4 /data/out.mp4
# `cinemagraph` is already on PATH via the venv above -- no `uv run` prefix needed.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
