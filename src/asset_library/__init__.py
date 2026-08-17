"""Local asset library: persistent storage for source images/videos and
generated outputs (cinemagraphs, AI-generated music, sound effects, masks,
whatever else), so they can be looked up again later instead of being
forgotten the moment a render finishes.

This is its own top-level package, not part of `cinemagraph` -- it started
there, but its job is explicitly cross-cutting: every service in this project
(the cinemagraph pipeline, ACE-Step music, Stable Audio sound effects,
CLIPSeg masks) should be able to catalog its output the same way, and none of
them should have to import a package named after one specific feature to do
it. Both the CLI and the API need the same storage/lookup logic, and unlike
server/jobs.py's in-memory, restart-losable job tracking, a library is meant
to survive restarts by design -- it's about the tool's own data, not one HTTP
request's bookkeeping. See docs/DESIGN.md sec 5.1 for the design rationale.

Storage shape, chosen to match a well-worn pattern (see Hydrus/Immich):
- files are content-addressed by SHA-256 under <library_root>/objects/,
  which gets deduplication for free (adding the same file twice is a no-op
  on disk, just a metadata touch)
- metadata lives in a small SQLite database (stdlib `sqlite3`, no new
  dependency) at <library_root>/index.sqlite3

Library root defaults to ~/.cinemagraph/library, overridable via
CINEMAGRAPH_LIBRARY_DIR -- independent of CINEMAGRAPH_DATA_DIR (the API's
ephemeral per-job scratch space), since the library needs to be useful from
the CLI alone, with no API involved. The env var/default path still say
"cinemagraph" because that's the product's own data home (~/.cinemagraph/),
not a reference to the Python package of the same name.

Projects (2026-08-05) are a reserved-prefix tag (PROJECT_TAG_PREFIX,
"project:") rather than a second table -- consistent with this module's own
Hydrus-inspired "tags not folders" shape, and needs no schema migration.
set_project()/list_projects()/rename_project()/delete_project() manage that
tag; list_assets(project=...) filters by it the same way tag= already did.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timezone
from pathlib import Path

KINDS = ("reference", "source", "generated")

# Projects are deliberately *not* a second table -- they're a reserved-prefix
# tag (see docs/DESIGN.md sec 5.1's own "tags not folders" rationale). That
# gets grouping/filtering essentially for free (list_assets(tag=...) already
# existed) with zero schema migration, at the cost of enforcing "one project
# per asset" ourselves (set_project below strips any other project:* tag
# before adding the new one) rather than the database doing it via a column.
PROJECT_TAG_PREFIX = "project:"


@dataclass(frozen=True)
class Asset:
    id: str  # sha256 hex digest, also the primary key
    kind: str
    original_filename: str
    added_at: str  # ISO 8601 UTC
    tags: list[str]
    provenance: dict | None
    path: Path  # absolute path to the stored file


def library_root() -> Path:
    root = Path(
        os.environ.get("CINEMAGRAPH_LIBRARY_DIR", "~/.cinemagraph/library")
    ).expanduser()
    root.mkdir(parents=True, exist_ok=True)
    (root / "objects").mkdir(exist_ok=True)
    return root


def _db_path(root: Path | None = None) -> Path:
    return (root or library_root()) / "index.sqlite3"


def _connect(root: Path | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(root))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE IF NOT EXISTS assets (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL,
            original_filename TEXT NOT NULL,
            added_at TEXT NOT NULL,
            extension TEXT NOT NULL,
            tags TEXT NOT NULL DEFAULT '',
            provenance TEXT
        )
    """)
    return conn


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_to_asset(row: sqlite3.Row, root: Path) -> Asset:
    return Asset(
        id=row["id"],
        kind=row["kind"],
        original_filename=row["original_filename"],
        added_at=row["added_at"],
        tags=[t for t in row["tags"].split(",") if t],
        provenance=json.loads(row["provenance"]) if row["provenance"] else None,
        path=root / "objects" / f"{row['id']}{row['extension']}",
    )


