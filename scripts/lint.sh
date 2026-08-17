#!/usr/bin/env bash
set -e
set -x

uv run mypy src
uv run ty check src
uv run ruff check src tests scripts
uv run ruff format src tests scripts --check
