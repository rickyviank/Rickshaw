"""Weighted-sum ranking with optional MMR-style diversity penalty."""

from __future__ import annotations

import math
from datetime import datetime, timezone

from rickshaw.memory._math import cosine_similarity
from rickshaw.memory.record import MemoryRecord


def _time_decay(record: MemoryRecord, half_life_hours: float = 24.0) -> float:
    """Exponential recency score in (0, 1]."""
    now = datetime.now(timezone.utc)
    age_hours = max((now - record.last_used_at).total_seconds() / 3600, 0)
    return math.exp(-math.log(2) * age_hours / half_life_hours)


class Ranker:
    """Score and re-rank MemoryRecords.

    ``score = w_rel * relevance + w_rec * recency + w_imp * importance``
    """

    def __init__(
        self,
        w_rel: float = 0.5,
        w_rec: float = 0.3,
        w_imp: float = 0.2,
        diversity_penalty: float = 0.0,
    ) -> None:
        self.w_rel = w_rel
        self.w_rec = w_rec
        self.w_imp = w_imp
        self.diversity_penalty = diversity_penalty

    def rank(
        self,
        candidates: list[tuple[MemoryRecord, float]],
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """Return the top *limit* records by weighted score.

        *candidates* is a list of (record, relevance_score) tuples as
        returned by :meth:`MemoryStore.search`.
        """
        if not candidates:
            return []

        scored = [
            (record, self._score(record, relevance))
            for record, relevance in candidates
        ]
        scored.sort(key=lambda x: x[1], reverse=True)

        if self.diversity_penalty <= 0:
            return [r for r, _ in scored[:limit]]

        # MMR-style greedy selection
        selected: list[MemoryRecord] = []
        remaining = list(scored)
        while remaining and len(selected) < limit:
            best_idx = 0
            best_mmr = -float("inf")
            for i, (record, score) in enumerate(remaining):
                if not selected:
                    mmr = score
                else:
                    max_sim = max(
                        cosine_similarity(record.embedding, s.embedding)
                        for s in selected
                    )
                    mmr = score - self.diversity_penalty * max_sim
                if mmr > best_mmr:
                    best_mmr = mmr
                    best_idx = i
            selected.append(remaining.pop(best_idx)[0])
        return selected

    def _score(self, record: MemoryRecord, relevance: float) -> float:
        recency = _time_decay(record)
        importance = min(max(record.importance, 0.0), 1.0)
        return (
            self.w_rel * relevance
            + self.w_rec * recency
            + self.w_imp * importance
        )
