"""SQLite-backed persistence for MemoryRecords."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from rickshaw.memory._math import cosine_similarity
from rickshaw.memory._vector_ext import pack_float32, try_load_extension
from rickshaw.memory.record import MemoryRecord, MemoryScope, MemoryType

logger = logging.getLogger(__name__)

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

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        vector_dim: int | None = None,
        use_vector_ext: bool = True,
    ) -> None:
        self._conn = sqlite3.connect(str(db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(_SCHEMA)
        self._conn.commit()

        # Optional indexed vector search via the sqlite-vector extension.
        # Falls back to brute-force cosine when unavailable (see _vector_ext).
        self._vector_dim = vector_dim
        self._vector_enabled = False
        if use_vector_ext and vector_dim:
            self._vector_enabled = try_load_extension(self._conn)
            if self._vector_enabled:
                self._init_vector_column()

    @property
    def vector_search_enabled(self) -> bool:
        """Whether indexed vector search (sqlite-vector) is active."""
        return self._vector_enabled

    def _init_vector_column(self) -> None:
        """Add and initialize the FLOAT32 vector column for the extension."""
        try:
            cols = {
                r["name"]
                for r in self._conn.execute("PRAGMA table_info(memories)").fetchall()
            }
            if "embedding_vec" not in cols:
                self._conn.execute("ALTER TABLE memories ADD COLUMN embedding_vec BLOB")
            self._conn.execute(
                "SELECT vector_init('memories', 'embedding_vec', ?)",
                (f"dimension={self._vector_dim},type=FLOAT32,distance=cosine",),
            )
            self._conn.commit()
        except Exception as exc:
            logger.warning(
                "Failed to initialize sqlite-vector column (%s); "
                "using brute-force cosine fallback.",
                exc,
            )
            self._vector_enabled = False

    def close(self) -> None:
        self._conn.close()

    def put(self, record: MemoryRecord) -> None:
        # embedding is always stored as JSON text for reconstruction and the
        # brute-force fallback; embedding_vec (FLOAT32 blob) is populated only
        # when the sqlite-vector extension is active.
        if self._vector_enabled:
            self._conn.execute(
                """INSERT OR REPLACE INTO memories
                   (id, text, embedding, scope, type, importance,
                    created_at, last_used_at, use_count, sensitive,
                    superseded_by, embedding_vec)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                    pack_float32(record.embedding),
                ),
            )
        else:
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
        """Return records ranked by similarity, with optional scope filter.

        The metadata scope filter is applied FIRST in SQL. When the
        sqlite-vector extension is loaded, the ranked KNN search runs inside
        SQLite; otherwise we fall back to a brute-force cosine scan over the
        scope-filtered candidate rows.
        """
        if self._vector_enabled:
            try:
                return self._search_vector_ext(query_vec, scope_filter, limit)
            except Exception as exc:
                logger.warning(
                    "sqlite-vector search failed (%s); "
                    "falling back to brute-force cosine scan.",
                    exc,
                )
        return self._search_bruteforce(query_vec, scope_filter, limit)

    def _search_vector_ext(
        self,
        query_vec: list[float],
        scope_filter: list[MemoryScope] | None,
        limit: int,
    ) -> list[tuple[MemoryRecord, float]]:
        """KNN search via sqlite-vector, scope-filtered in SQL first.

        Uses streaming mode (no ``k`` arg) so the SQL ``WHERE`` scope filter is
        applied before ranking. cosine *distance* is converted to similarity.
        """
        where = ["m.superseded_by IS NULL"]
        params: list[object] = [pack_float32(query_vec)]
        if scope_filter:
            placeholders = ",".join("?" for _ in scope_filter)
            where.append(f"m.scope IN ({placeholders})")
            params.extend(s.value for s in scope_filter)
        params.append(limit)
        query = (
            "SELECT m.*, v.distance AS _distance "
            "FROM vector_full_scan('memories', 'embedding_vec', ?) AS v "
            "JOIN memories m ON m.rowid = v.rowid "
            f"WHERE {' AND '.join(where)} "
            "ORDER BY v.distance ASC LIMIT ?"
        )
        rows = self._conn.execute(query, params).fetchall()
        scored: list[tuple[MemoryRecord, float]] = []
        for row in rows:
            record = self._row_to_record(row)
            sim = 1.0 - float(row["_distance"])
            scored.append((record, sim))
        return scored

    def _search_bruteforce(
        self,
        query_vec: list[float],
        scope_filter: list[MemoryScope] | None,
        limit: int,
    ) -> list[tuple[MemoryRecord, float]]:
        """Scope-filter in SQL, then brute-force cosine over candidate rows."""
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
