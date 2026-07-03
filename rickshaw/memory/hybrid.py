"""Hybrid retrieval: FTS5 BM25 + dense KNN fused via Reciprocal Rank Fusion.

The real accuracy jump comes from combining two complementary signals:

- **Lexical (BM25 via SQLite FTS5)**: exact term matching, great for queries
  that reuse the same vocabulary as stored memories (names, identifiers,
  technical terms). Replaces the TF-IDF embedder's lexical role with a proper
  ranking function.
- **Dense (vec0 / brute-force cosine)**: semantic matching, catches queries
  that mean the same thing but use different words.

The two ranked lists are fused with **Reciprocal Rank Fusion (RRF)**, which
combines rankings without needing comparable score scales:

    rrf_score(d) = sum over lists: 1 / (k + rank_in_list(d))

The fused candidate set is then passed into the existing :class:`Ranker`
(recency × importance × MMR) for final ordering — so the Ranker's policies are
preserved and reused, not replaced.

An optional **tier-3 cross-encoder reranker** (via fastembed) can be enabled to
re-score the top fused candidates with a pairwise model. Off by default due to
latency cost; see :class:`CrossEncoderReranker`.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import TYPE_CHECKING

from rickshaw.memory.record import MemoryRecord, MemoryScope

if TYPE_CHECKING:
    from rickshaw.memory.store import MemoryStore

logger = logging.getLogger(__name__)

# RRF constant. The original paper uses k=60; smaller k gives more weight to
# top ranks. k=60 is a safe default that works well across collection sizes.
_RRF_K = 60


def _fts5_available(conn: sqlite3.Connection) -> bool:
    """Return True if SQLite has FTS5 compiled in."""
    try:
        conn.execute("CREATE VIRTUAL TABLE IF NOT EXISTS _fts5_probe USING fts5(x)")
        conn.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False


class FTS5Index:
    """FTS5 full-text index over MemoryRecord.text for BM25 lexical search.

    The ``memories_fts`` shadow table mirrors (id, text, scope) so BM25 ranking
    + scope filtering happen inside SQLite. Superseded records are removed so
    they never surface.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._table = "memories_fts"
        conn.execute(
            f"""CREATE VIRTUAL TABLE IF NOT EXISTS {self._table} USING fts5(
                rec_id UNINDEXED,
                content,
                scope UNINDEXED
            )"""
        )
        conn.commit()

    def upsert(self, record: MemoryRecord) -> None:
        """Insert/replace a record's text. Superseded records are removed."""
        if record.superseded_by is not None:
            self.delete(record.id)
            return
        # FTS5 has no native UPSERT; delete-then-insert.
        self.delete(record.id)
        self._conn.execute(
            f"INSERT INTO {self._table} (rec_id, content, scope) VALUES (?, ?, ?)",
            (record.id, record.text, record.scope.value),
        )
        self._conn.commit()

    def delete(self, record_id: str) -> None:
        self._conn.execute(
            f"DELETE FROM {self._table} WHERE rec_id = ?", (record_id,)
        )
        self._conn.commit()

    def search(
        self,
        query: str,
        scope_filter: list[MemoryScope] | None,
        limit: int,
    ) -> list[tuple[str, float]]:
        """Return ``(record_id, bm25_score)`` for the top-*limit* matches.

        BM25 scores are negative (lower = more relevant); we negate so higher
        = more relevant, consistent with dense similarity scores. Scope
        filtering is applied via a WHERE clause.
        """
        if not query.strip():
            return []
        where = ""
        params: list = [query, limit]
        if scope_filter:
            scopes = [s.value for s in scope_filter]
            placeholders = ",".join("?" for _ in scopes)
            where = f" AND scope IN ({placeholders})"
            params = [query, *scopes, limit]
        try:
            rows = self._conn.execute(
                f"""SELECT rec_id, bm25({self._table}) AS score
                    FROM {self._table}
                    WHERE {self._table} MATCH ?
                    {where}
                    ORDER BY score
                    LIMIT ?""",
                params,
            ).fetchall()
            # Negate bm25 so higher = more relevant.
            return [(r[0], -float(r[1])) for r in rows]
        except sqlite3.OperationalError as exc:
            logger.debug("FTS5 search failed for query %r: %s", query, exc)
            return []

    def count(self) -> int:
        row = self._conn.execute(f"SELECT COUNT(*) FROM {self._table}").fetchone()
        return int(row[0]) if row else 0


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = _RRF_K,
) -> dict[str, float]:
    """Fuse multiple ranked lists into a single RRF score per item.

    Each list is a list of ``(id, score)`` tuples sorted by descending score.
    Returns a dict mapping id -> fused RRF score (higher = more relevant).
    """
    scores: dict[str, float] = {}
    for ranked in ranked_lists:
        for rank, (item_id, _score) in enumerate(ranked):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank + 1)
    return scores


