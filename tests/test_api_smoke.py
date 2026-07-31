"""TestClient smoke tests for the API layer. FastAPI's TestClient runs
BackgroundTasks synchronously as part of the request/response cycle, so by
the time a /render/* call returns, the job has already finished -- no
polling needed here, unlike a real deployment.
"""
import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.config import Settings, get_settings


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    # CINEMAGRAPH_LIBRARY_DIR is read by the asset_library package directly
    # (not part of server.config.Settings -- see config.py's module docstring),
    # so it's still overridden via the environment. data_dir goes through the
    # dependency_overrides below instead, which is why this no longer needs
    # importlib.reload(app_module): DATA_DIR used to be a module-level constant
    # frozen at import time, so changing the env var after import had no effect
    # without reloading the whole module. Settings is resolved per-request via
    # Depends(get_settings), and dependency_overrides beats the @lru_cache.
    monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(tmp_path / "library"))
    app.dependency_overrides[get_settings] = lambda: Settings(data_dir=tmp_path / "data")
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_index_serves_the_web_ui(api_client):
    resp = api_client.get("/")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")
    assert "cinemagraph-tool" in resp.text
    assert 'id="photo-form"' in resp.text
    assert 'id="video-form"' in resp.text


def test_capabilities_without_optional_services_configured(api_client):
    resp = api_client.get("/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {
        "semantic_mask": False,
        "music_generation": False,
        "sound_effect_generation": False,
        "image_generation": False,
        "image_generation_models": ["sdxl"],
        "configured": {
            "semantic_mask": False,
            "music_generation": False,
            "sound_effect_generation": False,
            "image_generation": False,
        },
    }


def test_capabilities_distinguishes_configured_but_unreachable(api_client, tmp_path):
    """A service whose URL is set but which doesn't actually answer /health
    (e.g. its container isn't running) should show up as unavailable *and*
    configured -- the combination the web UI uses to show a hint instead of
    hiding the tab. Distinct from the "nothing configured" case, where
    configured stays False too (see ui.py's loadCapabilities)."""
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_dir=tmp_path / "data", acestep_url="http://acestep.invalid:9"
    )
    resp = api_client.get("/capabilities")
    body = resp.json()
    assert body["music_generation"] is False
    assert body["configured"]["music_generation"] is True
    assert body["configured"]["sound_effect_generation"] is False


def test_list_effects(api_client):
    resp = api_client.get("/effects")
    assert resp.status_code == 200
    effects = resp.json()["effects"]
    assert "ripple" in effects
    assert "dust" in effects


def test_render_photo_then_job_status_and_download(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={"input_file": ("photo.jpg", f, "image/jpeg")},
            data={"effect": ["dust", "ripple"], "duration": "1.0", "fps": "10"},
        )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "done", status
    assert status["output_path"]

    file_resp = api_client.get(f"/jobs/{job_id}/file")
    assert file_resp.status_code == 200
    assert len(file_resp.content) > 0

    # The render's output is auto-catalogued in the asset library (kind="generated"),
    # tagged by media type and requested effect(s) -- this is what makes a finished
    # render findable again later instead of only existing as a job-scoped file.
    library = api_client.get("/library").json()
    assert len(library) == 1
    assert library[0]["kind"] == "generated"
    assert set(library[0]["tags"]) == {"photo", "dust", "ripple"}


def test_render_video_then_job_status(api_client, test_video):
    with open(test_video, "rb") as f:
        resp = api_client.post(
            "/render/video",
            files={"input_file": ("input.mp4", f, "video/mp4")},
        )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "done", status

    library = api_client.get("/library").json()
    assert len(library) == 1
    assert library[0]["kind"] == "generated"
    assert library[0]["tags"] == ["video"]


def _hand_painted_mask_bytes(w=160, h=100):
    """Same fixture-building approach as tests/test_mask.py's contract test."""
    import cv2
    import numpy as np

    mask = np.zeros((h, w), dtype=np.uint8)
    mask[10:30, 20:60] = 255
    ok, encoded = cv2.imencode(".png", mask)
    assert ok
    return encoded.tobytes()


def test_render_video_accepts_uploaded_mask_and_loop_controls(api_client, test_video):
    """Parity check: still_frame_index/blend_frames/auto_trim/mask upload were
    CLI-only (`make`) until this was added to /render/video."""
    with open(test_video, "rb") as f:
        resp = api_client.post(
            "/render/video",
            files={
                "input_file": ("input.mp4", f, "video/mp4"),
                "mask": ("mask.png", _hand_painted_mask_bytes(), "image/png"),
            },
            data={"still_frame_index": "0", "blend_frames": "5", "auto_trim": "false"},
        )
    assert resp.status_code == 200, resp.text
    status = api_client.get(f"/jobs/{resp.json()['job_id']}").json()
    assert status["status"] == "done", status


def test_render_video_rejects_loop_duration_with_gif(api_client, test_video):
    with open(test_video, "rb") as f:
        resp = api_client.post(
            "/render/video",
            files={"input_file": ("input.mp4", f, "video/mp4")},
            data={"also_gif": "true", "loop_duration": "60"},
        )
    assert resp.status_code == 422


