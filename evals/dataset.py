"""Fixture dataset for the retrieval evaluation harness.

A set of written memories (the corpus), natural-language recall queries, and
expected hit IDs. Designed to exercise both lexical and semantic matching, and
to include personable recall cases (preferences/identity).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MemoryFixture:
    """A memory to write into the store before running queries."""
    id: str
    text: str
    scope: str = "global"  # global / session / project / task
    type: str = "fact"      # fact / decision / preference / error
    importance: float = 0.3


@dataclass
class QueryFixture:
    """A recall query with expected matching memory IDs."""
    query: str
    expected_ids: list[str]
    description: str = ""
    # Personable queries test preference/identity recall.
    personable: bool = False


@dataclass
class EvalDataset:
    """The full eval dataset: memories to write + queries to evaluate."""
    memories: list[MemoryFixture] = field(default_factory=list)
    queries: list[QueryFixture] = field(default_factory=list)


def build_default_dataset() -> EvalDataset:
    """Build the default retrieval eval dataset.

    Covers:
    - Lexical matches (exact term overlap).
    - Semantic matches (same meaning, different words — TF-IDF should miss these).
    - Personable recall (preferences/identity via paraphrased questions).
    - Distractors (topically similar but irrelevant memories).
    """
    memories = [
        # --- Preferences / identity (personable) ---
        MemoryFixture("pref_dark_mode", "The user prefers dark mode for their editor and terminal",
                      type="preference", importance=0.8),
        MemoryFixture("pref_concise", "The user likes concise answers without unnecessary fluff",
                      type="preference", importance=0.8),
        MemoryFixture("pref_python", "The user primarily writes Python for data engineering work",
                      type="preference", importance=0.7),
        MemoryFixture("pref_name", "The user's name is Alice and she works as a backend engineer",
                      type="preference", importance=0.9),
        MemoryFixture("pref_testing", "The user prefers pytest with coverage thresholds over unittest",
                      type="preference", importance=0.6),

        # --- Technical facts (semantic + lexical) ---
        MemoryFixture("fact_pg_oltp", "PostgreSQL is well-suited for OLTP workloads with high concurrency",
                      type="fact", importance=0.4),
        MemoryFixture("fact_redis_cache", "Redis can be used as a distributed cache with TTL-based eviction",
                      type="fact", importance=0.4),
        MemoryFixture("fact_docker_multi", "Docker multi-stage builds reduce final image size significantly",
                      type="fact", importance=0.4),
        MemoryFixture("fact_k8s_hpa", "Kubernetes HorizontalPodAutoscaler scales pods based on CPU metrics",
                      type="fact", importance=0.4),
        MemoryFixture("fact_sql_index", "A B-tree index speeds up equality and range queries on large tables",
                      type="fact", importance=0.4),

        # --- Decisions ---
        MemoryFixture("dec_use_pg", "We decided to use PostgreSQL instead of MySQL for the new service",
                      type="decision", importance=0.6),
        MemoryFixture("dec_ci_github", "The team chose GitHub Actions for CI over Jenkins",
                      type="decision", importance=0.5),

        # --- Distractors (topically near but semantically irrelevant) ---
        MemoryFixture("distract_pg_backup", "PostgreSQL point-in-time recovery requires WAL archiving to be enabled",
                      type="fact", importance=0.3),
        MemoryFixture("distract_dark_theme_css", "The website uses a dark color scheme via CSS custom properties",
                      type="fact", importance=0.3),
        MemoryFixture("distract_python_gil", "Python's GIL prevents true parallelism for CPU-bound threads",
                      type="fact", importance=0.3),
    ]

    queries = [
        # --- Lexical matches (TF-IDF should handle these) ---
        QueryFixture("PostgreSQL OLTP", ["fact_pg_oltp"],
                     description="exact term overlap with target"),
        QueryFixture("Docker multi-stage builds", ["fact_docker_multi"],
                     description="exact term overlap"),
        QueryFixture("Redis distributed cache", ["fact_redis_cache"],
                     description="exact term overlap"),

        # --- Semantic matches (TF-IDF should struggle; Model2Vec/hybrid should win) ---
        QueryFixture("what database is good for high-concurrency transactional workloads",
                     ["fact_pg_oltp"],
                     description="paraphrased — no shared terms with the memory"),
        QueryFixture("how to make container images smaller",
                     ["fact_docker_multi"],
                     description="paraphrased — 'container images' vs 'Docker image size'"),
        QueryFixture("scaling pods automatically based on resource usage",
                     ["fact_k8s_hpa"],
                     description="paraphrased — 'resource usage' vs 'CPU metrics'"),
        QueryFixture("speeding up database queries on large datasets",
                     ["fact_sql_index"],
                     description="paraphrased — 'speeding up queries' vs 'B-tree index'"),

        # --- Lexical-only matches (BM25 catches these; dense may miss) ---
        QueryFixture("WAL archiving configuration",
                     ["distract_pg_backup"],
                     description="lexical-only: 'WAL' is a rare technical term"),
        QueryFixture("CSS custom properties dark color scheme",
                     ["distract_dark_theme_css"],
                     description="lexical: exact term overlap with a distractor that "
                                 "semantically resembles the dark-mode preference"),

        # --- Personable recall (preferences/identity) ---
        QueryFixture("what do you know about how I like to work?",
                     ["pref_concise", "pref_python", "pref_testing"],
                     description="personable: working style preferences",
                     personable=True),
        QueryFixture("what is my name and role?",
                     ["pref_name"],
                     description="personable: identity",
                     personable=True),
        QueryFixture("do I prefer dark or light mode?",
                     ["pref_dark_mode"],
                     description="personable: UI preference",
                     personable=True),
        QueryFixture("what programming language do I use most?",
                     ["pref_python"],
                     description="personable: language preference",
                     personable=True),
    ]

    return EvalDataset(memories=memories, queries=queries)
