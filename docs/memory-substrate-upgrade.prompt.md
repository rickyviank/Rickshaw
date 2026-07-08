# Build Prompt: Upgrade Rickshaw's memory substrate for accuracy + personability

> Hand this document to an implementer (human or coding agent — targeted at **Claude Fable 5**).
> It is a complete build specification written as a prompt.
>
> **Working directory:** `~/p_workspaces/Rickshaw` (the Rickshaw harness repo).
> **Sequencing:** This is step 1 of 2. The memory-graph layer
> (`docs/memory-graph-layer.prompt.md`) builds on the substrate delivered here — do this first.

---

## Your role

You are an expert Python engineer working in the **Rickshaw** repository — a multi-LLM
harness whose two promises are "your driver" (any provider behind one interface) and
"your memory" (an offline, user-owned semantic memory layer that travels across
providers). You are improving the memory layer's **substrate and retrieval quality**.

## Mission (two goals, both measurable)

1. **Accuracy** — increase how reliably the right memories are written and later recalled.
2. **Personability** — make Rickshaw feel like it remembers the *user*: their
   preferences, identity, recurring context, and tone — and surface that naturally.

## Non-negotiable constraints

- **Offline is the default, not a mode.** Every write and read must complete with zero
  network calls. Cloud/provider embeddings may exist ONLY as an opt-in adapter, never on
  the default path. Prefer an embedder that can be **bundled in the package** so a fresh
  install works fully air-gapped with no model download.
- **SQLite stays the source of truth.** Single-file, embeddable. Do not introduce a
  server-based vector DB.
- **Preserve public interfaces.** `MemoryService`/gateway, the `Embedder` interface, the
  `remember`/`recall`/`forget` tools, and `MemoryRecord` consumers must keep working.
  Extend; don't gratuitously break.
- **Graceful degradation.** If a neural embedder is unavailable, retrieval must still work
  (lexical fallback), never crash.

## Where things live (read these first)

- `rickshaw/memory/store.py` — SQLite store, currently JSON-encoded embeddings + brute-force cosine, optional ChromaDB.
- `rickshaw/memory/embedder.py` — `Embedder` interface; `TFIDFEmbedder` (lexical, the main weakness) and `ProviderEmbedder`.
- `rickshaw/memory/ranker.py` — relevance × recency × importance (+ optional MMR). **Keep and reuse.**
- `rickshaw/memory/service.py` — the façade; `assemble_context()`, `write()`, `write_observations()` (currently dumps raw assistant text as a flat FACT — improve this).
- `rickshaw/memory/record.py` — `MemoryRecord`, `MemoryScope`, `MemoryType`.
- `rickshaw/orchestrator.py` — turn loop; calls `assemble_context()` then `write_observations()`.
- `rickshaw/worker.py`, `rickshaw/queue.py` — deferred jobs (importance scoring, compaction, eviction).
- Tests use `pytest` / `pytest-asyncio`; follow existing patterns.

## The work — implement in phases, tests per phase

### Phase 1 — Unified vector store (replace the primitive substrate)

- Adopt **`sqlite-vec`** (`vec0` virtual tables) so KNN happens inside SQLite. This removes
  the SQLite↔ChromaDB dual-write sync problem. Keep a **brute-force cosine fallback** if the
  extension can't load, so nothing breaks.
- Store vectors as compact **float32 BLOB** (or sqlite-vec native), not JSON text.
- Migrate existing rows; provide a safe path for existing `rickshaw_memory.db` files.

### Phase 2 — Tiered local embeddings (behind the existing `Embedder` interface)

Make the embedder pluggable across three tiers; the interface and `dimension` contract stay stable:

- **`Model2VecEmbedder` (new DEFAULT)** — static distilled embeddings, few MB, **bundled**,
  no inference cost, fully air-gapped. Must beat TF-IDF semantically.
- **`FastEmbedEmbedder` (opt-in upgrade)** — `fastembed` (ONNX, no PyTorch) with
  `bge-small-en-v1.5` or `all-MiniLM-L6-v2`; one-time model fetch then offline.
- Keep **`ProviderEmbedder`** as the not-offline-constrained option.
- Config-select the tier; default to the bundled one. Re-embed/migrate on tier change.

### Phase 3 — Hybrid retrieval (the real accuracy jump)

- Add **lexical BM25 via SQLite FTS5** over `MemoryRecord.text`. (This makes TF-IDF redundant.)
- Add **dense** retrieval via the Phase-1 vector store.
- **Fuse** both ranked lists with **Reciprocal Rank Fusion (RRF)**, then pass the fused
  candidate set into the existing `Ranker` (recency × importance × MMR).
- Optional **tier-3 reranker** (small local cross-encoder, e.g. via fastembed) as an
  off-by-default toggle; document the latency cost.

### Phase 4 — Better memory WRITING (accuracy + personability at the source)

- Replace the naive `write_observations()`: distill each turn into **atomic, well-typed**
  records rather than one blob of assistant text. Correctly classify `MemoryType`
  (FACT / DECISION / PREFERENCE / ERROR) and `MemoryScope`, and set `sensitive` properly.
- Make **dedup scope-aware and update-on-duplicate** (bump `last_used_at`/`use_count`,
  optionally merge) instead of silently discarding.
- **Personability:** actively extract stable user-identity and preference memories
  (name, role, tone, recurring projects, likes/dislikes, working style) into
  GLOBAL-scoped `PREFERENCE` records, and support **pinning** so a compact "about the
  user" set is always available to the assembler. Distillation must run offline via a
  heuristic when no provider is reachable, and use the LLM when one is.

### Phase 5 — Prove it (evaluation harness)

- Build a small **retrieval eval** under `tests/` or `evals/`: a fixture set of
  written memories + natural-language recall queries + expected hits.
- Report **recall@k / MRR** for: TF-IDF baseline vs Model2Vec-dense vs hybrid(+RRF).
- Gate the change on hybrid **measurably beating** the TF-IDF baseline; include the numbers
  in your final summary. Add a couple of "personable recall" cases (e.g. "what do you know
  about how I like to work?") and show they surface the right preference memories.

## How to work

- **Plan before coding.** Confirm your phase breakdown and the sqlite-vec/embedder library
  choices against what's actually installed/available, then proceed.
- Keep each phase in its own focused commit with passing tests; don't mix phases.
- Add new deps to the project config; keep the offline-default deps light. Note any
  one-time model downloads and how to bundle/vendor for air-gapped installs.
- Match the existing code style, typing, and docstring conventions.

## Definition of done

- Default install runs **fully offline**, no network on read/write paths.
- sqlite-vec unified store live with brute-force fallback; embedder tiers selectable,
  default bundled; hybrid FTS5+dense+RRF retrieval feeding the existing Ranker.
- Writing distills atomic, well-typed, deduped records and captures personal/preference
  memory; pinning works.
- Eval harness shows hybrid retrieval beats the TF-IDF baseline on recall@k/MRR, with the
  numbers reported.
- All existing tests still pass; new tests cover each phase.
