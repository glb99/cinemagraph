"""/setup/* -- the routes behind the web UI's Setup tab.

Only one thing here writes: storing a Gemini API key. Everything else the tab
shows (which satellites are reachable, which engines are registered) already
comes from GET /capabilities.

Scope, deliberately: this writes a key into a dotenv file and says "restart to
apply". It does *not* reload settings, and it does *not* start, stop or
otherwise touch containers -- the satellites stay operator-controlled, for the
reason recorded in docs/DESIGN.md: this API has no authentication and its
container runs as root, so handing it the Docker socket would turn "anyone
reaching this port can spend your GPU" into "anyone reaching this port owns
the host".

That same missing authentication bounds what this route may hold. It accepts a
key and reports whether one is set; it never returns the value, logs it, or
offers any route that reads it back -- the rule `cinemagraph doctor` already
follows for GEMINI_API_KEY. Anyone who can reach this port can still overwrite
the stored key, so the port's own exposure is what protects it: the shipped
compose file binds it to 127.0.0.1.
"""

from fastapi import APIRouter, HTTPException

from ..config import SettingsDep
from ..env_file import EnvFileError, env_var_is_set, set_env_var
from ..schemas import GeminiKeyRequest, SetupWriteResponse

router = APIRouter(prefix="/setup", tags=["setup"])

_GEMINI_KEY = "GEMINI_API_KEY"


@router.post("/gemini-key")
def store_gemini_key(
    request: GeminiKeyRequest, settings: SettingsDep
) -> SetupWriteResponse:
    """Store (or, with an empty value, remove) GEMINI_API_KEY in the dotenv
    file, for the *next* start of this API to pick up.

    422 when the value can't be written unquoted -- dotenv quoting is read
    slightly differently by Compose, pydantic-settings and sh, so a value
    needing it is refused with a pointer to setting the variable directly,
    rather than guessing which dialect will parse it back.
    """
    key = request.api_key.strip()
    try:
        set_env_var(settings.env_file, _GEMINI_KEY, key)
    except EnvFileError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except OSError as error:
        # A read-only bind mount is the likely cause in a container, and it's
        # worth naming the path: the user's next move is to set the variable
        # in the environment instead.
        raise HTTPException(
            status_code=500,
            detail=f"Couldn't write {settings.env_file}: {error.strerror or error}",
        ) from error

    return SetupWriteResponse(
        env_file=str(settings.env_file),
        is_set=env_var_is_set(settings.env_file, _GEMINI_KEY),
    )
