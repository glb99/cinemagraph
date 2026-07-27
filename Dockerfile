# Core image only: CLI + API, opencv/numpy/click/fastapi/uvicorn/httpx. Never
# torch/transformers -- those live entirely in the separate machine-learning/
# project (own pyproject.toml, own image, see machine-learning/README.md),
# kept out of this image so it stays small and fast to build/pull.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

WORKDIR /app

# Install dependencies first (separate layer, cached across source-only changes).
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --extra server

# Then the source (cinemagraph + server both live under src/), and install the
# project itself.
COPY src ./src
RUN uv sync --frozen --extra server

ENV PATH="/app/.venv/bin:$PATH"
ENV CINEMAGRAPH_DATA_DIR=/data
VOLUME ["/data"]

EXPOSE 8000

# CLI usage overrides this, e.g.:
#   docker run -v ./data:/data <image> cinemagraph make /data/in.mp4 /data/out.mp4
# `cinemagraph` is already on PATH via the venv above -- no `uv run` prefix needed.
CMD ["uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
