"""SQLite-backed persistence for turn traces.

Events are queued in memory and flushed by a background thread so the hot path
is never blocked by disk I/O. The store shares the same SQLite database as the
memory layer (``rickshaw_memory.db``) but keeps its own tables.
"""

from __future__ import annotations

import json
import logging
import queue
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rickshaw.events import TurnEvent

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS traces (
    turn_id TEXT PRIMARY KEY,
    task_input TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'in_progress'
);

CREATE TABLE IF NOT EXISTS trace_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    timestamp TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_trace_events_turn ON trace_events(turn_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_to_row(event: TurnEvent, seq: int) -> tuple[str, int, str, str, str]:
    data = event.model_dump(mode="json")
    return (
        data["type"],
        seq,
        data["type"],
        json.dumps(data),
        data.get("timestamp") or _now_iso(),
    )


class TraceStore:
    """Asynchronous trace store.

    Create once and reuse. Call :meth:`close` at shutdown to flush and stop the
    background thread. Events are durable after :meth:`flush` returns or after
    the background thread has processed them.
    """

    def __init__(
        self,
        db_path: str | Path = ":memory:",
        *,
        max_queue_size: int = 10000,
    ) -> None:
        self._db_path = str(db_path)
        self._queue: "queue.Queue[dict[str, Any] | None]" = queue.Queue(
            maxsize=max_queue_size
        )
        self._seqs: dict[str, int] = {}
        self._closed = False
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def _worker(self) -> None:
        """Background thread that owns the SQLite connection."""
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.executescript(_SCHEMA)
        conn.commit()
        try:
            while True:
                item = self._queue.get()
                if item is None:
                    break
                try:
                    self._handle(conn, item)
                except Exception as exc:  # noqa: BLE001 - keep worker alive
                    logger.warning("Trace store failed to handle item: %s", exc)
        finally:
            conn.close()

    def _handle(self, conn: sqlite3.Connection, item: dict[str, Any]) -> None:
        kind = item["kind"]
        now = _now_iso()
        if kind == "start":
            conn.execute(
                """INSERT OR REPLACE INTO traces
                   (turn_id, task_input, created_at, updated_at, status)
                   VALUES (?, ?, ?, ?, ?)""",
                (item["turn_id"], item["task_input"], now, now, "in_progress"),
            )
        elif kind == "emit":
            turn_id = item["turn_id"]
            seq = self._seqs.get(turn_id, 0)
            self._seqs[turn_id] = seq + 1
            event = item["event"]
            data = event.model_dump(mode="json")
            conn.execute(
                """INSERT INTO trace_events
                   (turn_id, seq, event_type, payload, timestamp)
                   VALUES (?, ?, ?, ?, ?)""",
                (turn_id, seq, data["type"], json.dumps(data), data.get("timestamp") or now),
            )
            conn.execute(
                "UPDATE traces SET updated_at = ? WHERE turn_id = ?",
                (now, turn_id),
            )
        elif kind == "finish":
            conn.execute(
                "UPDATE traces SET status = ?, updated_at = ? WHERE turn_id = ?",
                (item["status"], now, item["turn_id"]),
            )
            self._seqs.pop(item["turn_id"], None)
        elif kind == "flush":
            conn.commit()
            item["done"].set()
            return
        conn.commit()

    def start_trace(self, turn_id: str, task_input: str) -> None:
        """Record the beginning of a turn."""
        if self._closed:
            return
        self._queue.put({
            "kind": "start",
            "turn_id": turn_id,
            "task_input": task_input,
        })

    def emit(self, turn_id: str, event: TurnEvent) -> None:
        """Queue an event for asynchronous persistence."""
        if self._closed:
            return
        self._queue.put({"kind": "emit", "turn_id": turn_id, "event": event})

    def finish_trace(self, turn_id: str, status: str = "completed") -> None:
        """Mark a turn trace as completed, interrupted, or failed."""
        if self._closed:
            return
        self._queue.put({
            "kind": "finish",
            "turn_id": turn_id,
            "status": status,
        })

    def flush(self, timeout: float | None = None) -> bool:
        """Wait until all queued items have been written."""
        if self._closed or not self._thread.is_alive():
            return True
        done = threading.Event()
        self._queue.put({"kind": "flush", "done": done})
        return done.wait(timeout=timeout)

    def close(self) -> None:
        """Flush and stop the background thread."""
        if self._closed:
            return
        self._closed = True
        self.flush(timeout=5.0)
        self._queue.put(None)
        self._thread.join(timeout=5.0)

    def get_trace(self, turn_id: str) -> dict[str, Any] | None:
        """Synchronously read a trace and its events from the store.

        Useful for tests and future query commands. Not used on the hot path.
        """
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM traces WHERE turn_id = ?", (turn_id,)
            ).fetchone()
            if row is None:
                return None
            events = conn.execute(
                "SELECT payload FROM trace_events WHERE turn_id = ? ORDER BY seq",
                (turn_id,),
            ).fetchall()
            return {
                "turn_id": row["turn_id"],
                "task_input": row["task_input"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
                "status": row["status"],
                "events": [json.loads(e["payload"]) for e in events],
            }
        finally:
            conn.close()


def new_turn_id() -> str:
    """Return a unique turn identifier."""
    return uuid.uuid4().hex
