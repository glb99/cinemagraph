"""Tests for the `cinemagraph doctor` checks.

The probes that talk to the outside world (subprocess, HTTP) are
monkeypatched here rather than exercised for real: what's worth pinning is
the *interpretation* -- which status a given observation maps to, and that
a failing probe degrades to a row instead of an exception. The one thing
tested against a real socket is port detection, since a fake would be
testing the fake.
"""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import diagnostics
from diagnostics import Status


@pytest.fixture
def clean_env(monkeypatch):
    """No satellite configured, no key, no TTL -- the state a fresh
    checkout is in, and the baseline every test here varies from."""
    for env_var, _, _ in diagnostics.SATELLITES:
        monkeypatch.delenv(env_var, raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("MODEL_TTL", raising=False)
    return monkeypatch


def _find(section, name):
    return next(c for c in section.checks if c.name == name)


# --- port probing, against real sockets ------------------------------------


def test_port_in_use_detects_a_real_listener():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
        assert diagnostics._port_in_use(port) is True


def test_port_in_use_is_false_once_the_listener_is_gone():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        port = sock.getsockname()[1]
    # Closed sockets refuse on some hosts and silently drop on others (see
    # _port_in_use's docstring); both must read as "not in use".
    assert diagnostics._port_in_use(port) is False


# --- HTTP probing ----------------------------------------------------------


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler's own naming
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass  # keep pytest output clean


@pytest.fixture
def live_server():
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_http_json_reads_a_real_response(live_server):
    reached, body = diagnostics._http_json(live_server + "/health")
    assert reached is True
    assert body == '{"status":"ok"}'


def test_http_json_reports_failure_without_raising():
    # Port 1 is not going to be serving HTTP; whatever the failure mode,
    # the contract is (False, reason) rather than an exception.
    reached, detail = diagnostics._http_json("http://127.0.0.1:1/health")
    assert reached is False
    assert detail


# --- satellite interpretation ----------------------------------------------


@pytest.mark.usefixtures("clean_env")
def test_unconfigured_satellite_is_info_not_a_failure():
    section = diagnostics.check_satellites()
    check = _find(section, "semantic mask (CLIPSeg)")
    assert check.status is Status.INFO
    assert "not configured" in check.detail


def test_configured_but_unreachable_satellite_is_a_failure(clean_env, monkeypatch):
    clean_env.setenv("ML_SERVICE_URL", "http://localhost:9")
    monkeypatch.setattr(
        diagnostics, "_http_probe", lambda url: (False, None, "refused")
    )
    check = _find(diagnostics.check_satellites(), "semantic mask (CLIPSeg)")
    assert check.status is Status.FAIL
    assert "unreachable" in check.detail


def test_configured_and_reachable_satellite_is_ok(clean_env, monkeypatch):
    clean_env.setenv("ML_SERVICE_URL", "http://localhost:8004")
    monkeypatch.setattr(
        diagnostics, "_http_probe", lambda url: (True, 200, '{"device":"cpu"}')
    )
    check = _find(diagnostics.check_satellites(), "semantic mask (CLIPSeg)")
    assert check.status is Status.OK
    assert "cpu" in check.detail


def test_gemini_key_presence_is_reported_but_never_its_value(clean_env):
    secret = "sk-do-not-print-me-0123456789"
    clean_env.setenv("GEMINI_API_KEY", secret)
    check = _find(diagnostics.check_satellites(), "hosted (Gemini / Lyria 3)")
    assert check.status is Status.OK
    # The whole point of reporting presence rather than the value.
    assert secret not in check.detail


# --- GPU parsing -----------------------------------------------------------


def test_gpu_memory_parsed_from_nvidia_smi(monkeypatch):
    monkeypatch.setattr(
        diagnostics,
        "_run",
        lambda cmd: (True, "NVIDIA GeForce RTX 4060 Laptop GPU, 8188, 233, 7955"),
    )
    section = diagnostics.check_gpu()
    assert _find(section, "device").detail == "NVIDIA GeForce RTX 4060 Laptop GPU"
    memory = _find(section, "memory")
    assert memory.status is Status.OK
    assert "7955 MiB free of 8188 MiB" in memory.detail


def test_gpu_warns_when_free_vram_is_under_sdxl_footprint(monkeypatch):
    monkeypatch.setattr(
        diagnostics, "_run", lambda cmd: (True, "Some GPU, 8188, 7000, 1188")
    )
    memory = _find(diagnostics.check_gpu(), "memory")
    assert memory.status is Status.WARN
    assert "SDXL" in memory.detail


def test_missing_nvidia_smi_is_info_not_a_failure(monkeypatch):
    monkeypatch.setattr(diagnostics, "_run", lambda cmd: (False, "not installed"))
    check = _find(diagnostics.check_gpu(), "nvidia-smi")
    assert check.status is Status.INFO


def test_unparseable_nvidia_smi_output_degrades_to_a_warning(monkeypatch):
    monkeypatch.setattr(diagnostics, "_run", lambda cmd: (True, "surprise"))
    check = _find(diagnostics.check_gpu(), "nvidia-smi")
    assert check.status is Status.WARN


# --- the whole run ---------------------------------------------------------


@pytest.mark.usefixtures("clean_env")
def test_run_all_never_raises_and_covers_every_section(monkeypatch):
    """A diagnostic that crashes on the broken machine it was written for
    is useless, so every probe is stubbed to its worst case at once."""
    monkeypatch.setattr(diagnostics, "_run", lambda cmd: (False, "boom"))
    monkeypatch.setattr(diagnostics, "_http_probe", lambda url: (False, None, "boom"))
    monkeypatch.setattr(diagnostics, "_port_in_use", lambda port: False)

    sections = diagnostics.run_all()
    assert [s.title for s in sections] == [
        "Tooling",
        "Project",
        "GPU",
        "Ports",
        "Optional services",
        "Core API",
    ]
    assert all(isinstance(c.status, Status) for s in sections for c in s.checks)


@pytest.mark.usefixtures("clean_env")
def test_doctor_command_exits_zero_even_when_everything_is_broken(monkeypatch):
    """`just doctor` must not fail a task chain just because a satellite is
    down -- being down is what it's there to tell you about."""
    from click.testing import CliRunner

    from cinemagraph.cli import cli

    monkeypatch.setattr(diagnostics, "_run", lambda cmd: (False, "boom"))
    monkeypatch.setattr(diagnostics, "_http_probe", lambda url: (False, None, "boom"))
    monkeypatch.setattr(diagnostics, "_port_in_use", lambda port: False)

    result = CliRunner().invoke(cli, ["doctor"])
    assert result.exit_code == 0
    assert "Tooling" in result.output


# --- ported from the parallel `cinemagraph/doctor.py` implementation --------
# These four cover behaviour that only existed in that version. It was written
# without knowing this module already existed; this file is where the two were
# reconciled. See docs/DESIGN.md's decision log.


def test_wedged_satellite_is_distinguished_from_a_dead_one(monkeypatch):
    """A satellite whose busy watchdog has flagged a stuck request answers
    /health with 503 and a body saying so. Reporting that as plain
    "unreachable" would send someone to restart a container that is already
    restarting itself."""
    monkeypatch.setenv("ML_SERVICE_URL", "http://localhost:8004")
    monkeypatch.setattr(
        diagnostics,
        "_http_probe",
        lambda url: (False, 503, '{"status":"stuck","inflight_seconds":1500.0}'),
    )
    check = _find(diagnostics.check_satellites(), "semantic mask (CLIPSeg)")
    assert check.status is Status.WARN  # not FAIL: it recovers on its own
    assert "wedged" in check.detail
    assert "1500s" in check.detail


def test_healthy_satellite_reports_model_state_in_words(monkeypatch):
    """MODEL_TTL unloads an idle model, so "reachable" and "holding the GPU"
    are different states and the difference is worth showing."""
    monkeypatch.setenv("IMAGE_GENERATION_URL", "http://localhost:8005")
    monkeypatch.setattr(
        diagnostics,
        "_http_probe",
        lambda url: (
            True,
            200,
            '{"status":"ok","device":"cuda","model_loaded":false,"inflight_seconds":null}',
        ),
    )
    check = _find(diagnostics.check_satellites(), "image (SDXL)")
    assert check.status is Status.OK
    assert "cuda" in check.detail
    assert "model unloaded (idle)" in check.detail


def test_library_check_does_not_create_the_library(tmp_path, monkeypatch):
    """asset_library.library_root() mkdirs as a side effect, which is why this
    resolves the path itself. Running `doctor` must never be the thing that
    brings a library into existence."""
    root = tmp_path / "library"
    monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(root))
    check = diagnostics._library_check()
    assert not root.exists()
    assert check.status is Status.INFO


