"""Setting a single variable in a dotenv file, preserving everything else.

Exists because the setup tab has to be able to store a Gemini API key
somewhere the API will pick it up, and `.env` is the file this project
already treats as the place secrets live: it's gitignored, and Docker
Compose auto-loads it for ``${VAR}`` substitution, so one file covers both
the source-checkout and the compose deployment.

Nothing here reloads anything. `get_settings()` is deliberately cached for
the process lifetime (see config.py), on the stated grounds that env vars are
fixed before a process starts -- writing this file doesn't change that, it
just means the *next* start sees the new value. The caller is responsible for
telling the user to restart; silently invalidating the settings cache would
trade a design invariant for the appearance of immediacy.
"""

import os
import re
import stat
import tempfile
from pathlib import Path

_KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")

# Deliberately narrow: everything a real API key uses, and nothing that would
# need dotenv quoting. Compose, pydantic-settings and `sh` each strip quotes
# slightly differently, so rejecting the values that would need quoting is
# more honest than picking one dialect's escaping and hoping the others agree.
_SAFE_VALUE = re.compile(r"^[A-Za-z0-9_./:@+\-]*$")


class EnvFileError(ValueError):
    """The key or value can't be written safely to a dotenv file."""


def _assignment_for(key: str) -> re.Pattern[str]:
    # `export FOO=` is accepted when reading because plenty of hand-written
    # .env files have it, even though Compose itself ignores the prefix.
    return re.compile(rf"^\s*(?:export\s+)?{re.escape(key)}\s*=")


def set_env_var(path: Path, key: str, value: str) -> None:
    """Write ``key=value`` into the dotenv file at *path*.

    Replaces an existing assignment in place (keeping surrounding lines,
    comments and ordering untouched) or appends one. An empty *value* removes
    the assignment entirely, so a key added here can be taken back out without
    hand-editing the file.

    Written via a temporary file in the same directory and then `os.replace`,
    so a failure part-way through can't leave a half-written .env -- which for
    this file would mean losing every *other* variable in it too.
    """
    if not _KEY_PATTERN.match(key):
        raise EnvFileError(f"{key!r} is not a valid environment variable name.")
    if not _SAFE_VALUE.match(value):
        raise EnvFileError(
            "Value contains characters that can't be stored unquoted in a .env "
            "file. Set this variable in the environment directly instead."
        )

    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = _assignment_for(key)
    assignment = f"{key}={value}"
    kept: list[str] = []
    replaced = False
    for line in lines:
        if not pattern.match(line):
            kept.append(line)
            continue
        # First match keeps the key's position in the file; any later
        # duplicate is dropped, since only the last assignment would have
        # taken effect anyway and leaving a stale one is a trap.
        if value and not replaced:
            kept.append(assignment)
            replaced = True
    if value and not replaced:
        kept.append(assignment)

    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".env.", suffix=".tmp")
    temp_path = Path(temp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write("\n".join(kept) + ("\n" if kept else ""))
        # Owner-only: this file holds API keys. mkstemp is already 0600, but
        # be explicit rather than relying on it. A no-op on Windows, where the
        # ACL, not the mode bit, is what actually governs access.
        os.chmod(temp_path, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp_path, path)
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise


def env_var_is_set(path: Path, key: str) -> bool:
    """Whether *path* assigns *key* a non-empty value.

    Reports only that it's set, never what to -- the same rule `cinemagraph
    doctor` follows for GEMINI_API_KEY, and the reason no route hands this
    file's contents back to a caller.
    """
    if not path.exists():
        return False
    pattern = _assignment_for(key)
    for line in path.read_text(encoding="utf-8").splitlines():
        if pattern.match(line):
            _, _, value = line.partition("=")
            return bool(value.strip())
    return False