def test_render_photo_accepts_uploaded_mask_and_per_effect_overrides(api_client, test_photo):
    """Parity check: a hand-painted mask and per-effect overrides (--rain-count
    etc. on the CLI) were previously only reachable through the CLI."""
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={
                "input_file": ("photo.jpg", f, "image/jpeg"),
                "mask": ("mask.png", _hand_painted_mask_bytes(480, 320), "image/png"),
            },
            data={"effect": ["dust"], "duration": "1.0", "fps": "10", "dust_count": "5"},
        )
    assert resp.status_code == 200, resp.text
    status = api_client.get(f"/jobs/{resp.json()['job_id']}").json()
    assert status["status"] == "done", status


def test_render_photo_rejects_override_for_effect_not_requested(api_client, test_photo):
    """Same rule the CLI enforces via validation.resolve_effect_kwargs:
    --rain-count only makes sense alongside --effect rain."""
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={"input_file": ("photo.jpg", f, "image/jpeg")},
            data={"effect": ["dust"], "rain_count": "150"},
        )
    assert resp.status_code == 422
    assert "rain" in resp.text


def test_render_photo_rejects_mask_and_mask_prompt_together(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={
                "input_file": ("photo.jpg", f, "image/jpeg"),
                "mask": ("mask.png", _hand_painted_mask_bytes(480, 320), "image/png"),
            },
            data={"effect": ["dust"], "mask_prompt": "sky"},
        )
    assert resp.status_code == 422


def test_render_photo_rejects_loop_duration_with_gif(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={"input_file": ("photo.jpg", f, "image/jpeg")},
            data={"effect": ["dust"], "also_gif": "true", "loop_duration": "60"},
        )
    assert resp.status_code == 422


def test_mask_preview_then_job_status_and_download(api_client, test_video):
    """API equivalent of `cinemagraph mask-preview`."""
    with open(test_video, "rb") as f:
        resp = api_client.post(
            "/mask-preview",
            files={"input_file": ("input.mp4", f, "video/mp4")},
        )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "done", status

    file_resp = api_client.get(f"/jobs/{job_id}/file")
    assert file_resp.status_code == 200

    # A mask preview is a diagnostic aid, not an asset worth cataloging --
    # unlike /render/photo and /render/video, it must NOT show up in the library.
    assert api_client.get("/library").json() == []
    assert file_resp.content[:8] == b"\x89PNG\r\n\x1a\n"  # PNG magic bytes


def test_render_photo_rejects_unknown_effect(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/render/photo",
            files={"input_file": ("photo.jpg", f, "image/jpeg")},
            data={"effect": ["not_a_real_effect"]},
        )
    assert resp.status_code == 422


def test_unknown_job_id_returns_404(api_client):
    resp = api_client.get("/jobs/does-not-exist")
    assert resp.status_code == 404


def test_semantic_mask_without_sidecar_returns_503(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/mask/semantic",
            files={"image": ("photo.jpg", f, "image/jpeg")},
            data={"prompt": "water"},
        )
    assert resp.status_code == 503


def test_generate_music_without_acestep_configured_reports_job_error(api_client):
    """/generate/music always returns 200 with a job_id (per the job-queue
    contract) -- the "service unavailable" outcome shows up as that job's
    status, not as an HTTP error on the initial request. TestClient runs
    BackgroundTasks synchronously, so the job has already failed by the
    time this call returns."""
    resp = api_client.post("/generate/music", data={"prompt": "ambient synth pad"})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "error", status
    assert "ACESTEP_URL" in status["error"]


def test_generate_sound_effect_without_service_configured_reports_job_error(api_client):
    """Same shape as the music-generation test above -- see that test's
    docstring. Validated for real against a live sound-effects/ instance
    outside the test suite (GPU required, not available in CI); see
    docs/experiments/."""
    resp = api_client.post("/generate/sound-effect", data={"prompt": "gentle wind chimes"})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "error", status
    assert "SOUND_EFFECTS_URL" in status["error"]


def test_generate_image_without_service_configured_reports_job_error(api_client):
    """Same shape as the other two "not configured" tests above -- proxies
    to the local image-generation/ service (SDXL), degrading the same way
    when IMAGE_GENERATION_URL isn't configured."""
    resp = api_client.post("/generate/image", data={"prompt": "a lofi bedroom at sunset"})
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "error", status
    assert "IMAGE_GENERATION_URL" in status["error"]


def test_generate_image_rejects_unknown_model(api_client):
    """`model` is validated against generation_registry.available_image_generators()
    before a job is even created -- an unregistered name is a 422, the same
    "reject at the route" pattern /render/photo already uses for unknown effects."""
    resp = api_client.post(
        "/generate/image", data={"prompt": "a lofi bedroom at sunset", "model": "does-not-exist"}
    )
    assert resp.status_code == 422
    assert "does-not-exist" in resp.json()["detail"]


