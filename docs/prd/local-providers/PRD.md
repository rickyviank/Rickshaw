# PRD: Local provider support (llama.cpp, MLX-LM, Ollama, LM Studio)

Status: **approved 2026-07-14**
Interview record: [DRAFT.md](DRAFT.md)

## 1. Problem Statement

Rickshaw's pitch is "your driver, your memory" — any model behind one normalized
interface. Today that promise effectively excludes locally-hosted models: although
llama.cpp (`llama-server`), MLX-LM (`mlx_lm.server`), Ollama, and LM Studio all
speak the OpenAI-compatible protocol rickshaw already supports, a user who wants
to chat against them must (a) know each server's default base URL by heart,
(b) walk through `/provider add` manually, and (c) will then hit a hard failure:
`OpenAIProvider.validate()` refuses to proceed when no API key is set
(`rickshaw/providers/openai_provider.py:204`), even though local servers
typically require none. There are no presets, `--validate-only` cannot be used
against a local endpoint, and a stopped server surfaces as generic retry noise
rather than an actionable message.

## 2. Proposed Solution

Make local providers first-class via configuration and validation changes only —
no new wire adapter (the existing OpenAI-compatible path is reused end-to-end).

1. **Four built-in presets** added to `_BUILTIN_PROFILES` in `rickshaw/config.py`:

   | Preset name | Default base URL | Server |
   |---|---|---|
   | `llamacpp` | `http://localhost:8080/v1` | llama.cpp `llama-server` |
   | `mlx` | `http://localhost:8080/v1` | `mlx_lm.server` |
   | `ollama` | `http://localhost:11434/v1` | Ollama |
   | `lmstudio` | `http://localhost:1234/v1` | LM Studio |

   All use `wire_format="openai"`, empty `model` (resolved at activation; see
   §3 J1), and a dedicated `api_key_env` (e.g. `LLAMACPP_API_KEY`) that is sent
   *if set* (for `llama-server --api-key` setups) but never required. Base URLs
   are overridable the same way as other profiles (settings.json / env / yaml).

