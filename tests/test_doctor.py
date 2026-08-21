"""Unit tests for `cinemagraph doctor`.

Every check takes its environment as a parameter rather than reading the
process's, which is the whole reason they're separate functions instead of
inline in cli.py -- none of this needs a live stack, a GPU, or a container.

The satellite tests fake the network at `doctor.probe_health`, the single
seam between the checks and urllib.
"""

import json
import urllib.error

import pytest
from click.testing import CliRunner

from cinemagraph import doctor
from cinemagraph.cli import cli


def test_ffmpeg_is_present_in_a_working_install():
    # imageio-ffmpeg bundles its own binary, so this is a real assertion about
    # the dev environment, not a tautology -- it fails if the wheel is broken.
    assert doctor.check_ffmpeg().status is doctor.Status.OK


class TestDataDir:
    def test_missing_directory_is_not_a_failure(self, tmp_path):
        # The API creates it on demand; flagging it would be a false alarm on
        # every fresh checkout.
        result = doctor.check_data_dir({"CINEMAGRAPH_DATA_DIR": str(tmp_path / "nope")})
        assert result.status is doctor.Status.OK
        assert "will be created" in result.detail

    def test_existing_writable_directory_passes(self, tmp_path):
        assert doctor.check_data_dir(
            {"CINEMAGRAPH_DATA_DIR": str(tmp_path)}
        ).status is (doctor.Status.OK)

    def test_unwritable_directory_fails_with_a_fix(self, tmp_path, monkeypatch):
        # Patch the probe rather than chmod: mode bits don't reliably deny the
        # owner on Windows, where this project is primarily developed.
        monkeypatch.setattr(doctor, "_writable", lambda path: False)
        result = doctor.check_data_dir({"CINEMAGRAPH_DATA_DIR": str(tmp_path)})
        assert result.status is doctor.Status.FAIL
        assert result.fix


class TestLibrary:
    def test_does_not_create_the_directory(self, tmp_path):
        """A diagnostic must not change what it diagnoses. asset_library's own
        library_root() mkdirs as a side effect, which is why doctor resolves
        the path itself instead of calling it."""
        root = tmp_path / "library"
        doctor.check_library({"CINEMAGRAPH_LIBRARY_DIR": str(root)})
        assert not root.exists()

    def test_reports_asset_count_from_a_real_index(self, tmp_path, monkeypatch):
        import asset_library

        root = tmp_path / "library"
        # Build the index through the real library so the schema is real too.
        # asset_library reads CINEMAGRAPH_LIBRARY_DIR itself rather than taking
        # a root argument, so point it there the same way the API tests do.
        monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(root))
        source = tmp_path / "thing.txt"
        source.write_text("hello")
        asset_library.add(str(source), kind="reference")

        result = doctor.check_library({"CINEMAGRAPH_LIBRARY_DIR": str(root)})
        assert result.status is doctor.Status.OK
        assert "1 assets" in result.detail

    def test_corrupt_index_warns_rather_than_fails(self, tmp_path):
        root = tmp_path / "library"
        root.mkdir()
        (root / "index.sqlite3").write_text("this is not a database")
        result = doctor.check_library({"CINEMAGRAPH_LIBRARY_DIR": str(root)})
        # Warn, not fail: the stored objects are content-addressed files and
        # are unaffected by a broken index.
        assert result.status is doctor.Status.WARN


class TestEnvFile:
    def test_missing_env_suggests_copying_the_example(self, tmp_path):
        (tmp_path / ".env.example").write_text("# example")
        result = doctor.check_env_file(tmp_path)
        assert result.status is doctor.Status.OFF
        assert result.fix == "cp .env.example .env"

    def test_present_env_says_the_cli_does_not_read_it(self, tmp_path):
        (tmp_path / ".env").write_text("HF_TOKEN=x")
        result = doctor.check_env_file(tmp_path)
        assert result.status is doctor.Status.OK
        # Without this note, a filled .env beside a "not set" row reads as a bug.
        assert "not by this CLI" in result.detail


