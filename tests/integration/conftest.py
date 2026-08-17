"""Fixtures for tests/integration/ -- see docs/DESIGN.md sec 3.7 for why these
exist, are marked `integration`, and are excluded from the default test run
(pyproject.toml's addopts).

These hit a real, already-deployed `core` (docker compose up), never
TestClient -- no in-process shortcuts, no "BackgroundTasks runs synchronously"
convenience a real deployment doesn't get. Each capability's own conftest/test
module does its own /capabilities check and skips (not fails) when its
service isn't configured, so the skip reason names the actual missing piece
rather than a single generic "integration tests skipped" note.
"""

import os

import httpx
import pytest

BASE_URL = os.environ.get("CINEMAGRAPH_TEST_BASE_URL", "http://localhost:8000")


@pytest.fixture(scope="session")
def live_client():
    with httpx.Client(base_url=BASE_URL, timeout=30.0) as client:
        yield client


@pytest.fixture(scope="session")
def capabilities(live_client):
    try:
        resp = live_client.get("/capabilities")
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPError as e:
        pytest.skip(
            f"core not reachable at {BASE_URL} ({e}) -- run `docker compose up core` "
            "(or set CINEMAGRAPH_TEST_BASE_URL) first"
        )