2. **Keyless validation for local endpoints** (`OpenAIProvider.validate()`):
   when `is_local_url(base_url)` is true, skip the key-presence check and
   instead require `GET {base_url}/models` to succeed **and** list ≥1 model.
   Failure messages distinguish:
   - *unreachable* → "«preset» unreachable at «url» — is «server binary» running?"
   - *no models* → per-server hint (e.g. "server is up but lists no models — run
     `ollama pull <model>` / load a model in LM Studio").
   Hosted endpoints keep current behavior exactly.

3. **Model resolution on activation** (TUI switch / picker path in
   `rickshaw/tui.py`): when a local preset is activated with no persisted model,
   fetch `/v1/models`; exactly one → select silently and persist; multiple →
   reuse the existing `/settings` model-picker step. If a persisted model is no
   longer listed (e.g. different gguf loaded), fall back to the same
   auto/pick logic instead of failing.

4. **Per-profile timeout**: optional `timeout` (seconds) field on
   `ProviderProfile` and the settings.json provider schema, applied to
   generation requests (`rickshaw_ai` HTTP client construction,
   `rickshaw_ai/factory.py:168`). Default stays **120s for all endpoints,
   local included**. Connect timeout remains short so down servers fail fast.

5. **Fail-fast down-server UX**: connection-refused against a local endpoint
   bypasses the Orchestrator's retry/backoff and fails the turn immediately
   with the same actionable per-preset hint as §2.2. Hosted endpoints keep
   existing retry behavior.

6. **Effort handling**: local models report `effort_levels=[]`;
   `reasoning_effort` is never sent to local endpoints. The TUI's
   "effort reset to medium" warning becomes a one-time quiet note for local
   providers (not repeated on every switch).

7. **`/provider add` keyless support**: the wizard's `api_key_env` step becomes
   optional (Enter to skip) when the entered base URL is local, consistent
   with §2.2.

### Affected modules

- `rickshaw/config.py` — presets, `ProviderProfile.timeout`
- `rickshaw/providers/openai_provider.py` — keyless local `validate()`
- `rickshaw/providers/build.py` — timeout plumb-through
- `rickshaw/tui.py` — model resolution, picker notes, wizard step, hints
- `rickshaw/orchestrator.py` — non-retryable local connection errors
- `rickshaw_ai/factory.py` — configurable client timeout
- `tests/` — coverage for each behavior above; `README.md` — presets table

## 3. User Journeys

**J1 — First chat against llama.cpp (happy path).** User starts `llama-server`
with a gguf; launches `rickshaw` with no cloud keys and no persisted provider →
on-launch picker lists `llamacpp` alongside hosted providers → user types
`llamacpp` → validation hits `/v1/models`, finds the loaded model, exactly one →
auto-selected and persisted → status bar shows `llamacpp · <model>` → user chats;
streaming works through the existing OpenAI-compatible path. No API key at any step.

**J2 — MLX on a non-default port.** `mlx_lm.server --port 9090`. User picks
`mlx`, validation fails reachability at :8080 with hint. User either sets the
override for the preset's base URL (settings.json entry `mlx.base_url`) or runs
`/provider add` (name `mlx-9090`, base URL `http://localhost:9090/v1`,
api_key_env skipped — J7). Activation then proceeds as J1.

**J3 — Ollama with several installed models.** User picks `ollama` →
`/v1/models` returns >1 → existing model picker lists them → user selects →
choice persisted. `/models` later re-lists on demand (local-aware cache).

**J4 — Switching hosted ↔ local mid-session.** `/provider llamacpp` from
`openai` (or back): memory layer is provider-independent, so recall context
carries over; effort note (one-time) appears when entering local; effort
restores normally on return to hosted. Usage/price segments show no cost for
local turns.

**J5 — Server down at activation (edge).** Picker selection of a local preset
with no server running → validation fails instantly (short connect timeout) →
"llamacpp unreachable at http://localhost:8080 — is llama-server running?" →
user stays on previous provider (or no-provider state on first launch).

**J6 — Server dies mid-session (edge).** Turn submission hits connection
refused → no retry/backoff → turn fails immediately with the same hint →
transcript keeps the failed prompt so the user can re-submit after restarting
the server.

**J7 — Keyless `/provider add` (edge).** Wizard detects local base URL →
`api_key_env` step shows "(optional for local — Enter to skip)" → skipping
stores an empty `api_key_env`; build path already tolerates this for local
endpoints.

**J8 — `--validate-only` in scripts (edge).** `rickshaw --provider llamacpp
--validate-only` exits 0 iff the server is reachable and lists ≥1 model; exit 1
with the actionable message otherwise. Works keyless.

**J9 — Model swapped on server (edge).** User restarts `llama-server` with a
different gguf → next activation/`/models` re-verifies: persisted model absent →
single new model auto-selected (note shown) or picker if several.

**J10 — Slow first token (edge).** Big model, cold load, >120s prompt eval →
generation times out at the profile default → error suggests raising the new
per-profile `timeout` setting for that preset.

**Known limitation (accepted):** `llamacpp` and `mlx` share default port 8080.
Rickshaw does not fingerprint which server actually answers; the preset name is
a label, and whichever OpenAI-compatible server is listening will be used.

## 4. Constraints

- Reuse the existing OpenAI-compatible adapter; **no new wire adapter** for local servers.
- Presets are harness-level only (`_BUILTIN_PROFILES`); `rickshaw_ai`'s builtin
  registry stays a hosted-provider catalog.
- Default generation timeout stays 120s everywhere (user-stated); tuning is
  explicit per profile.
- API keys must remain env-var-only (existing security invariant: never on disk).
- Hosted-provider behavior (validation, retries, effort) must be byte-for-byte unchanged.
- Backward compatibility: existing settings.json files without `timeout` and
  user-defined custom providers must keep working unmodified.

## 5. Decisions Log

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| D1 | Presets + keyless fixes | Keyless-fix only; full local-first UX (port scanning) | Discoverability without probing complexity/false positives |
| D2 | Four presets: llamacpp, mlx, ollama, lmstudio | Only the two named servers | Marginal cost per preset ≈ one config entry; covers most local users |
| D3 | Local validate = reachability + ≥1 model listed; key sent if set, never required | Skip validation; reachability-only | Validation should prove a chat turn can plausibly succeed; catches llama.cpp "up, nothing loaded". Accepted caveat: Ollama/LM Studio list installed (not loaded) models |
| D4 | Model: auto-select if exactly one, picker if several; re-verify persisted model | Always picker; always first | Fastest path to chatting without arbitrary choices |
| D5 | Effort: no-op for local, one-time quiet note | Blind `reasoning_effort` passthrough; per-preset opt-in flag | Zero wire risk (strict servers may 400); honest UX; flag can come later |
| D6 | Embeddings/memory: out of scope entirely | Auto-detect `/v1/embeddings`; document-only | Keep PRD focused on "connect and chat" |
| D7 | Per-profile `timeout`, default 120s everywhere (user-specified) | Fixed longer local default (600s); no change | Uniform predictable default + explicit escape hatch |
| D8 | Down server: fail fast, no retries, actionable per-preset hint | Keep retry/backoff; auto-offer provider picker | User-fixable state should surface instantly |
| D9 | Presets in `_BUILTIN_PROFILES` only | Dual registration in `rickshaw_ai`; library-only | Smallest change for first-class TUI presets; library already supports custom base_urls |

## 6. Out of Scope

- Embeddings via local servers, and any memory-layer changes (D6). Memory keeps
  its current TFIDF default.
- Auto-discovery of running servers / port scanning (D1).
- Server fingerprinting to disambiguate the shared 8080 default (accepted limitation, §3).
- `reasoning_effort` passthrough or effort emulation for local models (D5).
- Non-OpenAI local protocols (e.g. Ollama's native `/api/chat`, vLLM-specific extensions).
- `rickshaw_ai` builtin-registry entries, pricing/cost tracking for local models.
- Managing the local servers themselves (starting, stopping, model downloads).
