"""Local reference library: persistent storage for source images/videos and
generated outputs, so they can be looked up again later instead of being
forgotten the moment a render finishes.

This is deliberately a core-tier module (not under server/): both the CLI and
the API need the same storage/lookup logic, and unlike server/jobs.py's
in-memory, restart-losable job tracking, a library is meant to survive
restarts by design -- it's about the tool's own data, not one HTTP
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
the CLI alone, with no API involved.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KINDS = ("reference", "source", "generated")


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
    root = Path(os.environ.get("CINEMAGRAPH_LIBRARY_DIR", "~/.cinemagraph/library")).expanduser()
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
        existing = conn.execute("SELECT * FROM assets WHERE id = ?", (asset_id,)).fetchone()
        added_at = existing["added_at"] if existing else datetime.now(timezone.utc).isoformat()
        conn.execute(
            """
            INSERT INTO assets (id, kind, original_filename, added_at, extension, tags, provenance)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                kind=excluded.kind, tags=excluded.tags, provenance=excluded.provenance
            """,
            (
                asset_id, kind, recorded_name, added_at, extension,
                ",".join(tags or []), json.dumps(provenance) if provenance else None,
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


def list_assets(kind: str | None = None, tag: str | None = None) -> list[Asset]:
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
