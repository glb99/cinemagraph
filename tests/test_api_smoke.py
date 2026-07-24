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
    import api.app as app_module

    importlib.reload(app_module)  # re-read CINEMAGRAPH_DATA_DIR for this test's tmp_path
    with TestClient(app_module.app) as client:
        yield client


def test_health(api_client):
    resp = api_client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_capabilities_without_sidecar_configured(api_client):
    resp = api_client.get("/capabilities")
    assert resp.status_code == 200
    assert resp.json() == {"semantic_mask": False}


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
