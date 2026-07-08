# Build Prompt: Build a Memory Graph layer over Rickshaw's memory substrate

> Hand this document to an implementer (human or coding agent — targeted at **Claude Fable 5**).
> It is a complete build specification written as a prompt.
>
> **Working directory:** `~/p_workspaces/Rickshaw` (the Rickshaw harness repo).
> **Sequencing:** This is step 2 of 2. It assumes the substrate from
> `docs/memory-substrate-upgrade.prompt.md` is already delivered. Do that first.

---

## Your role

You are a senior Python engineer working in the **Rickshaw** repository — a multi-LLM
harness whose two promises are "your driver" (any provider behind one interface) and
"your memory" (an offline, user-owned memory layer that travels across providers). You are
adding a **memory graph**: a layer of entities and typed relations built over the existing
flat-record memory, enabling associative, multi-hop, temporally-aware recall.

## Mission

Move recall beyond "match the text of a record" to "traverse what connects." Enable queries
like *"who did I work with on the auth migration?"*, *"what tools does the payments team
use?"*, or *"what did I decide about caching, and has that changed?"* — by extracting a
graph of entities (people, projects, orgs, tools, concepts, events, preferences) and typed,
time-bounded relations between them, then traversing that graph during context assembly.
This also deepens **personability**: the user is a first-class node whose ego-graph
(preferences, projects, collaborators) becomes a coherent, always-available profile.

## Assumed starting point — DO NOT rebuild this

A prior task already delivered the substrate this layer sits on. Treat it as done and build
on it; if any piece is missing, note it and adapt rather than reimplementing:

- **Unified store**: SQLite + `sqlite-vec` (`vec0` KNN, float32 BLOB), FTS5 lexical index.
- **Hybrid retrieval**: FTS5 BM25 + dense KNN fused with RRF, feeding the existing `Ranker`.
- **Tiered pluggable `Embedder`**: Model2Vec (bundled default) → fastembed → provider.
- **Distillation on write**: turns are distilled into atomic, well-typed `MemoryRecord`s
  (FACT / DECISION / PREFERENCE / ERROR), scope-tagged, with `source_turn_id`, `thread_id`,
  `pinned` fields and an episodic Thread/Turn store.
- **Deferred worker + queue**: background job types for importance scoring, compaction, eviction.

The memory graph is a NEW layer that consumes the same episodes/records and reuses the same
embedder, vector index, FTS5, worker/queue, and RRF machinery.

## Non-negotiable constraints

- **Offline reads, always.** Seeding, traversal, profiling, and context assembly must
  complete with **zero network calls**, using SQLite + local vectors only.
- **Graph writes may use the LLM when reachable, but must degrade offline.** Extraction runs
  as a **deferred background job** (never on the hot read path). Use an LLM extractor when a
  provider is reachable; fall back to a deterministic heuristic extractor otherwise. Same
  pattern as the existing distillation step.
- **SQLite stays the source of truth.** Graph = additional tables in the same DB. No graph
  server, no external graph DB.
- **Provenance on everything.** Every entity and edge records which turn/record asserted it.
- **Nothing is silently destroyed.** Entity merges and contradicted edges are *reversible*:
  keep history (soft-invalidate with pointers), don't hard-delete on updates.
- **Preserve existing interfaces.** The current `MemoryService`/gateway, `Embedder`,
  `MemoryRecord`, `remember`/`recall`/`forget`, and hybrid retrieval must keep working. The
  graph augments context assembly; it does not replace flat retrieval.
- **Bounded traversal.** Every traversal has hard depth/breadth/time caps and cycle guards.

## Where things live (read these first)

- `rickshaw/memory/service.py` — the gateway façade; `assemble_context()` is the integration point.
- `rickshaw/memory/store.py` — SQLite + sqlite-vec + FTS5; add graph tables/queries here or in a sibling store.
- `rickshaw/memory/embedder.py` — the tiered `Embedder`; reuse it for entity embeddings.
- `rickshaw/memory/ranker.py` — recency × importance × MMR; reuse to rank graph-derived candidates.
- `rickshaw/memory/record.py` — `MemoryRecord`, `MemoryScope`, `MemoryType`.
- `rickshaw/worker.py`, `rickshaw/queue.py` — add graph job types here.
- `rickshaw/orchestrator.py` — turn loop; calls `assemble_context()` then the distillation/write step.
- Put new code under `rickshaw/memory/graph/`. Tests use `pytest`/`pytest-asyncio`.

## Core concepts (glossary — implement to these definitions)

- **Entity (node)** — a canonical thing: `person | project | organization | tool |
  technology | concept | place | event | preference | task`. Has a canonical name, type,
  aliases, an embedding, salience, free-form attributes, temporal bounds, and provenance.
- **Relation (edge)** — a typed, directed assertion `(subject) --predicate--> (object)`,
  e.g. `person --works_on--> project`, `user --prefers--> tool`, `project --uses-->
  technology`, `person --member_of--> organization`. Carries confidence, weight/count,
  a validity interval, provenance, and an invalidation pointer.