class TestSatellite:
    def test_unconfigured_is_off_not_a_warning(self):
        result = doctor.check_satellite("Music", "ACESTEP_URL", "docker compose up", {})
        assert result.status is doctor.Status.OFF
        assert result.fix == "docker compose up"

    def test_healthy_reports_device_and_model_state(self, monkeypatch):
        monkeypatch.setattr(
            doctor,
            "probe_health",
            lambda url, timeout=None: (
                200,
                {"status": "ok", "device": "cuda", "model_loaded": True},
            ),
        )
        result = doctor.check_satellite(
            "Image",
            "IMAGE_GENERATION_URL",
            "cmd",
            {"IMAGE_GENERATION_URL": "http://x:8005"},
        )
        assert result.status is doctor.Status.OK
        assert "cuda" in result.detail
        assert "model loaded" in result.detail

    def test_configured_but_unreachable_warns(self, monkeypatch):
        def boom(*_args, **_kwargs):
            raise urllib.error.URLError("connection refused")

        monkeypatch.setattr(doctor, "probe_health", boom)
        result = doctor.check_satellite(
            "Image",
            "IMAGE_GENERATION_URL",
            "start it",
            {"IMAGE_GENERATION_URL": "http://x:8005"},
        )
        # Configured-but-absent means someone meant to have this -- distinct
        # from never configuring it at all.
        assert result.status is doctor.Status.WARN
        assert result.fix == "start it"

    def test_wedged_satellite_is_reported_as_such(self, monkeypatch):
        """The 503 that the busy watchdog's /health returns is a real answer,
        not an outage -- doctor must surface it as a stuck request rather than
        as 'unreachable'."""
        monkeypatch.setattr(
            doctor,
            "probe_health",
            lambda url, timeout=None: (
                503,
                {"status": "stuck", "inflight_seconds": 1500.0},
            ),
        )
        result = doctor.check_satellite(
            "Image",
            "IMAGE_GENERATION_URL",
            "cmd",
            {"IMAGE_GENERATION_URL": "http://x:8005"},
        )
        assert result.status is doctor.Status.WARN
        assert "wedged" in result.detail
        assert "1500" in result.detail


class TestProbeHealth:
    def test_503_body_is_returned_not_raised(self, monkeypatch):
        """urllib raises HTTPError on 503; the body carries exactly the state
        we want to report, so it has to be unwrapped rather than propagated."""

        class FakeHTTPError(urllib.error.HTTPError):
            def __init__(self):
                super().__init__(
                    "http://x/health", 503, "Service Unavailable", {}, None
                )

            def read(self):
                return json.dumps({"status": "stuck"}).encode()

        def raise_503(*_args, **_kwargs):
            raise FakeHTTPError()

        monkeypatch.setattr(doctor.urllib.request, "urlopen", raise_503)
        code, body = doctor.probe_health("http://x")
        assert code == 503
        assert body["status"] == "stuck"


class TestAggregate:
    def test_worst_status_prefers_the_most_severe(self):
        results = [
            doctor.CheckResult("a", doctor.Status.OK, ""),
            doctor.CheckResult("b", doctor.Status.WARN, ""),
            doctor.CheckResult("c", doctor.Status.FAIL, ""),
        ]
        assert doctor.worst_status(results) is doctor.Status.FAIL

    def test_all_off_is_not_a_warning(self):
        results = [doctor.CheckResult("a", doctor.Status.OFF, "")]
        assert doctor.worst_status(results) is doctor.Status.OFF

    def test_run_all_checks_covers_every_satellite(self, tmp_path):
        names = {r.name for r in doctor.run_all_checks(tmp_path, {})}
        for label, _, _ in doctor.SATELLITES:
            assert label in names


class TestCommand:
    def test_exits_zero_when_nothing_failed(self, monkeypatch):
        monkeypatch.setattr(
            doctor,
            "run_all_checks",
            lambda *a, **k: [doctor.CheckResult("thing", doctor.Status.OK, "fine")],
        )
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "All good." in result.output

    def test_exits_nonzero_and_names_the_failure(self, monkeypatch):
        monkeypatch.setattr(
            doctor,
            "run_all_checks",
            lambda *a, **k: [
                doctor.CheckResult("ffmpeg", doctor.Status.FAIL, "gone", "run uv sync")
            ],
        )
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code != 0
        assert "ffmpeg" in result.output
        assert "run uv sync" in result.output

    def test_warnings_do_not_fail_the_command(self, monkeypatch):
        """An optional service being down must not break a deploy gate -- core
        rendering still works, which is the promise the whole degrade-cleanly
        design makes."""
        monkeypatch.setattr(
            doctor,
            "run_all_checks",
            lambda *a, **k: [
                doctor.CheckResult("Music", doctor.Status.WARN, "down", "start it")
            ],
        )
        result = CliRunner().invoke(cli, ["doctor"])
        assert result.exit_code == 0
        assert "start it" in result.output

    def test_fix_is_hidden_for_passing_checks(self, monkeypatch):
        monkeypatch.setattr(
            doctor,
            "run_all_checks",
            lambda *a, **k: [
                doctor.CheckResult(
                    "ok thing", doctor.Status.OK, "fine", "irrelevant hint"
                )
            ],
        )
        result = CliRunner().invoke(cli, ["doctor"])
        assert "irrelevant hint" not in result.output


@pytest.mark.parametrize("status", list(doctor.Status))
def test_every_status_has_a_cli_marker(status):
    """A new Status without a marker would KeyError at render time, in the one
    command people run when something is already wrong."""
    from cinemagraph.cli import _DOCTOR_MARKERS

    assert status in _DOCTOR_MARKERS
