"""Preflight checks for a whole deployment of this project.

`cinemagraph doctor` is the one command that answers "why isn't this
working" without a guessing game: which tools are installed, which ports
are taken and by what, whether the web UI has actually been built, which
optional services are configured versus actually reachable, and how much
VRAM is free right now.

**Why its own top-level package**, and not `cinemagraph/doctor.py`: it is
cross-cutting in exactly the sense `asset_library` is (docs/DESIGN.md sec
5.1). It knows about the satellite services, the frontend bundle, Docker
and the GPU -- none of which the `cinemagraph` package itself is allowed
to learn about. Putting it there would give the core photo/video library
an opinion on ACE-Step URLs.

**Zero dependencies beyond the stdlib**, deliberately, for the same reason
`asset_library` has none: `cinemagraph doctor` has to work from a bare
`uv sync` with no extras -- which is precisely the situation where you
most need it, since "the server extra isn't installed" is one of the
things it reports. So HTTP is `urllib.request`, not `httpx`; GPU state is
`nvidia-smi` parsed from CSV, not `torch.cuda`; and every external tool is
probed through `subprocess` with a timeout and treated as absent on any
failure.

Nothing here raises. Every probe degrades to a WARN/FAIL row, because a
diagnostic that crashes on the broken machine it was written for is
useless. `run_all()` returns plain data; formatting lives in `cli.py`.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Short: every one of these runs against localhost or a local daemon, and
# the whole command should stay interactive. A satellite that needs longer
# than this to answer /health is, for the operator's purposes, down.
_HTTP_TIMEOUT_S = 1.5
_PORT_TIMEOUT_S = 0.3
_SUBPROCESS_TIMEOUT_S = 10.0


class Status(Enum):
    OK = "ok"
    WARN = "warn"
    FAIL = "fail"
    INFO = "info"


@dataclass(frozen=True)
class Check:
    name: str
    status: Status
    detail: str


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)


# (env var, display name, default compose port) for each optional service.
# Mirrors server/config.py's OptionalService trio plus image generation --
# duplicated rather than imported because that module needs pydantic, which
# this package deliberately does not depend on (see the module docstring).
SATELLITES = (
    ("ML_SERVICE_URL", "semantic mask (CLIPSeg)", 8004),
    ("ACESTEP_URL", "music (ACE-Step)", 8001),
    ("SOUND_EFFECTS_URL", "sound effects (Stable Audio)", 8003),
    ("IMAGE_GENERATION_URL", "image (SDXL)", 8005),
)

# Ports this project's own processes use, for the "what is already running"
# check. The API and the Vite dev server are the two that actually collide
# in practice.
KNOWN_PORTS = (
    (8000, "core API"),
    (5173, "frontend dev server"),
    (8001, "acestep"),
    (8003, "sound-effects"),
    (8004, "machine-learning"),
    (8005, "image-generation"),
)


def _run(cmd: list[str]) -> tuple[bool, str]:
    """Run a command, returning (succeeded, first line of output).

    Any failure mode -- missing binary, non-zero exit, timeout, or a daemon
    that never answers -- collapses to (False, reason). Callers only ever
    need "is this usable".
    """
    if shutil.which(cmd[0]) is None:
        return False, "not installed"
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {_SUBPROCESS_TIMEOUT_S:.0f}s"
    except OSError as exc:
        return False, str(exc)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip().splitlines()
        return False, detail[0] if detail else f"exit {proc.returncode}"
    out = proc.stdout.strip().splitlines()
    return True, out[0] if out else ""


def _port_in_use(port: int) -> bool:
    """True if something is already listening on localhost:port.

    Tries IPv4 and IPv6 separately: Vite binds ::1 only, so a v4-only probe
    calls its port free while the browser reaches it perfectly well.

    Deliberately two states, not three. Distinguishing "closed" (refused
    instantly) from "filtered" (packets dropped, connection hangs) would be
    genuinely useful -- filtered is what makes an HTTP check burn its whole
    timeout instead of failing fast. It was tried and removed: on the
    Windows host this project is developed on, *every* closed local port
    times out rather than refusing, so the distinction fired on all six
    ports at once and turned the whole section into noise. A check that
    warns about everything says nothing. The practical consequence survives
    in _HTTP_TIMEOUT_S being short, since an unreachable local service here
    costs the full timeout rather than failing immediately.
    """
    for family, host in ((socket.AF_INET, "127.0.0.1"), (socket.AF_INET6, "::1")):
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(_PORT_TIMEOUT_S)
                sock.connect((host, port))
                return True
        except OSError:
            continue
    return False


# Local diagnostics must never be routed through an HTTP proxy: on a machine
# with one configured, every localhost check would otherwise be answered (or
# swallowed) by the proxy rather than by the service being diagnosed.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_probe(url: str) -> tuple[bool, int | None, str]:
    """GET a URL, returning (answered_200, status_code, body-or-error).

    The body is kept even on an error status, which matters for exactly one
    case: since the busy watchdog landed, a satellite that has a wedged
    request answers /health with 503 and a body saying so. Throwing that
    away would flatten "stuck, and about to restart itself" into the same
    "unreachable" as a container that was never started -- two problems
    with different answers.
    """
    try:
        with _OPENER.open(url, timeout=_HTTP_TIMEOUT_S) as resp:
            return True, resp.status, resp.read(400).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(400).decode("utf-8", "replace").strip()
        except OSError:
            body = ""
        return False, exc.code, body
    except (urllib.error.URLError, OSError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return False, None, str(reason)


def _http_json(url: str) -> tuple[bool, str]:
    """GET a URL, returning (reached, body-or-error). Body is truncated.

    Deliberately not parsed as JSON: this only ever feeds a human-readable
    detail string, and a service answering with something unparsable is
    itself the useful signal.
    """
    ok, code, body = _http_probe(url)
    if ok:
        return True, body
    return False, f"HTTP {code}" if code is not None else body


def check_tools() -> Section:
    section = Section("Tooling")
    section.checks.append(
        Check("python", Status.INFO, sys.version.split()[0] + f"  ({sys.executable})")
    )
    for tool, cmd, required in (
        ("uv", ["uv", "--version"], True),
        ("bun", ["bun", "--version"], True),
        ("docker", ["docker", "--version"], False),
        ("just", ["just", "--version"], False),
    ):
        ok, detail = _run(cmd)
        if ok:
            section.checks.append(Check(tool, Status.OK, detail))
        else:
            # Missing bun only matters for building the UI, missing docker
            # only for the satellites -- neither stops the CLI or the API,
            # so nothing here is a FAIL.
            section.checks.append(
                Check(tool, Status.WARN if required else Status.INFO, detail)
            )

    # ffmpeg is the one genuine hard dependency of the render path, and the
    # only tool here whose absence is a real FAIL: without it every video and
    # GIF write fails, with an error that doesn't mention ffmpeg. It's bundled
    # inside the imageio-ffmpeg wheel rather than installed on the system, so
    # `which ffmpeg` is the wrong question -- ask the package.
    try:
        import imageio_ffmpeg

        section.checks.append(
            Check("ffmpeg", Status.OK, imageio_ffmpeg.get_ffmpeg_exe())
        )
    except Exception as exc:  # noqa: BLE001 -- any failure means no renders
        section.checks.append(
            Check(
                "ffmpeg", Status.FAIL, f"unusable ({exc}) -- reinstall with `uv sync`"
            )
        )
    return section


def check_project() -> Section:
    """The two things most likely to be quietly stale or missing."""
    section = Section("Project")

    dist = Path(
        os.environ.get("CINEMAGRAPH_FRONTEND_DIR") or _REPO_ROOT / "frontend" / "dist"
    )
    index = dist / "index.html"
    if index.is_file():
        # A built bundle is served as-is, so "when" is the useful fact: a
        # UI change that doesn't show up at :8000 is almost always this.
        mtime = _format_mtime(index)
        section.checks.append(
            Check("web UI bundle", Status.OK, f"built {mtime}  ({dist})")
        )
    else:
        section.checks.append(
            Check(
                "web UI bundle",
                Status.WARN,
                f"not built -- GET / returns a 503 page. `cd frontend && bun run build`  ({dist})",
            )
        )

    try:
        import fastapi  # noqa: F401

        section.checks.append(Check("server extra", Status.OK, "installed"))
    except ImportError:
        section.checks.append(
            Check(
                "server extra",
                Status.WARN,
                "not installed -- the API can't start. `uv sync --extra server`",
            )
        )

    section.checks.append(
        Check(
            "data dir",
            Status.INFO,
            str(Path(os.environ.get("CINEMAGRAPH_DATA_DIR", "./data")).resolve()),
        )
    )
    section.checks.append(_library_check())
    return section


def _format_mtime(path: Path) -> str:
    from datetime import datetime

    return datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")


def _library_check() -> Check:
    """Asset count and root. Imported lazily so a broken library index
    degrades to one WARN row instead of taking the whole command down.

    Resolves the root the same way asset_library does but does NOT call
    library_root(), which creates the directory as a side effect: a
    diagnostic must not change the thing it is reporting on. Running
    `doctor` should never be what brings a library into existence.
    """
    root = Path(
        os.environ.get("CINEMAGRAPH_LIBRARY_DIR", "~/.cinemagraph/library")
    ).expanduser()
    if not (root / "index.sqlite3").is_file():
        return Check("asset library", Status.INFO, f"empty -- no index yet  ({root})")
    try:
        import sqlite3

        with sqlite3.connect(root / "index.sqlite3") as conn:
            count = conn.execute("SELECT count(*) FROM assets").fetchone()[0]
        return Check("asset library", Status.OK, f"{count} assets  ({root})")
    except Exception as exc:  # noqa: BLE001 -- any failure is just a WARN row
        # The stored objects are content-addressed files and survive a broken
        # index, so this is never worse than a warning.
        return Check("asset library", Status.WARN, f"index unreadable: {exc}  ({root})")


def check_gpu() -> Section:
    """VRAM via nvidia-smi rather than torch: torch isn't a dependency of
    this package (or of `cinemagraph` at all), and nvidia-smi reports the
    whole card including memory held by other processes -- which is the
    number that actually matters when four satellites share one GPU."""
    section = Section("GPU")
    ok, line = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total,memory.used,memory.free",
            "--format=csv,noheader,nounits",
        ]
    )
    if not ok:
        section.checks.append(
            Check("nvidia-smi", Status.INFO, f"{line} -- GPU features unavailable")
        )
        return section

    parts = [p.strip() for p in line.split(",")]
    if len(parts) != 4:
        section.checks.append(
            Check("nvidia-smi", Status.WARN, f"unparsed output: {line}")
        )
        return section

    name, total, used, free = parts
    section.checks.append(Check("device", Status.INFO, name))
    try:
        free_mib, total_mib = int(free), int(total)
    except ValueError:
        section.checks.append(Check("memory", Status.WARN, f"unparsed: {line}"))
        return section

    # SDXL alone measured at ~7147 MiB on this project's card (see
    # docs/experiments/2026-08-18-...), so free VRAM below that means an
    # image generation will not fit until something unloads.
    detail = f"{free_mib} MiB free of {total_mib} MiB  ({used} MiB in use)"
    status = Status.OK if free_mib >= 7200 else Status.WARN
    if status is Status.WARN:
        detail += "  -- under SDXL's measured ~7147 MiB footprint"
    section.checks.append(Check("memory", status, detail))
    return section


def check_ports() -> Section:
    section = Section("Ports")
    for port, owner in KNOWN_PORTS:
        # Both states are normal depending on what you meant to start, so
        # neither is a pass or a failure -- they're facts. The one that
        # matters is 8000 or 5173 being occupied by something that isn't
        # this project, which is a collision you can only see by looking.
        section.checks.append(
            Check(
                f"{port} ({owner})",
                Status.INFO,
                "in use -- something is listening" if _port_in_use(port) else "free",
            )
        )
    return section


def _parse_health(body: str) -> dict:
    """A satellite's /health body as a dict, or {} for anything else.

    Never raises: a service answering with prose, HTML, or nothing at all is
    a normal thing to encounter here, and the caller falls back to showing
    the raw body.
    """
    try:
        import json

        parsed = json.loads(body)
    except (ValueError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _describe_health(health: dict) -> str:
    """The interesting fields of a /health body, in words rather than JSON:
    which device it's on, whether the model is currently resident (MODEL_TTL
    unloads it when idle), and how long any in-flight request has been
    running."""
    parts: list[str] = []
    device = health.get("device")
    if isinstance(device, str):
        parts.append(device)
    loaded = health.get("model_loaded")
    if isinstance(loaded, bool):
        parts.append("model loaded" if loaded else "model unloaded (idle)")
    inflight = health.get("inflight_seconds")
    if isinstance(inflight, int | float):
        parts.append(f"busy {inflight:.0f}s")
    return ", ".join(parts)


def check_satellites() -> Section:
    """Configured-vs-reachable, the same distinction GET /capabilities
    draws and the web UI's tab gating depends on."""
    section = Section("Optional services")
    for env_var, label, default_port in SATELLITES:
        url = os.environ.get(env_var)
        if not url:
            section.checks.append(
                Check(
                    label,
                    Status.INFO,
                    f"not configured ({env_var} unset) -- feature hidden in the UI",
                )
            )
            continue
        ok, code, body = _http_probe(url.rstrip("/") + "/health")
        health = _parse_health(body)

        if ok:
            section.checks.append(
                Check(label, Status.OK, f"{url} -- {_describe_health(health) or body}")
            )
        elif health.get("status") == "stuck" or code == 503:
            # Answered, but reports itself wedged: a request has run past
            # BUSY_TIMEOUT and the service is about to exit and be restarted
            # by its container. Not the same problem as "down", and not one
            # the operator needs to act on -- so WARN, not FAIL.
            inflight = health.get("inflight_seconds")
            section.checks.append(
                Check(
                    label,
                    Status.WARN,
                    f"{url} reports a wedged request"
                    + (
                        f" ({inflight:.0f}s in flight)"
                        if isinstance(inflight, int | float)
                        else ""
                    )
                    + " -- it restarts itself once BUSY_TIMEOUT elapses",
                )
            )
        else:
            section.checks.append(
                Check(
                    label,
                    Status.FAIL,
                    f"{url} unreachable ({body or code}) -- configured but down, so the UI "
                    f"shows the tab with a warning. Expected on port {default_port}.",
                )
            )

    gemini = bool(os.environ.get("GEMINI_API_KEY"))
    section.checks.append(
        Check(
            "hosted (Gemini / Lyria 3)",
            Status.OK if gemini else Status.INFO,
            # Presence only. The value is a secret and is never echoed.
            "GEMINI_API_KEY set -- image+music work without a GPU"
            if gemini
            else "GEMINI_API_KEY unset -- no hosted image/music backend",
        )
    )
    ttl = os.environ.get("MODEL_TTL")
    section.checks.append(
        Check(
            "MODEL_TTL",
            Status.INFO,
            f"{ttl}s"
            if ttl
            else "unset -- compose defaults to 900s (0 = never unload)",
        )
    )
    return section


def check_api() -> Section:
    """Is this project's own API up, and what does it think it can do."""
    section = Section("Core API")
    base = os.environ.get("CINEMAGRAPH_API_URL", "http://localhost:8000")
    reached, body = _http_json(base + "/health")
    if not reached:
        section.checks.append(
            Check("api", Status.INFO, f"{base} not responding ({body}) -- not started?")
        )
        return section
    section.checks.append(Check("api", Status.OK, f"{base} -> {body}"))
    ok, caps = _http_json(base + "/capabilities")
    if ok:
        section.checks.append(Check("capabilities", Status.INFO, caps))
    return section


def run_all() -> list[Section]:
    """Every section, in the order a human would want to read them."""
    return [
        check_tools(),
        check_project(),
        check_gpu(),
        check_ports(),
        check_satellites(),
        check_api(),
    ]
