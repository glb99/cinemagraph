"""Live integration tests for music generation (docs/DESIGN.md sec 3.7/5.9).

Formalizes what every `docs/experiments/*music*` session did by hand this
project's own history: submit a real /generate/music request against a live
ACE-Step instance, poll the job, download and validate the actual audio, and
confirm library registration -- so the next regression in this integration
(the third one found by hand this session alone) is caught by
`pytest -m integration`, not rediscovered manually.

Run against a live stack:
    docker compose --profile audio up -d acestep core
    uv run pytest -m integration tests/integration/test_music_live.py

Excluded from the default `uv run pytest` run (see pyproject.toml) -- needs a
real GPU-backed ACE-Step instance and takes real minutes, neither of which
the default fast/hermetic suite should depend on. Not asserted bit-exact the
way golden_check.py checks renders: this is generative output, not a
deterministic pipeline -- validity/shape is what's checked, not exact bytes.
"""
import time

import pytest

pytestmark = pytest.mark.integration

POLL_INTERVAL_SECONDS = 3.0
POLL_MAX_SECONDS = 300.0
TEST_DURATION = 15.0


@pytest.fixture(autouse=True)
def _require_music_generation(capabilities):
    if not capabilities.get("music_generation"):
        pytest.skip(
            "music_generation not reported by /capabilities -- is ACESTEP_URL "
            "configured and is the acestep container up?"
        )


def _poll_job(live_client, job_id: str) -> dict:
    deadline = time.monotonic() + POLL_MAX_SECONDS
    while time.monotonic() < deadline:
        resp = live_client.get(f"/jobs/{job_id}")
        resp.raise_for_status()
        status = resp.json()
        if status["status"] in ("done", "error"):
            return status
        time.sleep(POLL_INTERVAL_SECONDS)
    pytest.fail(f"job {job_id} did not finish within {POLL_MAX_SECONDS}s")


def _assert_valid_mp3(data: bytes, expected_duration: float) -> None:
    """Cheap validity check, no new dependency needed: real output from this
    service is ID3-tagged, and its byte size tracks duration at the 128kbps
    CBR ACE-Step actually outputs (confirmed via `file` throughout
    docs/experiments/). Generous tolerance since encoding overhead/ID3 size
    aren't perfectly fixed -- this is a "not obviously broken" check, not a
    precise one.
    """
    assert data[:3] == b"ID3", f"expected an ID3-tagged MP3, got {data[:16]!r}"
    expected_bytes = expected_duration * 128_000 / 8
    assert expected_bytes * 0.5 < len(data) < expected_bytes * 2.0, (
        f"{len(data)} bytes doesn't look like ~{expected_duration}s of 128kbps audio"
    )


def _find_asset_by_prompt(live_client, prompt: str) -> dict:
    library = live_client.get("/library").json()
    asset = next((a for a in library if a["provenance"].get("prompt") == prompt), None)
    assert asset is not None, f"no library entry found with prompt={prompt!r}"
    return asset


def test_music_generation_end_to_end(live_client):
    prompt = "integration test: ambient synth pad, slow, sci-fi"
    resp = live_client.post(
        "/generate/music",
        data={"prompt": prompt, "lyrics": "", "duration": TEST_DURATION, "thinking": "false"},
    )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    status = _poll_job(live_client, job_id)
    assert status["status"] == "done", status.get("error")

    file_resp = live_client.get(f"/jobs/{job_id}/file")
    file_resp.raise_for_status()
    _assert_valid_mp3(file_resp.content, TEST_DURATION)

    asset = _find_asset_by_prompt(live_client, prompt)
    assert asset["tags"] == ["music"]
    assert asset["provenance"]["instrumental"] is False


def test_music_generation_instrumental_overrides_lyrics(live_client):
    """instrumental=true must reach ACE-Step regardless of whatever lyrics
    text is also submitted -- ACEStepAdapter's own docstring explains why an
    empty lyrics field alone does not get this from the real server (only the
    literal "[Instrumental]"/"[inst]" marker does, sent over the wire by the
    adapter itself). Provenance is asserted against the lyrics *actually
    submitted* here, not that internal marker -- run_music_job deliberately
    records the caller's real request, not a backend implementation detail
    (see docs/DESIGN.md sec 3.6's music/sound-effect-ports follow-up).
    """
    prompt = "integration test: instrumental override, no vocals"
    resp = live_client.post(
        "/generate/music",
        data={
            "prompt": prompt,
            "lyrics": "these words must never be sung out loud",
            "duration": TEST_DURATION,
            "thinking": "false",
            "instrumental": "true",
        },
    )
    resp.raise_for_status()
    job_id = resp.json()["job_id"]

    status = _poll_job(live_client, job_id)
    assert status["status"] == "done", status.get("error")

    file_resp = live_client.get(f"/jobs/{job_id}/file")
    file_resp.raise_for_status()
    _assert_valid_mp3(file_resp.content, TEST_DURATION)

    asset = _find_asset_by_prompt(live_client, prompt)
    assert asset["provenance"]["lyrics"] == "these words must never be sung out loud"
    assert asset["provenance"]["instrumental"] is True
