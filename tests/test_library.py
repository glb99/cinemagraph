"""Contract tests for the asset library: content-addressed storage +
SQLite metadata. library_root() reads CINEMAGRAPH_LIBRARY_DIR fresh on every
call (unlike server/app.py's DATA_DIR, which is cached at import time), so
monkeypatch.setenv per test is enough -- no importlib.reload dance needed.
"""
import pytest

import asset_library as library


@pytest.fixture(autouse=True)
def isolated_library(tmp_path, monkeypatch):
    monkeypatch.setenv("CINEMAGRAPH_LIBRARY_DIR", str(tmp_path / "library"))


@pytest.fixture
def sample_file(tmp_path):
    f = tmp_path / "sample.jpg"
    f.write_bytes(b"fake jpg content")
    return f


def test_add_returns_asset_with_expected_fields(sample_file):
    asset = library.add(str(sample_file), kind="reference", tags=["sky", "concept"])
    assert asset.kind == "reference"
    assert asset.original_filename == "sample.jpg"
    assert set(asset.tags) == {"sky", "concept"}
    assert asset.provenance is None
    assert asset.path.exists()
    assert asset.path.read_bytes() == b"fake jpg content"


def test_add_rejects_unknown_kind(sample_file):
    with pytest.raises(ValueError):
        library.add(str(sample_file), kind="not_a_real_kind")


def test_add_rejects_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        library.add(str(tmp_path / "does_not_exist.jpg"))


def test_readding_identical_content_dedups_and_updates_metadata(sample_file):
    first = library.add(str(sample_file), kind="reference", tags=["old"])
    second = library.add(str(sample_file), kind="generated", tags=["new"])

    assert first.id == second.id  # same content -> same id
    assert second.kind == "generated"
    assert second.tags == ["new"]
    assert second.added_at == first.added_at  # added_at is preserved, not reset

    # Only one object file should exist on disk for this content.
    assert len(list(library.library_root().glob("objects/*"))) == 1


def test_original_filename_override(sample_file):
    asset = library.add(str(sample_file), original_filename="renamed.jpg")
    assert asset.original_filename == "renamed.jpg"
    assert asset.path.suffix == ".jpg"


def test_get_returns_none_for_missing_asset():
    assert library.get("deadbeef") is None


def test_get_returns_matching_asset(sample_file):
    added = library.add(str(sample_file))
    fetched = library.get(added.id)
    assert fetched == added


def test_list_assets_orders_newest_first(tmp_path):
    f1 = tmp_path / "a.jpg"
    f1.write_bytes(b"content a")
    f2 = tmp_path / "b.jpg"
    f2.write_bytes(b"content b")

    asset1 = library.add(str(f1))
    asset2 = library.add(str(f2))

    listed = library.list_assets()
    assert [a.id for a in listed] == [asset2.id, asset1.id]


def test_list_assets_filters_by_kind(tmp_path):
    f1 = tmp_path / "a.jpg"
    f1.write_bytes(b"content a")
    f2 = tmp_path / "b.jpg"
    f2.write_bytes(b"content b")

    library.add(str(f1), kind="reference")
    generated = library.add(str(f2), kind="generated")

    result = library.list_assets(kind="generated")
    assert [a.id for a in result] == [generated.id]


def test_list_assets_filters_by_tag_without_prefix_collisions(tmp_path):
    """A tag filter for 'sky' must not match an asset only tagged 'skyline'."""
    f1 = tmp_path / "a.jpg"
    f1.write_bytes(b"content a")
    f2 = tmp_path / "b.jpg"
    f2.write_bytes(b"content b")

    sky_asset = library.add(str(f1), tags=["sky", "concept"])
    library.add(str(f2), tags=["skyline"])

    result = library.list_assets(tag="sky")
    assert [a.id for a in result] == [sky_asset.id]


def test_remove_deletes_metadata_and_file_by_default(sample_file):
    asset = library.add(str(sample_file))
    assert library.remove(asset.id) is True
    assert library.get(asset.id) is None
    assert not asset.path.exists()


def test_remove_can_keep_file_on_disk(sample_file):
    asset = library.add(str(sample_file))
    library.remove(asset.id, delete_file=False)
    assert library.get(asset.id) is None
    assert asset.path.exists()


def test_remove_returns_false_for_missing_asset():
    assert library.remove("deadbeef") is False


def test_provenance_round_trips(sample_file):
    provenance = {"prompt": "a lofi bedroom", "effect": "smoke"}
    asset = library.add(str(sample_file), kind="generated", provenance=provenance)
    assert asset.provenance == provenance
    assert library.get(asset.id).provenance == provenance
