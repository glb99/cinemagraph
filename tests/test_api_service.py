"""Unit tests for server/service.py's workflows.

These call the job functions directly rather than through a route -- lets
each job's own orchestration (mark_running/done/error, library
registration, provenance) be tested without an HTTP round-trip or a real
external service. Backend-specific request/response mechanics (ACE-Step's
polling loop, SDXL's img2img request shape, Gemini's multimodal input) live
one level down in generation_adapters.py, tested directly in
test_generation_ports.py instead -- these tests inject a fake
port (ImageGenerator/MusicGenerator/SoundEffectGenerator) rather than a fake
HTTP call, since that's the seam this module's jobs actually depend on.

Settings are constructed directly (Settings(acestep_url=...)) rather than
via monkeypatch.setenv, since service.py now takes settings as an explicit
argument instead of reading the environment itself -- config is resolved
once by the route (via Depends(get_settings)) and passed through.
"""
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


def _save(job_id):
    """Every job function now only *stages* a save candidate on completion
    (jobs.py's pending_library) -- nothing reaches asset_library.add() until
    this, service.save_job_to_library()'s own real code path, is called.
    Tests that want to assert on the resulting Asset (kind/tags/provenance)
    go through this rather than reading asset_library.list_assets()
    immediately after the job function returns, matching the real two-step
    generate-then-save flow the web UI now uses."""
    return service.save_job_to_library(job_id)


def test_render_job_registers_generated_output_in_library(tmp_path):
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-video-bytes")

    output = tmp_path / "out.mp4"
    job = jobs.create_job()
    service.run_render_job(
        job.id, fake_render, output, library_kind="generated", library_tags=["video"],
    )

    assert jobs.get_job(job.id).status is jobs.JobStatus.DONE
    assert asset_library.list_assets() == []  # staged, not yet saved

    asset = _save(job.id)
    assert asset.kind == "generated"
    assert asset.tags == ["video"]


def test_save_job_to_library_with_project_tags_the_asset(tmp_path):
    """project= is folded into the tag list right before asset_library.add()
    -- see save_job_to_library's own docstring for why project assignment
    happens at this exact call site rather than a separate step."""
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-video-bytes")

    output = tmp_path / "out.mp4"
    job = jobs.create_job()
    service.run_render_job(job.id, fake_render, output, library_kind="generated", library_tags=["video"])

    asset = service.save_job_to_library(job.id, project="sunset-loop")
    assert f"{asset_library.PROJECT_TAG_PREFIX}sunset-loop" in asset.tags
    assert "video" in asset.tags


def test_save_job_to_library_without_project_adds_no_project_tag(tmp_path):
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-video-bytes")

    output = tmp_path / "out.mp4"
    job = jobs.create_job()
    service.run_render_job(job.id, fake_render, output, library_kind="generated")

    asset = service.save_job_to_library(job.id)
    assert not any(t.startswith(asset_library.PROJECT_TAG_PREFIX) for t in asset.tags)


def test_render_job_skips_library_when_kind_not_given(tmp_path):
    """The /mask-preview route reuses run_render_job but never passes
    library_kind -- a diagnostic mask preview isn't an asset worth cataloging.
    Nothing was ever staged for it, so there's genuinely nothing to save --
    not just "library happens to be empty right now" (true of every job
    immediately after completion under the new stage-then-save flow), which
    is why this also confirms save_job_to_library() itself raises."""
    def fake_render(*, output_path, **kwargs):
        Path(output_path).write_bytes(b"fake-mask-bytes")

    job = jobs.create_job()
    service.run_render_job(job.id, fake_render, tmp_path / "mask.png")

    assert jobs.get_job(job.id).status is jobs.JobStatus.DONE
    assert asset_library.list_assets() == []
    with pytest.raises(ValueError, match="Nothing to save"):
        _save(job.id)


def test_render_job_staging_never_touches_asset_library(monkeypatch, tmp_path):
    """Staging (jobs.stage_for_library) is a pure in-memory dict write --
    it must never call asset_library.add() itself, since that call now only
    happens later, explicitly, via save_job_to_library(). Confirmed by
    making asset_library.add() raise: the render job must still complete
    normally, proving it was never even attempted during staging."""
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

    # Unlike the old immediate-registration design, a *real* save failure
    # is no longer silently swallowed -- it's now a deliberate, explicit,
    # user-initiated action (POST /jobs/{id}/save), so a genuine failure
    # here should be reportable, not hidden. This is an intentional
    # behavior change, not an oversight.
    with pytest.raises(OSError, match="disk full"):
        _save(job.id)


