# Task runner for this project. `just` (not `make`): make isn't installed on
# the Windows host this is developed on, and just is -- it also avoids make's
# tab-sensitivity and .PHONY bookkeeping for a file that declares no build
# artifacts. https://github.com/casey/just
#
# The GPU recipes below are the point of this file. Any two torch satellites
# resident at once exceed both this card's VRAM and Docker's VM RAM (SDXL
# alone measured at ~7147 MiB of 8188 MiB -- see
# docs/experiments/2026-08-18-lazy-model-ttl-and-hf-cache-volumes.md), so
# `gpu-image` and `gpu-audio` each stop the profile they conflict with before
# starting their own. That constraint was previously real but unenforced,
# living only in a comment and in whoever remembered it.

set shell := ["bash", "-uc"]
set windows-shell := ["bash", "-uc"]

port := env_var_or_default("PORT", "8000")
ui_port := env_var_or_default("UI_PORT", "5173")

# List the available recipes.
default:
    @just --list --unsorted

# Install Python and frontend dependencies.
setup:
    uv sync --extra server --extra generation
    cd frontend && bun install

# Report what's installed, running, reachable, and free. Never fails.
doctor:
    @uv run cinemagraph doctor

# Build the web UI. The API serves this bundle, so run it after UI changes.
build-ui:
    cd frontend && bun run build

# Run the API locally (serves the last-built bundle at /). PORT=8010 to move it.
api:
    @echo "http://localhost:{{ port }}  --  serving frontend/dist (run 'just build-ui' after UI edits)"
    uv run uvicorn server.app:app --port {{ port }}

# Use the localhost hostname, not 127.0.0.1: Vite binds ::1 here.

# Run the Vite dev server with hot reload, proxying to a local API on PORT.
ui:
    @echo "http://localhost:{{ ui_port }}  --  proxying API calls to :{{ port }}"
    cd frontend && VITE_API_PROXY_TARGET=http://127.0.0.1:{{ port }} bun run dev

# Everything that needs no GPU, in Docker: API + built UI on :8000.
up:
    docker compose up -d core

# Same, plus the Vite dev server container on :5173.
dev:
    docker compose up -d core frontend

# Needs GEMINI_API_KEY in .env -- 'gemini' for images, 'lyria3' for music.

# Every feature lit up with no GPU at all, via the hosted backends.
hosted:
    @grep -q '^GEMINI_API_KEY=.\+' .env 2>/dev/null || \
        echo "warning: no GEMINI_API_KEY in .env -- the image and music tabs will stay hidden"
    docker compose up -d core

# Stops the audio satellites first: they cannot share this card (see the
# header). CLIPSeg comes along because it runs on CPU and never contends.

# SDXL image generation, plus CLIPSeg. Stops the audio satellites first.
gpu-image:
    docker compose stop acestep sound-effects 2>/dev/null || true
    docker compose --profile ml --profile image up -d
    @echo "first request after an idle MODEL_TTL pays the model load -- this is expected"

# Note ACE-Step loads at startup and holds its VRAM: it is upstream's image
# and is not under MODEL_TTL, unlike the three satellites this project owns.

# ACE-Step music + Stable Audio sound effects. Stops image-generation first.
gpu-audio:
    docker compose stop image-generation 2>/dev/null || true
    docker compose --profile ml --profile audio up -d
    @echo "first request after an idle MODEL_TTL pays the model load -- this is expected"

# Safe alongside anything: it has no GPU reservation in compose, so it runs
# on CPU and never contends for VRAM.

# CLIPSeg only, for mask prompts. Safe to run alongside any other recipe.
mask:
    docker compose --profile ml up -d

# Stop everything, including the profile-gated satellites.
down:
    docker compose --profile ml --profile audio --profile image down

# Follow logs for whatever is running.
logs *args:
    docker compose logs -f {{ args }}

# Backend tests.
test:
    uv run pytest

# Lint and format everything, backend and frontend.
lint:
    uv run ruff check --fix src tests scripts
    uv run ruff format src tests scripts
    cd frontend && bun run lint

# Currently reports pre-existing findings; kept as a separate recipe rather
# than folded into `lint` for exactly that reason.

# Type-check the backend with mypy.
types:
    uv run mypy src

# The pre-commit hook does this automatically when src/server/ changes; this
# is the manual equivalent for when that hook isn't installed.

# Regenerate frontend/src/client from the app's own OpenAPI schema.
client:
    bash ./scripts/generate-client.sh
