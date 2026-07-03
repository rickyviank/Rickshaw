"""Retrieval evaluation harness — recall@k / MRR for TF-IDF vs Model2Vec vs hybrid.

Runs the fixture dataset through three retrieval configurations and reports
recall@k and Mean Reciprocal Rank (MRR):

1. **TF-IDF baseline**: TFIDFEmbedder + dense-only search (the old default).
2. **Model2Vec-dense**: Model2VecEmbedder + dense-only search (Phase 2 upgrade).
3. **Hybrid (+RRF)**: Model2VecEmbedder + FTS5 BM25 + dense KNN fused via RRF
   (Phase 3).

The eval gates on hybrid **measurably beating** the TF-IDF baseline. Run with::

    python -m evals.retrieval_eval

Or as a pytest test (see tests/test_eval.py) which asserts the gate.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from rickshaw.memory.embedder import Model2VecEmbedder, TFIDFEmbedder
from rickshaw.memory.ranker import Ranker
from rickshaw.memory.record import MemoryRecord, MemoryScope, MemoryType
from rickshaw.memory.service import MemoryService
from rickshaw.memory.store import MemoryStore

from evals.dataset import MemoryFixture, build_default_dataset

logger = logging.getLogger(__name__)


class Metrics(NamedTuple):
    """Retrieval quality metrics for a single configuration."""
    name: str
    recall_at_1: float
    recall_at_3: float
    recall_at_5: float
    mrr: float
    personable_recall_at_5: float
    personable_mrr: float

    def beats(self, other: Metrics) -> bool:
        """Return True if this config beats *other* on the key metrics."""
        return (
            self.recall_at_5 >= other.recall_at_5
            and self.mrr >= other.mrr
            and (self.recall_at_5 > other.recall_at_5 or self.mrr > other.mrr)
        )


def _fixture_to_record(f: MemoryFixture) -> MemoryRecord:
    scope_map = {
        "global": MemoryScope.GLOBAL,
        "session": MemoryScope.SESSION,
        "project": MemoryScope.PROJECT,
        "task": MemoryScope.TASK,
    }
    type_map = {
        "fact": MemoryType.FACT,
        "decision": MemoryType.DECISION,
        "preference": MemoryType.PREFERENCE,
        "error": MemoryType.ERROR,
        "tool_result": MemoryType.TOOL_RESULT,
    }
    return MemoryRecord(
        id=f.id,
        text=f.text,
        embedding=[],  # will be set by the service on write
        scope=scope_map.get(f.scope, MemoryScope.GLOBAL),
        type=type_map.get(f.type, MemoryType.FACT),
        importance=f.importance,
    )


def _build_service(
    embedder,
    hybrid: bool,
    db_path: str = ":memory:",
) -> MemoryService:
    """Build a MemoryService with the given embedder and hybrid toggle."""
    store = MemoryStore(db_path, vector_dim=embedder.dimension, use_vector_index=False)
    return MemoryService(
        embedder=embedder,
        store=store,
        ranker=Ranker(w_rel=1.0, w_rec=0.0, w_imp=0.0),  # relevance-only for eval
        hybrid=hybrid,
    )


def _recall_at_k(retrieved_ids: list[str], expected_ids: list[str], k: int) -> float:
    """Fraction of expected IDs in the top-k retrieved."""
    if not expected_ids:
        return 1.0
    top_k = retrieved_ids[:k]
    hits = sum(1 for eid in expected_ids if eid in top_k)
    return hits / len(expected_ids)


def _reciprocal_rank(retrieved_ids: list[str], expected_ids: list[str]) -> float:
    """1/rank of the first relevant result, or 0 if none in the list."""
    for i, rid in enumerate(retrieved_ids):
        if rid in expected_ids:
            return 1.0 / (i + 1)
    return 0.0


def evaluate_config(
    name: str,
    embedder,
    hybrid: bool,
    dataset=None,
) -> Metrics:
    """Run the dataset through one retrieval configuration and return metrics."""
    ds = dataset or build_default_dataset()
    service = _build_service(embedder, hybrid)

    # Write all memory fixtures.
    for f in ds.memories:
        rec = _fixture_to_record(f)
        rec.embedding = service.embedder.embed(f.text)
        service.store.put(rec)
        # Keep FTS in sync for hybrid.
        if service.retriever is not None:
            service.retriever.index_record(rec)

    # Run queries.
    recalls_1, recalls_3, recalls_5, rrs = [], [], [], []
    p_recalls_5, p_rrs = [], []
    for q in ds.queries:
        results = service.assemble_context(q.query)
        retrieved_ids = [r.id for r in results]
        r1 = _recall_at_k(retrieved_ids, q.expected_ids, 1)
        r3 = _recall_at_k(retrieved_ids, q.expected_ids, 3)
        r5 = _recall_at_k(retrieved_ids, q.expected_ids, 5)
        rr = _reciprocal_rank(retrieved_ids, q.expected_ids)
        recalls_1.append(r1)
        recalls_3.append(r3)
        recalls_5.append(r5)
        rrs.append(rr)
        if q.personable:
            p_recalls_5.append(r5)
            p_rrs.append(rr)

    n = len(ds.queries)
    np_ = max(len(p_recalls_5), 1)
    return Metrics(
        name=name,
        recall_at_1=sum(recalls_1) / n,
        recall_at_3=sum(recalls_3) / n,
        recall_at_5=sum(recalls_5) / n,
        mrr=sum(rrs) / n,
        personable_recall_at_5=sum(p_recalls_5) / np_,
        personable_mrr=sum(p_rrs) / np_,
    )


def run_eval() -> dict[str, Metrics]:
    """Run all three configurations and return a name->Metrics mapping.

    Prints a summary table and returns the metrics for programmatic use
    (e.g. by the pytest gate test).
    """
    results: dict[str, Metrics] = {}

    # 1. TF-IDF baseline (dense-only, no hybrid).
    results["tfidf"] = evaluate_config(
        "TF-IDF (dense-only)", TFIDFEmbedder(dim=128), hybrid=False,
    )

    # 2. Model2Vec dense-only.
    results["model2vec_dense"] = evaluate_config(
        "Model2Vec (dense-only)", Model2VecEmbedder(), hybrid=False,
    )

    # 3. Hybrid (Model2Vec + FTS5 BM25 + RRF).
    results["hybrid"] = evaluate_config(
        "Hybrid (Model2Vec + BM25 + RRF)", Model2VecEmbedder(), hybrid=True,
    )

    # Print summary.
    print("\n" + "=" * 80)
    print("Retrieval Evaluation: TF-IDF vs Model2Vec vs Hybrid")
    print("=" * 80)
    print(f"{'Config':<30} {'R@1':>6} {'R@3':>6} {'R@5':>6} {'MRR':>6} {'P-R@5':>6} {'P-MRR':>6}")
    print("-" * 80)
    for m in results.values():
        print(
            f"{m.name:<30} {m.recall_at_1:>6.3f} {m.recall_at_3:>6.3f} "
            f"{m.recall_at_5:>6.3f} {m.mrr:>6.3f} "
            f"{m.personable_recall_at_5:>6.3f} {m.personable_mrr:>6.3f}"
        )
    print("-" * 80)

    # Gate check.
    tfidf = results["tfidf"]
    hybrid_m = results["hybrid"]
    if hybrid_m.beats(tfidf):
        print(f"\nGATE: PASS — hybrid beats TF-IDF baseline "
              f"(R@5 {hybrid_m.recall_at_5:.3f} vs {tfidf.recall_at_5:.3f}, "
              f"MRR {hybrid_m.mrr:.3f} vs {tfidf.mrr:.3f})")
    else:
        print(f"\nGATE: FAIL — hybrid does NOT beat TF-IDF baseline "
              f"(R@5 {hybrid_m.recall_at_5:.3f} vs {tfidf.recall_at_5:.3f}, "
              f"MRR {hybrid_m.mrr:.3f} vs {tfidf.mrr:.3f})")

    return results


if __name__ == "__main__":
    run_eval()
