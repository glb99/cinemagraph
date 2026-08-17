#!/usr/bin/env bash
set -e
set -x

uv run ruff check src tests scripts --fix
uv run ruff format src tests scripts
