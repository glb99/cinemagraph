"""Contract tests for server/config.py: env-var mapping, defaults, and the
OptionalService properties that bundle url/name/env_var for error messages.
"""
from pathlib import Path

from server.config import Settings


def test_defaults_when_nothing_configured(monkeypatch):
    monkeypatch.delenv("CINEMAGRAPH_DATA_DIR", raising=False)
    monkeypatch.delenv("ML_SERVICE_URL", raising=False)
    monkeypatch.delenv("ACESTEP_URL", raising=False)
    monkeypatch.delenv("SOUND_EFFECTS_URL", raising=False)

    settings = Settings()
    assert settings.data_dir == Path("./data").resolve()
    assert settings.ml_service_url is None
    assert settings.acestep_url is None
    assert settings.sound_effects_url is None


def test_reads_the_established_env_var_names(monkeypatch):
    """Field names map to upper-cased env vars automatically, except
    data_dir, which keeps its CINEMAGRAPH_ prefix via an explicit alias."""
    monkeypatch.setenv("CINEMAGRAPH_DATA_DIR", "/tmp/somewhere")
    monkeypatch.setenv("ML_SERVICE_URL", "http://ml.example")
    monkeypatch.setenv("ACESTEP_URL", "http://acestep.example")
    monkeypatch.setenv("SOUND_EFFECTS_URL", "http://sfx.example")

    settings = Settings()
    assert settings.data_dir == Path("/tmp/somewhere").resolve()
    assert settings.ml_service_url == "http://ml.example"
    assert settings.acestep_url == "http://acestep.example"
    assert settings.sound_effects_url == "http://sfx.example"


def test_constructor_kwargs_override_environment(monkeypatch):
    """Tests rely on this: Settings(acestep_url=...) must win over whatever
    happens to be in the real environment, so tests stay isolated without
    needing monkeypatch.setenv for every field."""
    monkeypatch.setenv("ACESTEP_URL", "http://from-env.example")
    settings = Settings(acestep_url="http://from-constructor.example")
    assert settings.acestep_url == "http://from-constructor.example"


def test_optional_service_bundles_url_name_and_env_var():
    settings = Settings(acestep_url="http://acestep.example")
    service = settings.music_service
    assert service.url == "http://acestep.example"
    assert service.name == "Music generation"
    assert service.env_var == "ACESTEP_URL"


def test_optional_service_reflects_unset_url():
    settings = Settings(ml_service_url=None)
    service = settings.semantic_mask_service
    assert service.url is None
    assert service.env_var == "ML_SERVICE_URL"
