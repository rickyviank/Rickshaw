# DRAFT: Local provider support (llama.cpp, MLX-LM)

> Working document for the spec interview. Updated after each user decision.
> Status: **interview complete — synthesized into [PRD.md](PRD.md), awaiting sign-off**

## Request (verbatim)

"I want to make sure rickshaw can connect to locally-hosted providers from the likes of llama.cpp or MLX-LM."

## Current state (codebase findings)

Both llama.cpp (`llama-server`) and MLX-LM (`mlx_lm.server`) expose OpenAI-compatible
APIs (`/v1/chat/completions`, `/v1/models`), so much of the plumbing already exists:

- `ProviderProfile` (`rickshaw/config.py`) supports custom endpoints via
  `wire_format="openai"` with a custom `base_url`; persisted in `~/.rickshaw/settings.json`.
- `is_local_url()` / `ProviderProfile.is_local_endpoint()` already recognize
  loopback, `*.local`, and RFC 1918 hosts; `build_provider_from_profile()` suppresses
  the missing-API-key warning for local endpoints.
- `/provider add` TUI wizard registers custom endpoints (name, base_url, api_key_env,
  wire_format); the on-launch picker lists builtins + configured customs.
- `LLMProvider._cached_available_models(..., is_local=...)` is already local-aware.
- Tests already exercise `localhost:11434` (Ollama-style) URLs.

## Gaps found

1. **Keyless validation fails**: `OpenAIProvider.validate()` raises when `api_key` is
   empty (unless OAuth creds exist) — local servers typically need no key, so
   `--validate-only` and post-switch validation break (`rickshaw/providers/openai_provider.py:204`).
2. **`/provider add` requires `api_key_env`** — friction/confusion for keyless local servers.
3. **No local presets**: no built-in profiles for llama.cpp / MLX-LM (or Ollama /
   LM Studio); users must know default base URLs (`http://localhost:8080/v1`,
   `http://localhost:11434/v1`, `http://localhost:1234/v1`, ...).
4. **Effort mapping**: `_model_supports_effort()` only matches OpenAI `o*` models →
   local models get `effort_levels=[]`, TUI resets effort to medium with a warning.
5. **Embeddings**: memory's `ProviderEmbedder` hits `/v1/embeddings` — llama.cpp only
   supports it with `--embedding`; mlx_lm.server not at all. TFIDF offline fallback exists.
6. **Timeouts**: local first-token latency (model load / prompt eval) can far exceed
   hosted-API expectations; request timeouts may need to be raised/configurable.
7. **No discovery/health UX**: no auto-detection of running local servers, no
   friendly "is llama-server actually running?" error.

## Best-guess approach (to be confirmed)

Add first-class local provider presets (at minimum `llamacpp`, `mlx`) that reuse the
existing OpenAI wire format, plus fix keyless validation for local endpoints. No new
adapter code — this is configuration + validation + UX work.

## Assumptions (to be interviewed)

- A1. Scope: presets + keyless fixes (not just docs; not full auto-discovery). — **RESOLVED: confirmed**
- A2. Target servers: llama.cpp + MLX-LM required; Ollama / LM Studio nice-to-have. — **RESOLVED: all four ship as presets**
- A3. Keyless local endpoints should validate via `GET /v1/models` reachability instead of key presence. — **RESOLVED: deep check (reachability + ≥1 model listed)**
- A4. Model selection comes from the server's `/v1/models` (no hardcoded model lists). — **RESOLVED: auto-select if exactly one, picker if several**
- A5. Effort for local models: silently no-op vs. mapped to something (e.g. sampling/reasoning params). — **RESOLVED: no-op with one-time notice**
- A6. Embeddings/memory: keep TFIDF default for local; optionally use llama.cpp embeddings when available. — **RESOLVED: out of scope**
- A7. Timeout policy for local endpoints (longer? configurable? no timeout?). — **RESOLVED: per-profile `timeout` field, 120s default everywhere**
- A8. Failure UX when the local server is down (error message quality, retry, fallback). — **RESOLVED: fail fast + actionable hint**
- A9. Non-TUI/library usage (`rickshaw_ai` builtins registry) — should local presets exist there too? — **RESOLVED: harness-only (`_BUILTIN_PROFILES`)**

## User journeys to cover

- J1. First launch, no cloud keys, llama-server running → pick local provider → chat.
- J2. `/provider add`-style registration of a non-default local URL/port.
- J3. Switching between a local and a hosted provider mid-session (memory carries over).
- J4. Local server not running → clear, actionable error.
- J5. Model swap on the local server (e.g. new gguf loaded) → `/models` reflects it.
- J6. `--validate-only` in CI/scripts against a local endpoint.

## Decisions log