- **Mention** — a provenance link from an entity/edge to the source turn or record that
  asserted it (enables "why do you think that?" and reversibility).
- **Bitemporal validity** — edges have `valid_from`/`valid_to` (when the fact was true) so a
  changed fact (job change, renamed project) invalidates the old edge instead of deleting it.
- **Ego-graph** — the subgraph centered on the `user` entity; the basis for the personable
  "about you" profile.

## Data model (SQLite; sketch — refine as needed)

```sql
-- entities: canonical nodes
CREATE TABLE entities (
  id           TEXT PRIMARY KEY,
  canonical    TEXT NOT NULL,
  type         TEXT NOT NULL,            -- enum above
  aliases      TEXT NOT NULL DEFAULT '[]', -- json array
  attributes   TEXT NOT NULL DEFAULT '{}', -- json blob (role, location, ...)
  embedding    BLOB,                      -- float32; also indexed in sqlite-vec
  salience     REAL NOT NULL DEFAULT 0.0, -- importance, decays over time
  mention_count INTEGER NOT NULL DEFAULT 0,
  first_seen   TEXT NOT NULL,
  last_seen    TEXT NOT NULL,
  superseded_by TEXT                      -- points at surviving id after a merge
);

-- relations: typed, directed, bitemporal edges
CREATE TABLE relations (
  id            TEXT PRIMARY KEY,
  subject_id    TEXT NOT NULL REFERENCES entities(id),
  predicate     TEXT NOT NULL,
  object_id     TEXT NOT NULL REFERENCES entities(id),
  confidence    REAL NOT NULL DEFAULT 0.5,
  weight        REAL NOT NULL DEFAULT 1.0, -- reinforced by repeated assertion
  valid_from    TEXT,
  valid_to      TEXT,                      -- NULL = currently valid
  invalidated_by TEXT,                     -- edge/turn that superseded this
  created_at    TEXT NOT NULL
);

-- provenance: entity/edge -> the turn/record that asserted it
CREATE TABLE mentions (
  id          TEXT PRIMARY KEY,
  target_kind TEXT NOT NULL,   -- 'entity' | 'relation'
  target_id   TEXT NOT NULL,
  source_turn_id   TEXT,
  source_record_id TEXT,
  created_at  TEXT NOT NULL
);

CREATE INDEX idx_rel_subject ON relations(subject_id);
CREATE INDEX idx_rel_object  ON relations(object_id);
CREATE INDEX idx_rel_pred    ON relations(predicate);
```

Register entity embeddings in a `sqlite-vec` vec0 table and entity names/aliases in FTS5 so
seeding reuses the existing hybrid path. Ship an idempotent migration; the graph builds
purely from existing episodes/records, so a first run can backfill from history.

## Interfaces (sketch — keep them small and pluggable)

```python
class GraphExtractor(Protocol):
    def extract(self, text: str, *, context: str | None = None) -> ExtractionResult: ...
    # ExtractionResult = entities[] + triples[(subj, pred, obj, confidence)]

class LLMGraphExtractor(GraphExtractor):   # used when a provider is reachable
    ...
class HeuristicGraphExtractor(GraphExtractor):  # offline fallback: gazetteer + patterns
    ...                                          # + co-occurrence over known entity types

class EntityResolver:
    # candidate gen (exact alias + FTS + embedding KNN, type-filtered) -> score -> merge/create
    def resolve(self, mention: EntityMention) -> tuple[Entity, bool]: ...  # (entity, created?)

class GraphStore:
    def upsert_entity(...) -> Entity: ...
    def add_relation(...) -> Relation: ...
    def invalidate_relation(rel_id, by, at) -> None: ...
    def neighborhood(entity_ids, *, hops, edge_filter, as_of=None,
                     max_nodes, max_edges) -> SubGraph: ...
    def seed(query: str, *, k) -> list[Entity]: ...          # hybrid over names+embeddings
    def profile(entity_id) -> EntityProfile: ...             # structured, budgeted summary
```

## The work — implement in phases; tests + acceptance gate per phase

### Phase 1 — Data model & migrations

- Create `rickshaw/memory/graph/models.py` (Entity, Relation, Mention, enums) and graph
  tables + sqlite-vec/FTS5 registration. Idempotent migration; existing DBs upgrade cleanly.
- **Accept:** tables + indices exist; CRUD round-trips; migration is re-runnable; existing
  memory tests still pass.

### Phase 2 — Extraction pipeline (deferred, pluggable, offline-degrading)

- Add a `GRAPH_EXTRACTION` job type to the worker/queue. On each distilled turn/record,
  enqueue extraction. `LLMGraphExtractor` when a provider is reachable; `HeuristicGraphExtractor`
  (gazetteer of known entities + typed patterns + co-occurrence) offline. Both emit the same
  `ExtractionResult`.
- **Accept:** given a fixture transcript, extraction yields expected entities + triples with
  the LLM extractor; the heuristic extractor produces a sensible non-empty subset offline
  with **no network**; extraction never runs on the read path.

### Phase 3 — Entity resolution & canonicalization

