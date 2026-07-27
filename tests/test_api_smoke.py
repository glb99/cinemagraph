"""TestClient smoke tests for the API layer. FastAPI's TestClient runs
BackgroundTasks synchronously as part of the request/response cycle, so by
the time a /render/* call returns, the job has already finished -- no
polling needed here, unlike a real deployment.
"""
import importlib

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    monkeypatch.setenv("CINEMAGRAPH_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(tmp_path / "library"))
    import api.app as app_module

    importlib.reload(app_module)  # re-read CINEMAGRAPH_DATA_DIR for this test's tmp_path
    with TestClient(app_module.app) as client:
        yield client


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_capabilities_without_optional_services_configured(api_client):
    resp = api_client.get("/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {
        "semantic_mask": False,
        "music_generation": False,
        "sound_effect_generation": False,
    }


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
