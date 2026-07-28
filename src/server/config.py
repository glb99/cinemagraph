"""Server configuration, declared once and injected.

Replaces three different ad-hoc ways of reading os.environ that had grown up
across this package (a module-level constant frozen at import, a helper
re-reading on every call, and per-request lookups by magic string). Those
inconsistencies were load-bearing in one place -- tests had to
`importlib.reload(server.app)` just to change CINEMAGRAPH_DATA_DIR, because
it was captured at import time -- which is exactly what FastAPI's documented
settings-via-dependency pattern exists to avoid.

Scope note: this deliberately does NOT cover CINEMAGRAPH_LIBRARY_DIR, which
`cinemagraph.library` reads itself. That module is core-tier, and core's
dependencies are opencv/numpy/click/imageio -- `pydantic` only arrives with
the `server` extra, so importing settings there would drag a server-tier
dependency into the core library and break the tier discipline in
docs/DESIGN.md sec 3.2. Two config mechanisms instead of three is the
correct outcome here, not a half-finished migration.
"""
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


@dataclass(frozen=True)
class OptionalService:
    """Everything needed to call one optional external service *and* to
    explain its absence: where it is, what to call it in an error message,
    and which env var configures it.

    Bundled because these three facts were previously repeated together at
    every call site (`run_music_job` alone passed "ACESTEP_URL" and
    "Music generation" three separate times), and because a missing URL and
    an unreachable service produce the same user-visible outcome -- so the
    thing that formats that message needs all three in one place.
    """

    url: str | None
    name: str
    env_var: str


class Settings(BaseSettings):
    """Server-tier settings. Field names map to upper-cased env vars
    automatically (ml_service_url -> ML_SERVICE_URL), except data_dir, which
    keeps its established CINEMAGRAPH_ prefix via an explicit alias rather
    than being silently renamed.
    """

    model_config = SettingsConfigDict(extra="ignore")

    data_dir: Path = Field(Path("./data"), validation_alias="CINEMAGRAPH_DATA_DIR")
    ml_service_url: str | None = None
    acestep_url: str | None = None
    sound_effects_url: str | None = None

    @field_validator("data_dir")
    @classmethod
    def _resolve_data_dir(cls, value: Path) -> Path:
        # Matches the previous module-level `.resolve()`; job directories are
        # created underneath it, so a relative path here would resolve
        # against whatever cwd the worker happened to have.
        return value.resolve()

    @property
    def semantic_mask_service(self) -> OptionalService:
        return OptionalService(self.ml_service_url, "Semantic masking", "ML_SERVICE_URL")

    @property
    def music_service(self) -> OptionalService:
        return OptionalService(self.acestep_url, "Music generation", "ACESTEP_URL")

    @property
    def sound_effect_service(self) -> OptionalService:
        return OptionalService(self.sound_effects_url, "Sound effect generation", "SOUND_EFFECTS_URL")


@lru_cache
def get_settings() -> Settings:
    """Cached so the environment is read once per process rather than per
    request. Tests override this via `app.dependency_overrides[get_settings]`,
    which takes precedence over the cache -- the documented FastAPI pattern,
    and the reason the `importlib.reload` dance is no longer needed.

    Caching config does not weaken the "degrade when a satellite is absent"
    guarantee: that check is an actual HTTP call (see _external_service), so
    a service going down or coming back is still noticed per request. Only
    the *configuration* is cached, and that genuinely is fixed for a process
    lifetime -- env vars are set before the process starts.
    """
    return Settings()