- Implement `EntityResolver`: candidate generation via exact alias + FTS + embedding KNN
  (type-filtered), scored by name similarity × embedding sim × type match; above threshold →
  merge into canonical entity (union aliases/attributes, sum mention_count, keep provenance,
  set `superseded_by` on the loser) else create new. Merges are **reversible** (logged).
- **Accept:** "Bob", "Bob Smith", "bob@corp" collapse to one entity on a labeled fixture;
  report **precision/recall** of merges; a distinct same-type entity is NOT wrongly merged.

### Phase 4 — Temporal correctness & contradictions

- Edges are bitemporal. When a new assertion contradicts an existing edge (same subject +
  predicate, incompatible object for a single-valued predicate, e.g. `member_of`), **invalidate**
  the old edge (`valid_to`, `invalidated_by`) rather than deleting it. Support `as_of` queries.
- **Accept:** "used to work at X, now at Y" yields a current edge to Y and an invalidated
  (but retrievable) edge to X; a current-state query returns Y only; an `as_of` past query
  returns X.

### Phase 5 — Graph retrieval: seed → traverse → profile

- `seed()` reuses hybrid search over entity names/embeddings. `neighborhood()` does bounded
  BFS (recursive CTE or in-process) with depth/breadth/time caps, cycle guards, and edge
  weighting by confidence × recency × weight. `profile(entity)` renders a compact, budgeted
  structured summary of an entity and its strongest current relations.
- **Accept:** multi-hop query returns the correct connected entities within caps; traversal
  is bounded and terminates on cyclic graphs; profile output fits a token budget.

### Phase 6 — Integration with context assembly + personable ego-graph

- Extend `assemble_context()` with a **graph pass**: seed entities from the query, traverse,
  and (a) pull in `MemoryRecord`s linked to seed/neighbor entities via mentions, and (b) add a
  compact graph summary — then fuse with the existing hybrid record retrieval and rerank with
  the existing `Ranker`, all under the token budget. Flat retrieval remains the floor; the
  graph augments, never replaces.
- Make the **`user` entity** first-class: maintain its ego-graph and expose an always-available,
  pinnable "about you" profile (preferences, projects, key collaborators).
- **Accept:** on multi-hop fixtures the assembled context contains the linked facts that flat
  hybrid retrieval alone misses; the "about you" profile surfaces correct personal preferences;
  budget is never exceeded; turning the graph off falls back cleanly to hybrid-only.

### Phase 7 — Consolidation (background hygiene)

- Worker jobs: periodic entity-resolution sweeps (catch merges missed at write time),
  edge dedup/weight reinforcement, **salience decay**, and pruning of stale low-salience
  nodes/edges (soft, reversible). Respect provenance.
- **Accept:** running consolidation on a seeded graph reduces duplicate entities/edges without
  losing provenance; salience decays for untouched nodes; pruning is reversible.

### Phase 8 — Evaluation harness (gates the whole change)

- Build `evals/graph/` (or `tests/`) with fixtures + metrics:
  - **Multi-hop recall**: NL queries whose answers require 2+ hops; measure answer hit-rate,
    and **show the graph beats the flat hybrid baseline specifically on multi-hop queries**.
  - **Entity resolution**: precision/recall on a labeled merge set.
  - **Temporal correctness**: current-state vs `as_of` queries return the right edges.
  - **Personable recall**: "what do you know about how I work / who I work with?" returns the
    right ego-graph facts.
- **Accept:** report all numbers in the final summary; multi-hop recall must measurably exceed
  the hybrid-only baseline; single-hop performance must not regress.

## How to work

- **Plan before coding.** Confirm the phase breakdown, the entity/relation type sets, and
  which extraction libraries are actually available offline, then proceed.
- One focused commit per phase, each with passing tests; don't mix phases.
- Keep offline-default deps light; document any one-time model download for the LLM/NER path
  and how to run fully offline (heuristic extractor + local embedder).
- Match existing code style, typing, and docstring conventions; reuse the embedder, vector
  index, FTS5, RRF, worker/queue, and Ranker rather than duplicating them.

## Definition of done

- Graph tables live in the same SQLite DB with idempotent migration and history backfill.
- Extraction runs deferred, uses the LLM when reachable, degrades to an offline heuristic;
  **all reads (seed/traverse/profile/assemble) are fully offline**.
- Entity resolution merges reversibly with reported precision/recall; edges are bitemporal
  with working `as_of` and contradiction handling; provenance is on every node and edge.
- `assemble_context()` fuses graph-derived context with hybrid retrieval under budget;
  flat retrieval still works with the graph disabled; the `user` ego-graph powers a pinnable
  "about you" profile.
- Eval harness shows multi-hop recall beating the hybrid-only baseline with no single-hop
  regression; numbers reported.
- All existing tests pass; new tests cover every phase.

## Explicit non-goals

- No external/server graph database; no cloud graph or embedding on the default path.
- Not replacing flat/hybrid retrieval — the graph augments it.
- No hard deletes on merge/contradiction — history is retained and reversible.
- No open-ended ontology: stick to the fixed entity/relation type sets above (extending them
  is a follow-up, not part of this task).