def add(
    path: str,
    kind: str = "reference",
    tags: list[str] | None = None,
    provenance: dict | None = None,
    original_filename: str | None = None,
) -> Asset:
    """Register a file in the library. Re-adding identical bytes is a no-op
    on disk (same hash -> same object) but still updates kind/tags/provenance
    if given, so re-tagging an existing asset is just calling add() again.

    `original_filename` overrides the name recorded in metadata -- needed
    when `path` is a temp file (e.g. an API upload staged to disk before
    hashing) whose own name isn't the name worth remembering."""
    if kind not in KINDS:
        raise ValueError(f"Unknown kind '{kind}'. Choose from: {', '.join(KINDS)}")

    src = Path(path)
    if not src.is_file():
        raise FileNotFoundError(f"Not a file: {path}")

    root = library_root()
    asset_id = _hash_file(src)
    recorded_name = original_filename or src.name
    extension = Path(recorded_name).suffix.lower() or src.suffix.lower()
    dest = root / "objects" / f"{asset_id}{extension}"
    if not dest.exists():
        shutil.copy2(src, dest)

    conn = _connect(root)
    try:
        existing = conn.execute(
            "SELECT * FROM assets WHERE id = ?", (asset_id,)
        ).fetchone()
        added_at = existing["added_at"] if existing else datetime.now(UTC).isoformat()
        conn.execute(
            """
            INSERT INTO assets (id, kind, original_filename, added_at, extension, tags, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, tags=excluded.tags, provenance=excluded.provenance
            """,
            (
                asset_id,
                kind,
                recorded_name,
                added_at,
                extension,
                ",".join(tags or []),
                json.dumps(provenance) if provenance else None,
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_asset(row, root)
    finally:
        conn.close()


def get(asset_id: str) -> Asset | None:
    root = library_root()
    conn = _connect(root)
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_asset(row, root) if row else None
    finally:
        conn.close()


def list_assets(
    kind: str | None = None, tag: str | None = None, project: str | None = None
) -> list[Asset]:
    root = library_root()
    conn = _connect(root)
    try:
        query = "SELECT * FROM assets"
        clauses, params = [], []
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind)
        if tag is not None:
            clauses.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{tag},%")
        if project is not None:
            clauses.append("(',' || tags || ',') LIKE ?")
            params.append(f"%,{PROJECT_TAG_PREFIX}{project},%")
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY added_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [_row_to_asset(row, root) for row in rows]
    finally:
        conn.close()


def remove(asset_id: str, delete_file: bool = True) -> bool:
    """Remove an asset's metadata (and, by default, its stored file). Returns
    False if the asset wasn't found."""
    root = library_root()
    conn = _connect(root)
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return False
        conn.execute("DELETE FROM assets WHERE id = ?", (asset_id,))
        conn.commit()
        if delete_file:
            asset = _row_to_asset(row, root)
            asset.path.unlink(missing_ok=True)
        return True
    finally:
        conn.close()


def set_project(asset_id: str, project: str | None) -> Asset:
    """Assigns an asset to a project (or clears it, if project=None) by
    replacing any existing project:* tag with the new one -- a direct UPDATE
    on the tags column, not a re-add(), since no file content is changing.
    Raises ValueError if asset_id isn't in the library."""
    root = library_root()
    conn = _connect(root)
    try:
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            raise ValueError(f"No asset with id '{asset_id}'")
        tags = [
            t
            for t in row["tags"].split(",")
            if t and not t.startswith(PROJECT_TAG_PREFIX)
        ]
        if project:
            tags.append(f"{PROJECT_TAG_PREFIX}{project}")
        conn.execute(
            "UPDATE assets SET tags = ? WHERE id = ?", (",".join(tags), asset_id)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        return _row_to_asset(row, root)
    finally:
        conn.close()


def list_projects() -> list[str]:
    """Every distinct project name currently in use, sorted -- there's no
    separate projects table to query (see PROJECT_TAG_PREFIX's own docstring),
    so this scans tags for the reserved prefix instead."""
    root = library_root()
    conn = _connect(root)
    try:
        rows = conn.execute(
            "SELECT DISTINCT tags FROM assets WHERE tags LIKE ?",
            (f"%{PROJECT_TAG_PREFIX}%",),
        ).fetchall()
        names = set()
        for row in rows:
            for t in row["tags"].split(","):
                if t.startswith(PROJECT_TAG_PREFIX):
                    names.add(t[len(PROJECT_TAG_PREFIX) :])
        return sorted(names)
    finally:
        conn.close()


def _bulk_retag(old_tag: str, new_tag: str | None) -> int:
    """Replaces one exact tag with another (or removes it, if new_tag=None)
    across every asset that has it. Shared by rename_project/delete_project
    -- both are really the same "swap this tag everywhere" operation."""
    root = library_root()
    conn = _connect(root)
    try:
        rows = conn.execute(
            "SELECT id, tags FROM assets WHERE (',' || tags || ',') LIKE ?",
            (f"%,{old_tag},%",),
        ).fetchall()
        for row in rows:
            tags = [t for t in row["tags"].split(",") if t and t != old_tag]
            if new_tag:
                tags.append(new_tag)
            conn.execute(
                "UPDATE assets SET tags = ? WHERE id = ?", (",".join(tags), row["id"])
            )
        conn.commit()
        return len(rows)
    finally:
        conn.close()


def rename_project(old: str, new: str) -> int:
    """Renames a project across every asset that has it. Returns the count
    of assets updated (0 if no asset currently uses `old`)."""
    return _bulk_retag(f"{PROJECT_TAG_PREFIX}{old}", f"{PROJECT_TAG_PREFIX}{new}")


def delete_project(name: str) -> int:
    """Un-assigns a project from every asset that has it -- the assets
    themselves are untouched, only the project:* tag is removed. Returns the
    count of assets updated."""
    return _bulk_retag(f"{PROJECT_TAG_PREFIX}{name}", None)
