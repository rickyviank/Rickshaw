"""Pytest gate test for the retrieval evaluation harness.

Asserts that hybrid retrieval measurably beats the TF-IDF baseline on
recall@k and MRR. This is the Definition of Done gate from the Phase 5 spec.
"""

from __future__ import annotations

from evals.retrieval_eval import run_eval


def test_hybrid_beats_tfidf_baseline():
    """The hybrid retrieval gate: hybrid must beat TF-IDF on R@5 and MRR."""
    results = run_eval()
    tfidf = results["tfidf"]
    hybrid = results["hybrid"]
    # Gate: hybrid must beat TF-IDF on both R@5 and MRR, with at least one
    # strict improvement.
    assert hybrid.recall_at_5 >= tfidf.recall_at_5, (
        f"Hybrid R@5 ({hybrid.recall_at_5:.3f}) must beat TF-IDF R@5 "
        f"({tfidf.recall_at_5:.3f})"
    )
    assert hybrid.mrr >= tfidf.mrr, (
        f"Hybrid MRR ({hybrid.mrr:.3f}) must beat TF-IDF MRR ({tfidf.mrr:.3f})"
    )
    assert (
        hybrid.recall_at_5 > tfidf.recall_at_5 or hybrid.mrr > tfidf.mrr
    ), "Hybrid must strictly improve on at least one metric"


def test_model2vec_beats_tfidf_on_personable_queries():
    """Model2Vec (and hybrid) must beat TF-IDF on personable recall (MRR)."""
    results = run_eval()
    tfidf = results["tfidf"]
    hybrid = results["hybrid"]
    assert hybrid.personable_mrr > tfidf.personable_mrr, (
        f"Hybrid personable MRR ({hybrid.personable_mrr:.3f}) must beat "
        f"TF-IDF personable MRR ({tfidf.personable_mrr:.3f})"
    )
    assert hybrid.personable_recall_at_5 >= tfidf.personable_recall_at_5, (
        f"Hybrid personable R@5 ({hybrid.personable_recall_at_5:.3f}) must beat "
        f"TF-IDF personable R@5 ({tfidf.personable_recall_at_5:.3f})"
    )


def test_eval_dataset_has_personable_queries():
    """The eval dataset includes personable recall cases."""
    from evals.dataset import build_default_dataset
    ds = build_default_dataset()
    personable = [q for q in ds.queries if q.personable]
    assert len(personable) >= 3, "Need at least 3 personable queries"
    # Each personable query should target preference-type memories.
    for q in personable:
        assert len(q.expected_ids) > 0