@pytest.mark.anyio
async def test_music_job_downloads_audio_and_registers_in_library(tmp_path, acestep_settings):
    """ACE-Step's own polling-loop details (remote failure, timeout, the
    response-envelope parsing) are now ACEStepAdapter's concern, tested
    directly in test_generation_ports.py -- this just confirms run_music_job
    delegates to an injected MusicGenerator and records provenance
    correctly, the same job-level shape run_image_job's tests already use."""
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"audio-bytes"

    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        library_kind="generated", music_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"audio-bytes"

    asset = _save(job.id)
    assert asset.kind == "generated"
    assert asset.tags == ["music"]
    assert asset.provenance == {
        "prompt": "p", "lyrics": "", "model": "acestep",
        "duration": 10.0, "thinking": False, "instrumental": False,
    }


@pytest.mark.anyio
async def test_music_job_provenance_records_submitted_lyrics_not_backend_marker(tmp_path, acestep_settings):
    """Provenance must reflect what the caller actually asked for
    (instrumental=True, original lyrics text) -- not whatever
    backend-internal substitution an adapter makes to achieve it (ACE-Step's
    own "[Instrumental]" marker hack, entirely ACEStepAdapter's concern per
    its own docstring)."""
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"audio-bytes"

    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="p", lyrics="some lyrics I typed",
        duration=10.0, thinking=False, instrumental=True,
        library_kind="generated", music_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error

    asset = _save(job.id)
    assert asset.provenance["lyrics"] == "some lyrics I typed"
    assert asset.provenance["instrumental"] is True


@pytest.mark.anyio
async def test_music_job_reports_generator_error(tmp_path, acestep_settings):
    class FailingGenerator:
        async def generate(self, prompt, **kwargs):
            raise RuntimeError("ACE-Step reported generation failure.")

    job = jobs.create_job()
    await service.run_music_job(
        job.id, tmp_path / "out.mp3",
        settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        music_generator=FailingGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.ERROR
    assert "failure" in result.error.lower()


@pytest.mark.anyio
async def test_music_job_routes_to_remix_for_cover_task_type(tmp_path, acestep_settings):
    """task_type != text2music must call generator.remix(), not generate()
    -- the two are meaningfully different capabilities (see
    generation_ports.py's MusicGenerator.remix() docstring), not just an
    extra kwarg on the same call."""
    calls = []

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            calls.append(("generate", kwargs))
            return b"should-not-be-called"

        async def remix(self, prompt, **kwargs):
            calls.append(("remix", kwargs))
            return b"remixed-audio-bytes"

    source = tmp_path / "source.mp3"
    source.write_bytes(b"original-song-bytes")
    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="jazzier version", lyrics="",
        duration=30.0, thinking=False, task_type="cover", src_audio_path=source,
        cover_strength=0.5, library_kind="generated", music_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"remixed-audio-bytes"

    assert len(calls) == 1
    method, kwargs = calls[0]
    assert method == "remix"
    assert kwargs["task_type"] == "cover"
    assert kwargs["src_audio_bytes"] == b"original-song-bytes"
    assert kwargs["cover_strength"] == 0.5

    asset = _save(job.id)
    assert asset.provenance["task_type"] == "cover"
    assert asset.provenance["source_asset_path"] == str(source)


@pytest.mark.anyio
async def test_music_job_routes_to_remix_for_text2music_with_reference_audio(tmp_path, acestep_settings):
    """Style transfer is independent of task_type -- a plain text2music
    request with a reference track attached must still route to remix(),
    not generate()."""
    calls = []

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            calls.append("generate")
            return b"should-not-be-called"

        async def remix(self, prompt, **kwargs):
            calls.append("remix")
            return b"styled-audio-bytes"

    reference = tmp_path / "reference.mp3"
    reference.write_bytes(b"reference-track-bytes")
    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="a dreamy synth piece", lyrics="",
        duration=30.0, thinking=False, task_type="text2music", reference_audio_path=reference,
        library_kind="generated", music_generator=FakeGenerator(),
    )

    assert calls == ["remix"]
    asset = _save(job.id)
    assert asset.provenance["reference_asset_path"] == str(reference)


@pytest.mark.anyio
async def test_music_job_uses_generate_for_plain_text2music(tmp_path, acestep_settings):
    """Regression check: no task_type override and no reference audio must
    still use generate(), exactly as before remix() existed."""
    calls = []

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            calls.append("generate")
            return b"audio-bytes"

        async def remix(self, prompt, **kwargs):
            calls.append("remix")
            return b"should-not-be-called"

    output = tmp_path / "out.mp3"
    job = jobs.create_job()
    await service.run_music_job(
        job.id, output, settings=acestep_settings, prompt="p", lyrics="", duration=10.0, thinking=False,
        library_kind="generated", music_generator=FakeGenerator(),
    )

    assert calls == ["generate"]
    asset = _save(job.id)
    assert "task_type" not in asset.provenance