- **D1 (scope)**: Built-in local presets + keyless validation fixes. Rejected
  "keyless-fix only" (poor discoverability — users must know server URLs) and
  "full local-first UX with port scanning" (auto-discovery adds probing complexity
  and false-positive risk disproportionate to the goal of "can connect").
  *Rationale: first-class feel with minimal new surface; reuses the existing
  OpenAI-compatible adapter and profile system.*
- **D2 (preset list)**: Four presets — `llamacpp` (http://localhost:8080/v1),
  `mlx` (http://localhost:8080/v1), `ollama` (http://localhost:11434/v1),
  `lmstudio` (http://localhost:1234/v1). Rejected limiting to the two named
  servers: marginal cost per preset is one config entry (same OpenAI-compatible
  adapter), and Ollama/LM Studio cover most local-hosting users.
  *Rationale: maximize coverage at near-zero added complexity.*
- **D3 (keyless validation)**: For local endpoints, `validate()` skips the
  key-presence requirement and instead requires `GET /v1/models` to succeed AND
  return ≥1 model. If the profile's key env var is set anyway (e.g.
  `llama-server --api-key`), it is still sent. Error messages must distinguish
  "server unreachable" from "server up but no models available" (the latter with
  a per-server hint, e.g. `ollama pull ...`). Rejected skip-entirely (makes
  `--validate-only` meaningless locally) and reachability-only (misses the
  common llama.cpp "up but nothing loaded" state). Caveat accepted: for
  Ollama/LM Studio the list reflects *installed/downloaded* models (they load
  lazily), so "≥1 listed" means "something is available", not "loaded in RAM".
  *Rationale: validation should prove a chat turn can plausibly succeed.*
- **D4 (model selection)**: On activating a local preset with no persisted model:
  query `/v1/models`; exactly one result → select it silently (and persist);
  multiple → reuse the existing `/settings` model-picker step. No hardcoded
  model lists for local presets. Rejected always-picker (needless step for
  single-model llama.cpp/MLX servers) and always-first (arbitrary when several
  models are installed). The already-selected model is re-verified against
  `/v1/models` on switch; if it disappeared (e.g. different gguf loaded),
  fall back to the same auto/pick logic.
  *Rationale: fastest path to chatting without surprise model choices.*
- **D5 (effort levels)**: Local models report `effort_levels=[]` (existing
  pattern); `reasoning_effort` is never sent to local endpoints. The TUI's
  "effort reset to medium" warning becomes a one-time, quieter note for local
  providers instead of firing on every switch. Rejected blind passthrough
  (strict servers may 400 on unknown fields; silent no-op elsewhere misleads
  users into thinking effort works) and a per-preset opt-in flag (config
  surface not justified yet — can be added later if local reasoning-model
  support matures).
  *Rationale: zero wire risk and honest UX about what the server honors.*
- **D6 (embeddings/memory)**: Out of scope. The embedding path is untouched —
  memory keeps its current behavior (TFIDF default; `RICKSHAW_EMBEDDING_PROVIDER`
  unchanged). Local presets are chat-only in this PRD. Rejected auto-detection
  (probing magic + mid-session quality shifts) and even documenting local
  embedding setups (defer entirely).
  *Rationale: keep this PRD focused on "can connect and chat".*
- **D7 (timeouts)**: Add an optional per-profile `timeout` (seconds) to
  `ProviderProfile` / `settings.json`, applied to generation requests. Default
  remains 120s for ALL endpoints, local included (user override of my 600s-local
  suggestion). Connect timeout stays short so a down server fails fast. Users
  with cold-load or slow-eval setups raise it per profile. Rejected fixed
  600s-local default (user prefers uniform default + explicit tuning) and
  no-change (big local models would hit opaque 120s failures with no recourse).
  *Rationale: predictable uniform default; escape hatch where needed.*
- **D8 (down-server UX)**: Connection-refused against a local endpoint is
  treated as non-retryable: the Orchestrator's retry/backoff is skipped and the
  turn fails immediately with a per-preset actionable hint (server name, URL,
  and start command suggestion, e.g. "is llama-server running?"). Hosted
  endpoints keep existing retry behavior. Rejected keep-retries (delays an
  error only the user can fix) and auto-offering the provider picker (extra
  TUI state; interrupts flow).
  *Rationale: a stopped local server is a user-fixable state — surface it
  instantly and clearly.*
- **D9 (preset home)**: Presets live in `_BUILTIN_PROFILES`
  (`rickshaw/config.py`) only. They surface in the on-launch picker and
  `/provider` list through the existing builtin+settings merge. `rickshaw_ai`'s
  builtins registry remains a hosted-provider catalog (library users can
  already target local servers by constructing `ProviderInfo` with a custom
  base_url). Rejected dual registration (empty model catalogs sit oddly in a
  pricing/model registry) and library-only (largest refactor, needs a
  ProviderInfo→ProviderProfile bridge).
  *Rationale: smallest change that makes presets first-class in the TUI.*
