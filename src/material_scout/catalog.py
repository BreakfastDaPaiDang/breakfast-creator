from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from .models import ContentAsset, Representation


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    source TEXT NOT NULL,
    remote_id TEXT,
    source_url TEXT NOT NULL,
    creator TEXT,
    published_at TEXT,
    acquired_at TEXT,
    rights_status TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS representations (
    representation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT,
    sha256 TEXT,
    derived_from TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, path)
);

CREATE TABLE IF NOT EXISTS fragments (
    fragment_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    representation_id INTEGER REFERENCES representations(representation_id),
    fragment_kind TEXT NOT NULL,
    locator_json TEXT NOT NULL,
    description TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS visible_marks (
    mark_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    mark_kind TEXT NOT NULL,
    authorization TEXT NOT NULL,
    location_json TEXT,
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS usages (
    usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE RESTRICT,
    fragment_id TEXT REFERENCES fragments(fragment_id),
    project_path TEXT NOT NULL,
    purpose TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Catalog:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Catalog":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def upsert_asset(self, asset: ContentAsset) -> None:
        provenance = asset.provenance
        self.connection.execute(
            """
            INSERT INTO assets (
                asset_id, kind, title, source, remote_id, source_url, creator,
                published_at, acquired_at, rights_status, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id) DO UPDATE SET
                title=excluded.title,
                rights_status=excluded.rights_status,
                acquired_at=excluded.acquired_at,
                metadata_json=excluded.metadata_json,
                updated_at=CURRENT_TIMESTAMP
            """,
            (
                asset.asset_id,
                asset.kind.value,
                asset.title,
                provenance.source,
                provenance.remote_id,
                provenance.source_url,
                provenance.creator,
                provenance.published_at,
                provenance.acquired_at,
                asset.rights_status.value,
                json.dumps(asset.metadata, ensure_ascii=False),
            ),
        )
        for representation in asset.representations:
            self.add_representation(asset.asset_id, representation)
        self.connection.commit()

    def add_representation(self, asset_id: str, representation: Representation) -> None:
        self.connection.execute(
            """
            INSERT INTO representations (
                asset_id, role, path, mime_type, sha256, derived_from, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_id, path) DO UPDATE SET
                role=excluded.role,
                mime_type=excluded.mime_type,
                sha256=excluded.sha256,
                derived_from=excluded.derived_from,
                metadata_json=excluded.metadata_json
            """,
            (
                asset_id,
                representation.role.value,
                representation.path,
                representation.mime_type,
                representation.sha256,
                representation.derived_from,
                json.dumps(representation.metadata, ensure_ascii=False),
            ),
        )

    def list_assets(self, limit: int = 100) -> list[dict[str, object]]:
        rows = self.connection.execute(
            """
            SELECT asset_id, kind, title, source, source_url, rights_status, created_at
            FROM assets ORDER BY created_at DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_asset(self, asset_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            "SELECT * FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        return dict(row) if row else None

    def record_mark(
        self,
        asset_id: str,
        status: str,
        mark_kind: str,
        authorization: str,
        notes: str | None = None,
    ) -> int:
        cursor = self.connection.execute(
            """
            INSERT INTO visible_marks (asset_id, status, mark_kind, authorization, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            (asset_id, status, mark_kind, authorization, notes),
        )
        self.connection.commit()
        return int(cursor.lastrowid)

    def latest_mark(self, asset_id: str) -> dict[str, object] | None:
        row = self.connection.execute(
            """
            SELECT * FROM visible_marks WHERE asset_id = ?
            ORDER BY mark_id DESC LIMIT 1
            """,
            (asset_id,),
        ).fetchone()
        return dict(row) if row else None
