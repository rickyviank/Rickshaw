"""MemoryService — facade where read/write policies live."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from rickshaw.memory._math import cosine_similarity
from rickshaw.memory.embedder import Embedder, Model2VecEmbedder, TFIDFEmbedder
from rickshaw.memory.hybrid import CrossEncoderReranker, HybridRetriever
from rickshaw.memory.ranker import Ranker
from rickshaw.memory.record import MemoryRecord, MemoryScope, MemoryType
from rickshaw.memory.store import MemoryStore
from rickshaw.providers.base import Response

_DEDUPE_THRESHOLD = 0.92


class MemoryService:
    """High-level facade over the memory subsystem.

    Policies:
      1. Dedupe-on-write via embedding-similarity threshold.
      2. Scope filtering on search (session + global by default).
      3. Ranked retrieval via Ranker.
      4. Compaction/reflection is deferred to the worker.
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        store: MemoryStore | None = None,
        ranker: Ranker | None = None,
        db_path: str | Path = ":memory:",
        dedupe_threshold: float = _DEDUPE_THRESHOLD,
        context_budget: int = 10,
        hybrid: bool = True,
        reranker: CrossEncoderReranker | None = None,
    ) -> None:
        self.embedder = embedder or Model2VecEmbedder()
        self.store = store or MemoryStore(db_path, vector_dim=self.embedder.dimension)
        self.ranker = ranker or Ranker()
        self.dedupe_threshold = dedupe_threshold
        self.context_budget = context_budget
        # Phase 3: hybrid retrieval (FTS5 BM25 + dense KNN, fused via RRF).
        # When enabled, assemble_context uses the hybrid retriever instead of
        # dense-only search. Falls back to dense-only if FTS5 is unavailable.
        self.retriever: HybridRetriever | None = None
        if hybrid:
            self.retriever = HybridRetriever(
                self.store, self.embedder, reranker=reranker,
            )
            # Wire the FTS5 index into the store so puts/deletes stay in sync.
            if self.retriever._fts is not None:
                self.store.register_fts_index(self.retriever._fts)
        # If the embedder tier changed (dimension mismatch), re-embed all
        # existing records so they live in the new vector space. This makes
        # tier switches safe without losing stored memories.
        self._migrate_embedding_dimension()

    def _migrate_embedding_dimension(self) -> None:
        """Re-embed all records when the embedder dimension changes (tier switch)."""
        records = self.store.all_records()
        if not records:
            return
        target_dim = self.embedder.dimension
        needs_reembed = any(len(r.embedding) != target_dim for r in records)
        if not needs_reembed:
            return
        for record in records:
            record.embedding = self.embedder.embed(record.text)
            self.store.put(record)
        # Re-sync the FTS index after re-embedding (texts unchanged, but the
        # store.put path updates the FTS index automatically).

    def assemble_context(
        self,
        query: str,
        scope_filter: list[MemoryScope] | None = None,
    ) -> list[MemoryRecord]:
        """Retrieve relevant memories via hybrid search, rank, return budget-bounded.

        When hybrid retrieval is enabled, runs FTS5 BM25 + dense KNN, fuses via
        RRF, and feeds the fused candidates into the Ranker. Otherwise falls
        back to dense-only search. The egress/privacy boundary (excluding
        sensitive records) is enforced before ranking in both paths.
        """
        if scope_filter is None:
            scope_filter = [MemoryScope.GLOBAL, MemoryScope.SESSION]
        if self.retriever is not None:
            candidates = self.retriever.retrieve(
                query, scope_filter=scope_filter, limit=max(self.context_budget * 2, 20),
            )
        else:
            query_vec = self.embedder.embed(query)
            candidates = self.store.search(query_vec, scope_filter=scope_filter)
        # Egress/privacy boundary: exclude sensitive records BEFORE ranking so
        # the context budget is filled entirely with shareable records (the
        # ranker and prompt builder never see sensitive data).
        candidates = [(r, s) for r, s in candidates if not r.sensitive]
        ranked = self.ranker.rank(candidates, limit=self.context_budget)
        # Touch last_used_at on retrieved records
        now = datetime.now(timezone.utc)
        for record in ranked:
            record.last_used_at = now
            record.use_count += 1
            self.store.update(record)
        return ranked

    def write(
        self,
        text: str,
        scope: MemoryScope = MemoryScope.SESSION,
        type: MemoryType = MemoryType.FACT,
        sensitive: bool = False,
        importance: float = 0.0,
    ) -> MemoryRecord | None:
        """Dedupe via embedding similarity, then store.

        Returns the new record, or ``None`` if a duplicate was detected.
        """
        embedding = self.embedder.embed(text)
        # Dedupe: check existing records.
        # FUTURE: dedup should be scope-aware, update-on-duplicate (bump
        # last_used_at/use_count instead of discarding), and use an adaptive
        # per-model threshold. See FUTURE.md ("Deduplication").
        existing = self.store.search(embedding, limit=5)
        for record, sim in existing:
            if sim >= self.dedupe_threshold:
                return None

        record = MemoryRecord(
            text=text,
            embedding=embedding,
            scope=scope,
            type=type,
            importance=importance,
            sensitive=sensitive,
        )
        self.store.put(record)
        return record

    def write_observations(
        self,
        response: Response,
        scope: MemoryScope = MemoryScope.SESSION,
    ) -> list[MemoryRecord]:
        """Derive memory records from a turn's Response.

        Stores the assistant text as a fact. Provider-agnostic.
        """
        records: list[MemoryRecord] = []
        if response.text:
            rec = self.write(
                text=response.text,
                scope=scope,
                type=MemoryType.FACT,
            )
            if rec is not None:
                records.append(rec)
        return records

    def remember(self, fact: str) -> str:
        """Store a fact (tool-callable). Returns the record id or a message."""
        record = self.write(fact)
        if record is None:
            return "duplicate: already stored"
        return record.id

    def recall(self, query: str, limit: int = 5) -> list[dict[str, str]]:
        """Retrieve relevant memories (tool-callable)."""
        results = self.assemble_context(query)[:limit]
        return [{"id": r.id, "text": r.text} for r in results]

    def forget(self, record_id: str) -> str:
        """Delete a memory by id (tool-callable)."""
        if self.store.delete(record_id):
            return f"deleted {record_id}"
        return f"not found: {record_id}"
