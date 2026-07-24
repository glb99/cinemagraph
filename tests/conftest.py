"""Shared fixtures: synthetic video/photo inputs generated fresh into tmp_path,
reusing the same generators the README points users at (examples/make_test_clip.py,
examples/make_test_photo.py) so there's exactly one place that logic lives.
"""
import importlib.util
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def _load_example_module(name: str):
    spec = importlib.util.spec_from_file_location(name, EXAMPLES_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def _make_test_clip():
    return _load_example_module("make_test_clip").main


@pytest.fixture(scope="session")
def _make_test_photo():
    return _load_example_module("make_test_photo").main


@pytest.fixture
def test_video(tmp_path, _make_test_clip):
    return str(_make_test_clip(tmp_path / "input.mp4"))


@pytest.fixture
def test_photo(tmp_path, _make_test_photo):
    return str(_make_test_photo(tmp_path / "photo.jpg"))
