#!/usr/bin/env bash
# Regenerates frontend/src/client from the live FastAPI app's own OpenAPI
# schema. Needs `uv sync --extra server --extra generation` synced first
# (same precondition as running the server itself).
set -e
set -x

uv run python -c "import server.app, json; print(json.dumps(server.app.app.openapi()))" > openapi.json
mv openapi.json frontend/
cd frontend
bun run generate-client
bun run lint
