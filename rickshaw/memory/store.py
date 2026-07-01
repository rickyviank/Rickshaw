"""SQLite-backed persistence for MemoryRecords."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rickshaw.memory._math import cosine_similarity
from rickshaw.memory.record import MemoryRecord, MemoryScope, MemoryType

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    text TEXT NOT NULL,
    embedding TEXT NOT NULL,
    scope TEXT NOT NULL,
    type TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.0,
    created_at TEXT NOT NULL,
    last_used_at TEXT NOT NULL,
    use_count INTEGER NOT NULL DEFAULT 0,
    sensitive INTEGER NOT NULL DEFAULT 0,
    superseded_by TEXT
);
"""


def _dt_to_iso(dt: datetime) -> str:
    return dt.isoformat()


def _iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


class MemoryStore:
    """SQLite-backed store for MemoryRecords."""

    def __init__(self, db_path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def put(self, record: MemoryRecord) -> None:
        self._conn.execute(
            """INSERT OR REPLACE INTO memories
               (id, text, embedding, scope, type, importance,
                created_at, last_used_at, use_count, sensitive, superseded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id,
                record.text,
                json.dumps(record.embedding),
                record.scope.value,
                record.type.value,
                record.importance,
                _dt_to_iso(record.created_at),
                _dt_to_iso(record.last_used_at),
                record.use_count,
                int(record.sensitive),
                record.superseded_by,
            ),
        )
        self._conn.commit()

    def get(self, record_id: str) -> MemoryRecord | None:
        row = self._conn.execute(
            "SELECT * FROM memories WHERE id = ?", (record_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def search(
        self,
        query_vec: list[float],
        scope_filter: list[MemoryScope] | None = None,
        limit: int = 20,
    ) -> list[tuple[MemoryRecord, float]]:
        """Return records ranked by cosine similarity, with optional scope filter.

        Metadata scope filter is applied FIRST in SQL, then brute-force
        cosine similarity over candidate rows.
        """
        if scope_filter:
            placeholders = ",".join("?" for _ in scope_filter)
            query = (
                f"SELECT * FROM memories WHERE scope IN ({placeholders}) "
                "AND superseded_by IS NULL"
            )
            rows = self._conn.execute(
                query, [s.value for s in scope_filter]
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM memories WHERE superseded_by IS NULL"
            ).fetchall()

        scored: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            record = self._row_to_record(row)
            sim = cosine_similarity(query_vec, record.embedding)
            scored.append((record, sim))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored[:limit]

    def update(self, record: MemoryRecord) -> None:
        self.put(record)

    def mark_superseded(self, record_id: str, superseded_by: str) -> None:
        self._conn.execute(
            "UPDATE memories SET superseded_by = ? WHERE id = ?",
            (superseded_by, record_id),
        )
        self._conn.commit()

    def all_records(
        self, scope_filter: list[MemoryScope] | None = None,
    ) -> list[MemoryRecord]:
        if scope_filter:
            placeholders = ",".join("?" for _ in scope_filter)
            rows = self._conn.execute(
                f"SELECT * FROM memories WHERE scope IN ({placeholders})",
                [s.value for s in scope_filter],
            ).fetchall()
        else:
            rows = self._conn.execute("SELECT * FROM memories").fetchall()
        return [self._row_to_record(r) for r in rows]

    def delete(self, record_id: str) -> bool:
        cursor = self._conn.execute(
            "DELETE FROM memories WHERE id = ?", (record_id,)
        )
        self._conn.commit()
        return cursor.rowcount > 0

    @staticmethod
    def _row_to_record(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            text=row["text"],
            embedding=json.loads(row["embedding"]),
            scope=MemoryScope(row["scope"]),
            type=MemoryType(row["type"]),
            importance=row["importance"],
            created_at=_iso_to_dt(row["created_at"]),
            last_used_at=_iso_to_dt(row["last_used_at"]),
            use_count=row["use_count"],
            sensitive=bool(row["sensitive"]),
            superseded_by=row["superseded_by"],
        )