def test_generate_image_uses_requested_model(api_client):
    """A registered adapter other than the default ("sdxl") is actually used
    when named via `model` -- the real per-request selection path (see
    run_image_job's docstring)."""
    from server import generation_registry

    class FakeGenerator:
        async def generate(self, prompt, **kwargs):
            return b"fake-png-bytes-from-route-test"

    generation_registry.register_image_generator("test-route-fake", FakeGenerator())
    try:
        resp = api_client.post(
            "/generate/image",
            data={"prompt": "a lofi bedroom at sunset", "model": "test-route-fake"},
        )
        assert resp.status_code == 200, resp.text
        job_id = resp.json()["job_id"]

        status = api_client.get(f"/jobs/{job_id}").json()
        assert status["status"] == "done", status
    finally:
        del generation_registry._IMAGE_GENERATORS["test-route-fake"]


def test_library_add_list_get_file_and_remove(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/library",
            files={"upload": ("photo.jpg", f, "image/jpeg")},
            data={"kind": "reference", "tags": "sky, concept"},
        )
    assert resp.status_code == 200, resp.text
    added = resp.json()
    assert added["original_filename"] == "photo.jpg"
    assert set(added["tags"]) == {"sky", "concept"}
    asset_id = added["id"]

    listed = api_client.get("/library").json()
    assert any(a["id"] == asset_id for a in listed)

    fetched = api_client.get(f"/library/{asset_id}")
    assert fetched.status_code == 200
    assert fetched.json() == added

    file_resp = api_client.get(f"/library/{asset_id}/file")
    assert file_resp.status_code == 200
    assert len(file_resp.content) > 0

    delete_resp = api_client.delete(f"/library/{asset_id}")
    assert delete_resp.status_code == 200

    assert api_client.get(f"/library/{asset_id}").status_code == 404


def test_library_add_rejects_unknown_kind(api_client, test_photo):
    with open(test_photo, "rb") as f:
        resp = api_client.post(
            "/library",
            files={"upload": ("photo.jpg", f, "image/jpeg")},
            data={"kind": "not_a_real_kind"},
        )
    assert resp.status_code == 422


def test_library_get_and_remove_missing_asset_return_404(api_client):
    assert api_client.get("/library/does-not-exist").status_code == 404
    assert api_client.delete("/library/does-not-exist").status_code == 404


def test_library_list_filters_by_kind(api_client, test_photo, test_video):
    with open(test_photo, "rb") as f:
        api_client.post("/library", files={"upload": ("photo.jpg", f, "image/jpeg")}, data={"kind": "reference"})
    with open(test_video, "rb") as f:
        api_client.post("/library", files={"upload": ("input.mp4", f, "video/mp4")}, data={"kind": "source"})

    result = api_client.get("/library", params={"kind": "source"}).json()
    assert len(result) == 1
    assert result[0]["kind"] == "source"


def test_assemble_rejects_unknown_asset_id(api_client):
    """Asset ids are validated eagerly at the route, same as /render/photo's
    unknown-effect check -- a bad id is a 422 before any job/ffmpeg work
    starts, not an opaque failure deep inside a background job."""
    resp = api_client.post(
        "/assemble",
        data={"clip_asset_ids": ["does-not-exist"], "music_asset_ids": ["also-missing"]},
    )
    assert resp.status_code == 422
    assert "does-not-exist" in resp.json()["detail"]


def test_assemble_combines_real_assets_into_one_valid_file(api_client, test_video, tmp_path):
    """Real end-to-end smoke test, matching test_pipeline_smoke.py's own
    convention (real synthetic fixtures through the real pipeline, not
    mocked) -- run_assembly_job's `run_ffmpeg` default is bound at
    definition time, so it can't be faked through an HTTP round trip the
    way generation_registry's adapters can; a tiny real ffmpeg call is fast
    enough here to be a genuine smoke test rather than a slow one."""
    import subprocess

    import imageio_ffmpeg

    import asset_library

    audio_path = tmp_path / "tone.mp3"
    exe = imageio_ffmpeg.get_ffmpeg_exe()
    subprocess.run(
        [exe, "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=1", str(audio_path)],
        capture_output=True,
    )

    clip_asset = asset_library.add(test_video, kind="generated", tags=["photo"])
    music_asset = asset_library.add(str(audio_path), kind="generated", tags=["music"])

    resp = api_client.post(
        "/assemble",
        data={"clip_asset_ids": [clip_asset.id], "music_asset_ids": [music_asset.id]},
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["job_id"]

    status = api_client.get(f"/jobs/{job_id}").json()
    assert status["status"] == "done", status

    file_resp = api_client.get(f"/jobs/{job_id}/file")
    assert file_resp.status_code == 200
    assert len(file_resp.content) > 0

    library_result = api_client.get("/library", params={"tag": "assembled"}).json()
    assert len(library_result) == 1
    assert library_result[0]["provenance"]["clip_asset_ids"] == [clip_asset.id]
    assert library_result[0]["provenance"]["music_asset_ids"] == [music_asset.id]
