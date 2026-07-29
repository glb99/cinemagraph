"""Unit tests for server/service.py's workflows.

These call the job functions directly rather than through a route. That's
the point of the module existing: run_music_job's polling branches (remote
reports failure, polling times out) are effectively unreachable through an
HTTP round-trip, so they went untested while this code lived in app.py.

Settings are constructed directly (Settings(acestep_url=...)) rather than
via monkeypatch.setenv, since service.py now takes settings as an explicit
argument instead of reading the environment itself -- config is resolved
once by the route (via Depends(get_settings)) and passed through.
"""
import json
from pathlib import Path

import asset_library
import pytest

from server import jobs, service
from server.config import Settings


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(tmp_path / "library"))


@pytest.fixture
def acestep_settings():
    return Settings(acestep_url="http://acestep.invalid")


def test_render_job_registers_generated_output_in_library(tmp_path):
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-video-bytes")

    output = tmp_path / "out.mp4"
    job = jobs.create_job()
    service.run_render_job(
        job.id, fake_render, output, library_kind="generated", library_tags=["video"],
    )

    assert jobs.get_job(job.id).status is jobs.JobStatus.DONE

    [asset] = asset_library.list_assets()
    assert asset.kind == "generated"
    assert asset.tags == ["video"]


def test_render_job_skips_library_when_kind_not_given(tmp_path):
    """The /mask-preview route reuses run_render_job but never passes
    library_kind -- a diagnostic mask preview isn't an asset worth cataloging."""
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-mask-bytes")

    job = jobs.create_job()
    service.run_render_job(job.id, fake_render, tmp_path / "mask.png")

    assert jobs.get_job(job.id).status is jobs.JobStatus.DONE
    assert asset_library.list_assets() == []


