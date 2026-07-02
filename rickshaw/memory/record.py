"""MemoryRecord — the core data unit for the semantic memory layer."""

from __future__ import annotations

import enum
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone


class MemoryScope(enum.Enum):
    GLOBAL = "global"
    PROJECT = "project"
    SESSION = "session"
    TASK = "task"


class MemoryType(enum.Enum):
    FACT = "fact"
    DECISION = "decision"
    TOOL_RESULT = "tool_result"
    ERROR = "error"
    PREFERENCE = "preference"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class MemoryRecord:
    """A single memory entry operated on by every component."""

    id: str = field(default_factory=_new_id)
    text: str = ""
    embedding: list[float] = field(default_factory=list)
    scope: MemoryScope = MemoryScope.SESSION
    type: MemoryType = MemoryType.FACT
    importance: float = 0.0
    created_at: datetime = field(default_factory=_utcnow)
    last_used_at: datetime = field(default_factory=_utcnow)
    use_count: int = 0
    sensitive: bool = False
    superseded_by: str | None = None
    extra: dict = field(default_factory=dict)