def test_ffmpeg_is_checked_and_found():
    """The one hard dependency of the render path, and the only tool whose
    absence is a real FAIL -- it's bundled inside the imageio-ffmpeg wheel, so
    `which ffmpeg` would wrongly report it missing."""
    check = _find(diagnostics.check_tools(), "ffmpeg")
    assert check.status is Status.OK
    assert "ffmpeg" in check.detail.lower()


def test_strict_flag_turns_failures_into_a_non_zero_exit(monkeypatch):
    """The two callers want opposite things: a justfile recipe must not have
    its chain broken by a report, while a deploy gate or container healthcheck
    needs the failure to propagate. Default 0, --strict to opt in."""
    from click.testing import CliRunner

    from cinemagraph.cli import cli

    monkeypatch.setenv("ML_SERVICE_URL", "http://localhost:8004")
    monkeypatch.setattr(diagnostics, "_run", lambda cmd: (False, "boom"))
    monkeypatch.setattr(diagnostics, "_http_probe", lambda url: (False, None, "boom"))
    monkeypatch.setattr(diagnostics, "_port_in_use", lambda port: False)

    runner = CliRunner()
    assert runner.invoke(cli, ["doctor"]).exit_code == 0

    strict = runner.invoke(cli, ["doctor", "--strict"])
    assert strict.exit_code != 0
    # Names the rows that failed, so the message is actionable on its own.
    assert "semantic mask (CLIPSeg)" in strict.output
