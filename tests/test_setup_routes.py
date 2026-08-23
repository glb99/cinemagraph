"""POST /setup/gemini-key, and the dotenv writer behind it.

The route's contract is "stored, not yet in effect": get_settings() is cached
for the process lifetime by design (see server/config.py), so these tests
assert on what lands in the file, never on the running app's own settings
changing.
"""

import pytest
from fastapi.testclient import TestClient

from server.app import app
from server.config import Settings, get_settings
from server.env_file import EnvFileError, env_var_is_set, set_env_var


@pytest.fixture
def api_client(tmp_path):
    app.dependency_overrides[get_settings] = lambda: Settings(
        data_dir=tmp_path / "data",
        frontend_dist_dir=tmp_path / "frontend-dist",
        env_file=tmp_path / ".env",
    )
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_stores_a_key_and_reports_it_set(api_client, tmp_path):
    response = api_client.post("/setup/gemini-key", json={"api_key": "AIza-test_123"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_set"] is True
    assert body["restart_required"] is True
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "GEMINI_API_KEY=AIza-test_123\n"


def test_never_returns_the_key_itself(api_client):
    """The response says whether one is set, never what it is -- the same rule
    `cinemagraph doctor` follows, and the reason no GET counterpart exists."""
    secret = "AIza-secret_value"
    response = api_client.post("/setup/gemini-key", json={"api_key": secret})

    assert secret not in response.text


def test_empty_value_removes_the_assignment(api_client, tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTHER=keep\nGEMINI_API_KEY=old\n", encoding="utf-8")

    response = api_client.post("/setup/gemini-key", json={"api_key": ""})

    assert response.json()["is_set"] is False
    assert env.read_text(encoding="utf-8") == "OTHER=keep\n"


def test_rejects_a_value_that_would_need_quoting(api_client, tmp_path):
    response = api_client.post("/setup/gemini-key", json={"api_key": "has spaces"})

    assert response.status_code == 422
    # Nothing written: a refused value must not leave a half-applied file.
    assert not (tmp_path / ".env").exists()


def test_preserves_other_variables_and_key_position(tmp_path):
    env = tmp_path / ".env"
    env.write_text(
        "# comment\nGEMINI_API_KEY=old\nACESTEP_URL=http://localhost:8001\n",
        encoding="utf-8",
    )

    set_env_var(env, "GEMINI_API_KEY", "new")

    assert env.read_text(encoding="utf-8") == (
        "# comment\nGEMINI_API_KEY=new\nACESTEP_URL=http://localhost:8001\n"
    )


def test_collapses_duplicate_assignments(tmp_path):
    """Only the last assignment would have taken effect anyway, so leaving a
    stale earlier one behind is a trap rather than a courtesy."""
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=one\nOTHER=x\nGEMINI_API_KEY=two\n", encoding="utf-8")

    set_env_var(env, "GEMINI_API_KEY", "three")

    assert env.read_text(encoding="utf-8") == "GEMINI_API_KEY=three\nOTHER=x\n"


def test_reads_an_exported_assignment(tmp_path):
    """`export FOO=` is common in hand-written .env files even though Compose
    ignores the prefix, so it has to count as set."""
    env = tmp_path / ".env"
    env.write_text("export GEMINI_API_KEY=value\n", encoding="utf-8")

    assert env_var_is_set(env, "GEMINI_API_KEY") is True


def test_blank_assignment_is_not_set(tmp_path):
    env = tmp_path / ".env"
    env.write_text("GEMINI_API_KEY=\n", encoding="utf-8")

    assert env_var_is_set(env, "GEMINI_API_KEY") is False


def test_rejects_an_invalid_variable_name(tmp_path):
    with pytest.raises(EnvFileError):
        set_env_var(tmp_path / ".env", "not-a-valid-name", "x")


def test_creates_the_file_when_absent(tmp_path):
    env = tmp_path / "nested" / ".env"

    set_env_var(env, "GEMINI_API_KEY", "k")

    assert env.read_text(encoding="utf-8") == "GEMINI_API_KEY=k\n"
