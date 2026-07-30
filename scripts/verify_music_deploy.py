"""Post-deployment verification for music generation.

Not full CI/CD on purpose -- this project has no automated deploy pipeline
to gate (docs/DESIGN.md sec 6 explicitly defers "Heavy CI/CD" until the repo
has a remote), and these tests need a real GPU acestep can't get on typical
CI hardware anyway. This is the same category of tool as
scripts/golden_check.py: a deliberate, manually-invoked script, not
something wired into an automatic trigger.

Bundles the manual dance done by hand throughout docs/experiments/ into one
command: bring up `acestep`+`core`, wait for both to actually report ready
(not just "container started"), run the tests/integration/ suite
(pytest -m integration), then tear everything down regardless of outcome --
matching this project's standing rule to never leave a `docker compose`
process running unattended. See docs/DESIGN.md sec 3.7/5.9.

Usage:
    uv run python scripts/verify_music_deploy.py
    uv run python scripts/verify_music_deploy.py --keep-up   # skip teardown (debugging)
"""
import argparse
import subprocess
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
ACESTEP_HEALTH_URL = "http://localhost:8001/health"
CORE_CAPABILITIES_URL = "http://localhost:8000/capabilities"
READY_TIMEOUT_SECONDS = 1800  # generous: a cold acestep can take ~15-20min for a fresh ~11GB download
POLL_INTERVAL_SECONDS = 5


def _compose(*args: str) -> int:
    # flush=True throughout this file: stdout is fully block-buffered (not
    # line-buffered) whenever it isn't a real terminal -- true every time this
    # runs under a log redirect/pipe/background-task capture, which is the
    # normal way this script gets invoked. Without it, every print() below
    # queues up and only appears at process exit, all at once and badly out
    # of order relative to the subprocess calls' own output (which flush
    # immediately regardless) -- found by actually running this twice and
    # noticing the output made no chronological sense, not assumed.
    print(f"$ docker compose {' '.join(args)}", flush=True)
    return subprocess.run(["docker", "compose", *args], cwd=ROOT).returncode


def _wait_for_ready() -> bool:
    """Poll both acestep's own /health and core's /capabilities until both
    report the models are actually loaded and music_generation is live --
    not just that the containers started, which can be true well before
    either is ready to serve a real request (see docs/experiments/ for the
    cold-start timing this project has hit more than once).
    """
    deadline = time.monotonic() + READY_TIMEOUT_SECONDS
    with httpx.Client(timeout=5.0) as client:
        while time.monotonic() < deadline:
            try:
                acestep = client.get(ACESTEP_HEALTH_URL).json()["data"]
                core = client.get(CORE_CAPABILITIES_URL).json()
                if acestep.get("llm_initialized") and core.get("music_generation"):
                    print("acestep fully loaded, core reports music_generation: true", flush=True)
                    return True
                print(
                    f"waiting -- acestep models_initialized={acestep.get('models_initialized')} "
                    f"llm_initialized={acestep.get('llm_initialized')}, "
                    f"core music_generation={core.get('music_generation')}",
                    flush=True,
                )
            except httpx.HTTPError as e:
                print(f"waiting -- not reachable yet ({e})", flush=True)
            time.sleep(POLL_INTERVAL_SECONDS)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--keep-up", action="store_true",
        help="don't tear the stack down afterward (for debugging a failure)",
    )
    args = parser.parse_args()

    if _compose("--profile", "audio", "up", "-d", "acestep", "core") != 0:
        print("docker compose up failed -- is Docker Desktop running?", flush=True)
        return 1

    try:
        if not _wait_for_ready():
            print(f"\nTimed out after {READY_TIMEOUT_SECONDS}s waiting for acestep/core to be ready.", flush=True)
            return 1

        print("\nRunning tests/integration/ ...", flush=True)
        result = subprocess.run(
            ["uv", "run", "pytest", "-m", "integration", "-v"], cwd=ROOT
        )
        return result.returncode
    finally:
        if args.keep_up:
            print("\n--keep-up set -- leaving acestep/core running. "
                  "Remember to `docker compose --profile audio down` when done.", flush=True)
        else:
            _compose("--profile", "audio", "down")


if __name__ == "__main__":
    raise SystemExit(main())
