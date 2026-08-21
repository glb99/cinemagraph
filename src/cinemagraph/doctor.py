"""Environment diagnostics: what's configured, what's reachable, what's broken.

Every misconfiguration in this project used to be discoverable only by reading
comments in docker-compose.yml. This turns each one into a line with a fix
attached.

Tier note (docs/DESIGN.md sec 3.2): this lives in `cinemagraph`, the core tier,
which must never gain httpx, pydantic, or any other server-tier dependency --
`cinemagraph doctor` has to work from a bare `uv sync` with no extras. So it
reads os.environ directly (exactly as asset_library already does) and uses
stdlib urllib for reachability, rather than importing server.config or
server._external_service. Duplicating a little env-var knowledge is the price
of that, and it is deliberate.

Checks return data, never print (sec 3.4) -- cli.py owns the formatting, and
the pure-function shape is what makes them testable without a live stack.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

# Kept short: doctor is meant to answer quickly even when several satellites
# are down, and every one of them is either local or on the LAN.
PROBE_TIMEOUT_SECONDS = 3.0


class Status(StrEnum):
    OK = "ok"
    # Configured and working is OK; *not* configured is OFF, not a warning.
    # An optional feature nobody enabled is the expected state for most
    # installs, and flagging it would train people to ignore the output.
    OFF = "off"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: Status
    detail: str
    fix: str | None = None


# Mirrors server/config.py's OptionalService trio, intentionally re-stated here
# rather than imported -- see the tier note above.
SATELLITES: tuple[tuple[str, str, str], ...] = (
    (
        "Semantic masking",
        "ML_SERVICE_URL",
        "docker compose --profile ml up machine-learning",
    ),
    ("Music generation", "ACESTEP_URL", "docker compose --profile audio up acestep"),
    (
        "Sound effects",
        "SOUND_EFFECTS_URL",
        "docker compose --profile audio up sound-effects",
    ),
    (
        "Image generation",
        "IMAGE_GENERATION_URL",
        "docker compose --profile image up image-generation",
    ),
)


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    """Every check takes its environment as an argument and defaults to the
    real one here, rather than reading os.environ inline. That's what lets the
    tests drive each check with an exact environment and no monkeypatching."""
    return os.environ if env is None else env


def check_ffmpeg() -> CheckResult:
    """imageio-ffmpeg bundles its own binary, so this should essentially never
    fail -- but when it does, every write_video call fails with a message that
    doesn't mention ffmpeg, so it's worth stating plainly."""
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:
        return CheckResult(
            "ffmpeg",
            Status.FAIL,
            f"not usable: {exc}",
            "reinstall dependencies with `uv sync`",
        )
    return CheckResult("ffmpeg", Status.OK, exe)


def _writable(path: Path) -> bool:
    """Probe by writing, not by inspecting mode bits: the mode says nothing
    useful on Windows, and nothing at all about a read-only bind mount."""
    probe = path / ".cinemagraph-write-probe"
    try:
        probe.write_text("")
        probe.unlink()
        return True
    except OSError:
        return False


def check_data_dir(env: Mapping[str, str] | None = None) -> CheckResult:
    values = _env(env)
    raw = values.get("CINEMAGRAPH_DATA_DIR", "./data")
    path = Path(raw).expanduser()
    if not path.exists():
        # Not a failure: the API creates it on demand. Only worth mentioning
        # so an unexpected location is visible before jobs start landing in it.
        return CheckResult(
            "Data directory",
            Status.OK,
            f"{path.resolve()} (will be created on first use)",
        )
    if not _writable(path):
        return CheckResult(
            "Data directory",
            Status.FAIL,
            f"{path.resolve()} is not writable",
            "fix the directory's permissions, or set CINEMAGRAPH_DATA_DIR elsewhere",
        )
    return CheckResult("Data directory", Status.OK, str(path.resolve()))


def check_library(env: Mapping[str, str] | None = None) -> CheckResult:
    """Resolves the library path the same way asset_library.library_root() does,
    but deliberately does NOT call it: that function creates the directory as a
    side effect, and a diagnostic must not change what it is diagnosing."""
    values = _env(env)
    root = Path(
        values.get("CINEMAGRAPH_LIBRARY_DIR", "~/.cinemagraph/library")
    ).expanduser()
    if not root.exists():
        return CheckResult(
            "Asset library",
            Status.OK,
            f"{root} (empty -- will be created on first use)",
        )
    if not _writable(root):
        return CheckResult(
            "Asset library",
            Status.FAIL,
            f"{root} is not writable",
            "fix the directory's permissions, or set CINEMAGRAPH_LIBRARY_DIR elsewhere",
        )
    index = root / "index.sqlite3"
    if not index.exists():
        return CheckResult("Asset library", Status.OK, f"{root} (no index yet)")
    try:
        import sqlite3

        with sqlite3.connect(index) as conn:
            count = conn.execute("SELECT count(*) FROM assets").fetchone()[0]
    except Exception as exc:
        return CheckResult(
            "Asset library",
            Status.WARN,
            f"{root}: index present but unreadable ({exc})",
            "if this persists, move index.sqlite3 aside; stored objects are not affected",
        )
    return CheckResult("Asset library", Status.OK, f"{root} ({count} assets)")


def check_env_file(repo_root: Path) -> CheckResult:
    """Only meaningful in a source checkout. Absent .env is fine -- everything
    optional is simply off -- so this is never worse than a warning.

    The "not read by this CLI" note is load-bearing: nothing here loads .env
    (there's no python-dotenv dependency, by choice), so Docker Compose is the
    only thing that consumes it. Without saying so, a filled-in .env sitting
    next to a row reporting the same variable as unset reads as a bug.
    """
    if (repo_root / ".env").exists():
        return CheckResult(
            "Config file",
            Status.OK,
            f"{repo_root / '.env'} (read by Docker Compose; not by this CLI)",
        )
    if (repo_root / ".env.example").exists():
        return CheckResult(
            "Config file",
            Status.OFF,
            "no .env (optional features are off)",
            "cp .env.example .env",
        )
    return CheckResult("Config file", Status.OFF, "no .env")


def probe_health(url: str, timeout: float = PROBE_TIMEOUT_SECONDS) -> tuple[int, dict]:
    """GET {url}/health -> (status_code, parsed body).

    A 503 is a real answer here, not an error: the satellites return one when a
    request has wedged past BUSY_TIMEOUT, and that body is exactly what we want
    to report. urllib raises on it, so it's caught and unwrapped rather than
    treated as unreachable.
    """
    request = urllib.request.Request(f"{url.rstrip('/')}/health", method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read() or b"{}")
        except (ValueError, OSError):
            return exc.code, {}


def check_satellite(
    label: str,
    env_var: str,
    start_command: str,
    env: Mapping[str, str] | None = None,
) -> CheckResult:
    values = _env(env)
    url = values.get(env_var)
    if not url:
        return CheckResult(label, Status.OFF, f"{env_var} not set", start_command)

    try:
        code, body = probe_health(url)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Configured but unreachable is the interesting case: someone meant to
        # have this and it isn't there. That's a warning, unlike "not set".
        return CheckResult(
            label,
            Status.WARN,
            f"{url} is configured but unreachable ({exc})",
            start_command,
        )

    if code == 503 or body.get("status") == "stuck":
        inflight = body.get("inflight_seconds")
        return CheckResult(
            label,
            Status.WARN,
            f"{url} reports a wedged request"
            + (f" ({inflight}s in flight)" if inflight is not None else ""),
            "it should self-recover when BUSY_TIMEOUT elapses; "
            "`docker compose restart` won't wait",
        )
    if code != 200:
        return CheckResult(
            label, Status.WARN, f"{url} returned HTTP {code}", start_command
        )

    bits = [url]
    if "device" in body:
        bits.append(f"on {body['device']}")
    if body.get("model_loaded") is not None:
        bits.append("model loaded" if body["model_loaded"] else "model idle")
    if body.get("inflight_seconds") is not None:
        bits.append(f"busy {body['inflight_seconds']}s")
    return CheckResult(label, Status.OK, ", ".join(bits))


def check_gemini(env: Mapping[str, str] | None = None) -> CheckResult:
    """Presence only. Never validates the key against Google -- a diagnostic
    must not spend the user's quota or send their credential anywhere."""
    values = _env(env)
    if values.get("GEMINI_API_KEY"):
        return CheckResult(
            "Gemini API key", Status.OK, "set (enables the gemini and lyria3 backends)"
        )
    return CheckResult(
        "Gemini API key",
        Status.OFF,
        "not set",
        "optional -- set GEMINI_API_KEY to enable hosted image and music backends",
    )


def check_gpu() -> CheckResult:
    """Informational only, never a failure: everything in this project runs on
    CPU, and the satellites are the only things that care."""
    if shutil.which("nvidia-smi") is None:
        return CheckResult(
            "GPU",
            Status.OFF,
            "no nvidia-smi (CPU only -- everything still works, slower)",
        )
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.total,memory.used",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
    except (subprocess.SubprocessError, OSError) as exc:
        return CheckResult("GPU", Status.WARN, f"nvidia-smi failed: {exc}")
    line = out.stdout.strip().splitlines()
    if not line:
        return CheckResult("GPU", Status.OFF, "nvidia-smi reported no devices")
    return CheckResult("GPU", Status.OK, "; ".join(part.strip() for part in line))


def run_all_checks(
    repo_root: Path | None = None, env: Mapping[str, str] | None = None
) -> list[CheckResult]:
    """Order matters for reading: local prerequisites first, then optional
    services, so the first FAIL a user sees is the one blocking core use."""
    # src/cinemagraph/doctor.py -> src/cinemagraph -> src -> repo root.
    root = repo_root if repo_root is not None else Path(__file__).resolve().parents[2]
    results = [
        check_ffmpeg(),
        check_data_dir(env),
        check_library(env),
        check_env_file(root),
        check_gpu(),
    ]
    results.extend(
        check_satellite(label, var, command, env) for label, var, command in SATELLITES
    )
    results.append(check_gemini(env))
    return results


def worst_status(results: list[CheckResult]) -> Status:
    for status in (Status.FAIL, Status.WARN, Status.OK):
        if any(r.status is status for r in results):
            return status
    return Status.OFF