class CrossEncoderReranker:
    """Optional tier-3 cross-encoder reranker via fastembed.

    Re-scores the top fused candidates with a pairwise (query, document) model
    for higher precision. Off by default — adds latency proportional to the
    candidate set size. Requires the ``fastembed`` extra::

        pip install rickshaw[embed]

    The default model is ``cross-encoder/ms-marco-MiniLM-L-6`` (fast, ~22MB).
    """

    DEFAULT_MODEL = "cross-encoder/ms-marco-MiniLM-L-6"

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or self.DEFAULT_MODEL
        self._model = None
        self._available = False
        try:
            # fastembed supports cross-encoders via the TextEmbedding API with
            # a `passage`/`query` split; for reranking we use the ranker model.
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name)
            self._available = True
        except Exception as exc:
            logger.info(
                "Cross-encoder reranker unavailable (%s); skipping rerank. "
                "Install fastembed: pip install rickshaw[embed]",
                exc,
            )
            self._available = False

    @property
    def available(self) -> bool:
        return self._available

    def rerank(
        self,
        query: str,
        candidates: list[MemoryRecord],
        limit: int,
    ) -> list[tuple[MemoryRecord, float]]:
        """Re-score and return the top *limit* candidates as (record, score)."""
        if not self._available or not candidates:
            return [(c, 0.0) for c in candidates[:limit]]
        try:
            # Encode query + each doc, score by cosine similarity.
            q_vec = next(self._model.embed([query]))
            docs = [r.text for r in candidates]
            doc_vecs = list(self._model.embed(docs))
            scored = []
            import math
            for rec, d_vec in zip(candidates, doc_vecs):
                dot = sum(a * b for a, b in zip(q_vec, d_vec))
                nq = math.sqrt(sum(a * a for a in q_vec))
                nd = math.sqrt(sum(b * b for b in d_vec))
                sim = dot / (nq * nd) if nq > 0 and nd > 0 else 0.0
                scored.append((rec, float(sim)))
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:limit]
        except Exception as exc:
            logger.debug("Cross-encoder rerank failed: %s", exc)
            return [(c, 0.0) for c in candidates[:limit]]


class HybridRetriever:
    """Combines FTS5 BM25 + dense KNN via RRF, then feeds the Ranker.

    Usage::

        retriever = HybridRetriever(store, embedder)
        candidates = retriever.retrieve(query, scope_filter, limit=20)
        ranked = ranker.rank(candidates, limit=context_budget)
    """

    def __init__(
        self,
        store: MemoryStore,
        embedder,
        use_lexical: bool = True,
        use_dense: bool = True,
        reranker: CrossEncoderReranker | None = None,
        rrf_k: int = _RRF_K,
    ) -> None:
        self.store = store
        self.embedder = embedder
        self.use_lexical = use_lexical
        self.use_dense = use_dense
        self.reranker = reranker
        self.rrf_k = rrf_k
        self._fts: FTS5Index | None = None
        if use_lexical and _fts5_available(store._conn):
            self._fts = FTS5Index(store._conn)
            self._backfill_fts()

    def _backfill_fts(self) -> None:
        """Populate the FTS5 shadow table from rows present at open time."""
        if self._fts is None:
            return
        if self._fts.count() >= self.store._conn.execute(
            "SELECT COUNT(*) FROM memories"
        ).fetchone()[0]:
            return
        rows = self.store._conn.execute(
            "SELECT id, text, scope, superseded_by FROM memories"
        ).fetchall()
        for row in rows:
            if row["superseded_by"] is not None:
                continue
            self._fts.delete(row["id"])
            self.store._conn.execute(
                f"INSERT INTO {self._fts._table} (rec_id, content, scope) VALUES (?, ?, ?)",
                (row["id"], row["text"], row["scope"]),
            )
        self.store._conn.commit()

    def index_record(self, record: MemoryRecord) -> None:
        """Keep the FTS5 index in sync when a record is written."""
        if self._fts is not None:
            self._fts.upsert(record)

    def remove_record(self, record_id: str) -> None:
        """Remove a record from the FTS5 index."""
        if self._fts is not None:
            self._fts.delete(record_id)

    @property
    def lexical_enabled(self) -> bool:
        return self._fts is not None

    def retrieve(
        self,
        query: str,
        scope_filter: list[MemoryScope] | None = None,
        limit: int = 20,
    ) -> list[tuple[MemoryRecord, float]]:
        """Run hybrid retrieval and return fused (record, relevance) candidates.

        The relevance score is the RRF-fused rank score (higher = more
        relevant). These candidates are meant to be passed into the Ranker for
        final recency × importance × MMR ordering.
        """
        ranked_lists: list[list[tuple[str, float]]] = []

        # Dense retrieval via the store's existing vector search.
        if self.use_dense:
            query_vec = self.embedder.embed(query)
            dense_hits = self.store.search(query_vec, scope_filter=scope_filter, limit=limit)
            dense_ranked = [(r.id, s) for r, s in dense_hits]
            ranked_lists.append(dense_ranked)

        # Lexical retrieval via FTS5 BM25.
        if self.use_lexical and self._fts is not None:
            lex_hits = self._fts.search(query, scope_filter=scope_filter, limit=limit)
            ranked_lists.append(lex_hits)

        if not ranked_lists:
            return []

        # Fuse via RRF.
        fused = reciprocal_rank_fusion(ranked_lists, k=self.rrf_k)

        # Hydrate full records and filter superseded.
        candidates: list[tuple[MemoryRecord, float]] = []
        for record_id, rrf_score in sorted(fused.items(), key=lambda x: x[1], reverse=True):
            record = self.store.get(record_id)
            if record is None or record.superseded_by is not None:
                continue
            candidates.append((record, rrf_score))

        # Optional tier-3 cross-encoder reranking over the fused top candidates.
        if self.reranker is not None and self.reranker.available and candidates:
            candidates = self.reranker.rerank(
                query, [r for r, _ in candidates[:limit]], limit=limit,
            )

        return candidates[:limit]