def test_render_job_library_failure_does_not_flip_job_to_error(monkeypatch, tmp_path):
    """Cataloging is best-effort: the render already succeeded and its file
    already exists, so a library-side problem must not hide that from the caller."""
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-video-bytes")

    def fail_add(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(asset_library, "add", fail_add)

    output = tmp_path / "out.mp4"
    job = jobs.create_job()
    service.run_render_job(job.id, fake_render, output, library_kind="generated")

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE
    assert output.read_bytes() == b"fake-video-bytes"


def _ace_response(payload):
    """Minimal stand-in for the httpx.Response that call_optional_service returns."""
    class _Resp:
        content = b"audio-bytes"

        def json(self):
            return payload

    return _Resp()


@pytest.mark.anyio
async def test_music_job_reports_remote_failure(monkeypatch, tmp_path, acestep_settings):
    """ACE-Step status == 2 means it gave up -- surface that as a job error
    rather than polling until the timeout."""
    monkeypatch.setattr(service, "MUSIC_POLL_INTERVAL_SECONDS", 0)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        return _ace_response({"data": [{"status": 2, "result": None}]})

    job = jobs.create_job()
    await service.run_music_job(
        job.id, tmp_path / "out.mp3",
        settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        call_service=fake_call,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.ERROR
    assert "failure" in result.error.lower()


@pytest.mark.anyio
async def test_music_job_times_out_when_never_ready(monkeypatch, tmp_path, acestep_settings):
    """status == 0 forever: the loop must give up rather than hang."""
    monkeypatch.setattr(service, "MUSIC_POLL_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(service, "MUSIC_POLL_MAX_ATTEMPTS", 3)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        return _ace_response({"data": [{"status": 0, "result": None}]})

    job = jobs.create_job()
    await service.run_music_job(
        job.id, tmp_path / "out.mp3",
        settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        call_service=fake_call,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.ERROR
    assert "timed out" in result.error.lower()


@pytest.mark.anyio
async def test_music_job_downloads_audio_on_success(monkeypatch, tmp_path, acestep_settings):
    """The happy path, which has never been exercised against a live
    ACE-Step server -- this at least pins the response-envelope parsing
    (data[0].result is a JSON *string* holding a list)."""
    monkeypatch.setattr(service, "MUSIC_POLL_INTERVAL_SECONDS", 0)

    async def fake_call(svc, method, path, **kwargs):
        if path == "/release_task":
            return _ace_response({"data": {"task_id": "t1"}})
        if path == "/query_result":
            return _ace_response(
                {"data": [{"status": 1, "result": json.dumps([{"file": "/v1/audio/t1.mp3"}])}]}
            )
        return _ace_response({})  # the audio download

    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        library_kind="generated", call_service=fake_call,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"audio-bytes"

    [asset] = asset_library.list_assets()
    assert asset.kind == "generated"
    assert asset.tags == ["music"]
    assert asset.provenance == {"prompt": "p", "lyrics": "", "duration": 10.0, "thinking": False}


@pytest.mark.anyio
async def test_sound_effect_job_downloads_audio_and_registers_in_library(tmp_path):
    async def fake_call(svc, method, path, **kwargs):
        class _Resp:
            content = b"sfx-bytes"

        return _Resp()

    output = tmp_path / "out.wav"
    job = jobs.create_job()
    await service.run_sound_effect_job(
        job.id, output, settings=Settings(sound_effects_url="http://sfx.invalid"),
        prompt="gentle wind chimes", duration=8.0, library_kind="generated", call_service=fake_call,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"sfx-bytes"

    [asset] = asset_library.list_assets()
    assert asset.kind == "generated"
    assert asset.tags == ["sound-effect"]
    assert asset.provenance == {"prompt": "gentle wind chimes", "duration": 8.0}


@pytest.mark.anyio
async def test_image_job_downloads_image_and_registers_in_library(tmp_path):
    async def fake_call(svc, method, path, **kwargs):
        class _Resp:
            content = b"fake-png-bytes"

        return _Resp()

    output = tmp_path / "out.png"
    job = jobs.create_job()
    await service.run_image_job(
        job.id, output, settings=Settings(image_generation_url="http://img.invalid"),
        prompt="a lofi bedroom at sunset", library_kind="generated", call_service=fake_call,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"fake-png-bytes"

    [asset] = asset_library.list_assets()
    assert asset.kind == "generated"
    assert asset.tags == ["image"]
    assert asset.provenance == {"prompt": "a lofi bedroom at sunset"}


@pytest.mark.anyio
async def test_semantic_mask_job_writes_mask_then_renders(tmp_path, test_photo):
    """The chain: segment -> save mask -> render with it. Asserts the mask is
    kept on disk (so a bad result can be diagnosed) and is actually passed
    through to the pipeline rather than silently dropped."""
    settings = Settings(ml_service_url="http://ml.invalid")

    async def fake_call(svc, method, path, **kwargs):
        assert path == "/segment"
        assert kwargs["data"]["prompt"] == "sky"

        class _Resp:
            content = b"fake-png-bytes"

        return _Resp()

    seen = {}

    def fake_render(**kwargs):
        seen.update(kwargs)
        Path(kwargs["output_path"]).write_bytes(b"fake-video-bytes")

    mask_path = tmp_path / "mask.png"
    output_path = tmp_path / "out.mp4"
    job = jobs.create_job()
    await service.run_photo_semantic_mask_job(
        job.id, output_path, settings=settings,
        photo_path=Path(test_photo), mask_path=mask_path, mask_prompt="sky", effect=["dust"],
        library_kind="generated", library_tags=["photo", "dust"],
        call_service=fake_call, render_fn=fake_render,
    )

    assert jobs.get_job(job.id).status is jobs.JobStatus.DONE
    assert mask_path.read_bytes() == b"fake-png-bytes"
    assert seen["mask_path"] == str(mask_path)
    assert seen["effect"] == ["dust"]

    [asset] = asset_library.list_assets()
    assert asset.kind == "generated"
    assert asset.tags == ["photo", "dust"]
    assert asset.provenance == {"mask_prompt": "sky", "effect": ["dust"]}


@pytest.mark.anyio
async def test_semantic_mask_job_errors_when_service_absent(tmp_path, test_photo):
    """No ml_service_url configured: the job must fail cleanly, and must NOT
    fall back to rendering unmasked (that would quietly produce something
    other than what was asked for)."""
    settings = Settings(ml_service_url=None)

    def fail_render(**kwargs):
        raise AssertionError("render must not run when segmentation is unavailable")

    job = jobs.create_job()
    await service.run_photo_semantic_mask_job(
        job.id, tmp_path / "out.mp4", settings=settings,
        photo_path=Path(test_photo), mask_path=tmp_path / "mask.png",
        mask_prompt="sky", effect=["dust"], render_fn=fail_render,
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.ERROR
    assert "not configured" in result.error.lower() or "unavailable" in result.error.lower()