@pytest.mark.anyio
async def test_sound_effect_job_downloads_audio_and_registers_in_library(tmp_path):
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"sfx-bytes"

    output = tmp_path / "out.wav"
    job = jobs.create_job()
    await service.run_sound_effect_job(
        job.id, output, settings=Settings(sound_effects_url="http://sfx.invalid"),
        prompt="gentle wind chimes", duration=8.0, library_kind="generated",
        sound_effect_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"sfx-bytes"

    asset = _save(job.id)
    assert asset.kind == "generated"
    assert asset.tags == ["sound-effect"]
    assert asset.provenance == {"prompt": "gentle wind chimes", "duration": 8.0}


@pytest.mark.anyio
async def test_image_job_downloads_image_and_registers_in_library(tmp_path):
    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"fake-png-bytes"

    output = tmp_path / "out.png"
    job = jobs.create_job()
    await service.run_image_job(
        job.id, output, settings=Settings(image_generation_url="http://img.invalid"),
        prompt="a lofi bedroom at sunset", library_kind="generated", image_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert output.read_bytes() == b"fake-png-bytes"

    asset = _save(job.id)
    assert asset.kind == "generated"
    assert asset.tags == ["image"]
    assert asset.provenance == {"prompt": "a lofi bedroom at sunset", "model": "sdxl"}


@pytest.mark.anyio
async def test_image_job_with_reference_image_passes_bytes_and_strength_to_generator(tmp_path):
    """reference_image_path switches the job into img2img mode: the file's
    bytes and filename are read here and handed to the injected
    ImageGenerator alongside strength -- run_image_job no longer builds the
    HTTP request itself (see generation_adapters.SDXLAdapter, sec 3.6)."""
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"fake-reference-image-bytes")
    captured = {}

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            captured["prompt"] = prompt
            captured["kwargs"] = kwargs
            return b"fake-png-bytes"

    output = tmp_path / "out.png"
    job = jobs.create_job()
    await service.run_image_job(
        job.id, output, settings=Settings(image_generation_url="http://img.invalid"),
        prompt="a lofi bedroom at sunset", reference_image_path=reference, strength=0.4,
        library_kind="generated", image_generator=FakeGenerator(),
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.DONE, result.error
    assert captured["prompt"] == "a lofi bedroom at sunset"
    assert captured["kwargs"] == {
        "reference_image_bytes": b"fake-reference-image-bytes",
        "reference_image_filename": "reference.png",
        "strength": 0.4,
    }

    asset = _save(job.id)
    assert asset.provenance == {"prompt": "a lofi bedroom at sunset", "model": "sdxl", "strength": 0.4}


@pytest.mark.anyio
async def test_image_job_resolves_generator_from_registry_by_model_when_none_injected(tmp_path):
    """Without an explicit image_generator=, run_image_job looks the `model`
    name up in generation_registry -- the actual per-request model-selection
    path real requests go through (app.py never passes image_generator=
    itself), as opposed to every other test here which bypasses it via a
    directly-injected fake."""
    from server import generation_registry

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"fake-from-registry-bytes"

    generation_registry.register_image_generator("test-registry-fake", FakeGenerator())
    try:
        output = tmp_path / "out.png"
        job = jobs.create_job()
        await service.run_image_job(
            job.id, output, settings=Settings(),
            prompt="a lofi bedroom at sunset", model="test-registry-fake",
            library_kind="generated",
        )

        result = jobs.get_job(job.id)
        assert result.status is jobs.JobStatus.DONE, result.error
        assert output.read_bytes() == b"fake-from-registry-bytes"

        asset = _save(job.id)
        assert asset.provenance["model"] == "test-registry-fake"
    finally:
        del generation_registry._IMAGE_GENERATORS["test-registry-fake"]


@pytest.mark.anyio
async def test_image_job_errors_cleanly_for_unregistered_model(tmp_path):
    """A model name unregistered by the time the background job actually
    runs (registry state can't change between the route's own validation and
    the job executing, but this is the last line of defense) should mark the
    job as errored, not raise uncaught out of a BackgroundTask."""
    output = tmp_path / "out.png"
    job = jobs.create_job()
    await service.run_image_job(
        job.id, output, settings=Settings(),
        prompt="a lofi bedroom at sunset", model="does-not-exist",
        library_kind="generated",
    )

    result = jobs.get_job(job.id)
    assert result.status is jobs.JobStatus.ERROR
    assert "does-not-exist" in result.error


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

    asset = _save(job.id)
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
