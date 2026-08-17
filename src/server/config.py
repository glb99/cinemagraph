"""Server configuration, declared once and injected.

Replaces three different ad-hoc ways of reading os.environ that had grown up
across this package (a module-level constant frozen at import, a helper
re-reading on every call, and per-request lookups by magic string). Those
inconsistencies were load-bearing in one place -- tests had to
`importlib.reload(server.app)` just to change CINEMAGRAPH_DATA_DIR, because
it was captured at import time -- which is exactly what FastAPI's documented
settings-via-dependency pattern exists to avoid.

Scope note: this deliberately does NOT cover CINEMAGRAPH_LIBRARY_DIR, which
the `asset_library` package reads itself. That package has zero dependencies
beyond the stdlib by design (it needs to work from the CLI alone, and from
any future service, with no server extras installed) -- `pydantic` only
arrives with the `server` extra, so importing settings there would drag a
server-tier dependency into a package that's meant to stay usable standalone,
breaking the tier discipline in docs/DESIGN.md sec 3.2. Two config mechanisms
instead of three is the correct outcome here, not a half-finished migration.
"""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# src/server/config.py -> src/server -> src -> repo root. Only meaningful for a
# source checkout (editable install or `uv run`); in a real installed package
# this points somewhere that simply doesn't exist, which is exactly what the
# frontend_dist_dir default below is written to tolerate.
_REPO_ROOT = Path(__file__).resolve().parents[2]


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

    # populate_by_name is load-bearing, not boilerplate: setting a
    # validation_alias on a field otherwise makes that field *only* settable
    # by its alias, and extra="ignore" then swallows the field-name form
    # without a word. `Settings(data_dir=tmp_path)` -- which every test
    # fixture and dependency_overrides call in this repo uses -- was
    # therefore a silent no-op, leaving data_dir at its default and pointing
    # tests at the repo's own ./data directory. Found while adding
    # frontend_dist_dir, whose tests actually assert on the path.
    model_config = SettingsConfigDict(extra="ignore", populate_by_name=True)

    data_dir: Path = Field(Path("./data"), validation_alias="CINEMAGRAPH_DATA_DIR")
    # The frontend/ build output (`bun run build`) that GET / serves. Defaults
    # to this repo's own frontend/dist, which is right for local development
    # and wrong-but-harmless anywhere else: the routes report "not built yet"
    # rather than failing, so an API-only deployment that never builds a
    # frontend still starts and serves every other route. The Docker image
    # builds the bundle and sets this explicitly.
    frontend_dist_dir: Path = Field(
        _REPO_ROOT / "frontend" / "dist", validation_alias="CINEMAGRAPH_FRONTEND_DIR"
    )
    ml_service_url: str | None = None
    acestep_url: str | None = None
    sound_effects_url: str | None = None
    image_generation_url: str | None = None
    # Not an OptionalService (no URL/health-check -- it's a hosted API called
    # directly via generation/, not a satellite container). Presence alone
    # gates whether GeminiAdapter gets registered (server/app.py); see
    # docs/DESIGN.md sec 3.6's Gemini follow-up.
    gemini_api_key: str | None = None

    @field_validator("data_dir", "frontend_dist_dir")
    @classmethod
    def _resolve_paths(cls, value: Path) -> Path:
        # Matches the previous module-level `.resolve()`; job directories are
        # created underneath data_dir, so a relative path here would resolve
        # against whatever cwd the worker happened to have. frontend_dist_dir
        # gets the same treatment for the same reason, and because the static
        # routes compare resolved paths to keep requests inside it.
        return value.resolve()

    @property
    def semantic_mask_service(self) -> OptionalService:
        return OptionalService(
            self.ml_service_url, "Semantic masking", "ML_SERVICE_URL"
        )

    @property
    def music_service(self) -> OptionalService:
        return OptionalService(self.acestep_url, "Music generation", "ACESTEP_URL")

    @property
    def sound_effect_service(self) -> OptionalService:
        return OptionalService(
            self.sound_effects_url, "Sound effect generation", "SOUND_EFFECTS_URL"
        )

    @property
    def image_generation_service(self) -> OptionalService:
        return OptionalService(
            self.image_generation_url, "Image generation", "IMAGE_GENERATION_URL"
        )


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
