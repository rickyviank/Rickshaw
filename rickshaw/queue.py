"""Simple persistent/in-memory job queue for deferred work."""

from __future__ import annotations

import enum
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


class JobType(enum.Enum):
    IMPORTANCE_SCORING = "importance_scoring"
    COMPACTION = "compaction"
    EVICTION = "eviction"


class JobStatus(enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class Job:
    """A deferred work item."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    type: JobType = JobType.IMPORTANCE_SCORING
    payload: dict = field(default_factory=dict)
    status: JobStatus = JobStatus.PENDING
    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    error: str | None = None


class JobQueue:
    """Thread-safe in-memory FIFO job queue."""

    def __init__(self) -> None:
        self._queue: deque[Job] = deque()
        self._completed: list[Job] = []

    def enqueue(self, job: Job) -> None:
        self._queue.append(job)

    def dequeue(self) -> Job | None:
        if not self._queue:
            return None
        return self._queue.popleft()

    def mark_completed(self, job: Job) -> None:
        job.status = JobStatus.COMPLETED
        self._completed.append(job)

    def mark_failed(self, job: Job, error: str) -> None:
        job.status = JobStatus.FAILED
        job.error = error
        self._completed.append(job)

    @property
    def pending_count(self) -> int:
        return len(self._queue)

    @property
    def completed_jobs(self) -> list[Job]:
        return list(self._completed)
