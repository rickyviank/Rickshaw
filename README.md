# Rickshaw

```
o--o  rickshaw · your driver, your memory
```

A multi-LLM provider harness with a normalized interface and user-selectable reasoning effort levels.

The slogan captures the two pillars:

- **your driver** — pick any driver/model behind one normalized interface (OpenAI, Devin, or your own provider) and dial the reasoning effort per session or per turn.
- **your memory** — a fully offline, user-owned [semantic memory layer](#semantic-memory-layer) that persists and ranks context so it travels with you across providers.

> **`rickshaw-ai`** — the provider layer is now a standalone, importable package,
> [`rickshaw_ai`](rickshaw_ai/README.md): a unified async LLM API with provider
> collections, automatic auth resolution (API key + OAuth), token & cost
> tracking, streaming tool calls, and cross-provider session hand-off. The
> harness's `rickshaw/providers/` is a thin synchronous facade over it.

## Setup

```bash
# Clone and install
git clone https://github.com/rickyviank/Rickshaw.git
cd Rickshaw
pip install -e ".[dev]"

# Configure credentials
cp .env.example .env
# Edit .env with your API keys
```

Textual and Rich are included as hard dependencies — no extra install step
is needed for the terminal UI.

### Required environment variables

| Variable | Required | Description |
|---|---|---|
| `RICKSHAW_PROVIDER` | No | Default provider (e.g. `openai`, `anthropic`). When unset and no `--provider` flag, the TUI prompts interactively. |
| `RICKSHAW_EFFORT` | No | Default effort level: `low`, `medium`, `high`. Defaults to `medium`. |
| `OPENAI_API_KEY` | For OpenAI | OpenAI API key. |
| `OPENAI_BASE_URL` | No | Override the OpenAI API base URL. |
| `OPENAI_MODEL` | No | Chat model to use (default: `gpt-4o`). |
| `OPENAI_EMBEDDING_MODEL` | No | Embedding model (default: `text-embedding-3-small`). |
| `DEVIN_API_KEY` | For Devin | Devin API key. |
| `DEVIN_BASE_URL` | No | Override the Devin API base URL. |
| `ANTHROPIC_API_KEY` | For Anthropic | Anthropic API key. |
| `ANTHROPIC_BASE_URL` | No | Override the Anthropic API base URL (default: `https://api.anthropic.com`). |
| `ANTHROPIC_MODEL` | No | Claude model to use (default: `claude-3-5-sonnet-latest`). |
| `RICKSHAW_EMBEDDING_PROVIDER` | No | Separate embedding provider (e.g. `openai`) independent of the chat provider. |
| `LLAMACPP_BASE_URL` etc. | No | Base-URL overrides for the local presets (`MLX_BASE_URL`, `OLLAMA_BASE_URL`, `LMSTUDIO_BASE_URL`). |
| `LLAMACPP_API_KEY` etc. | No | Optional keys for local servers that enforce one (sent when set, never required). |

You may also supply values in a `config.yaml` file in the working directory.

## Supported providers

- **openai** — OpenAI chat completions and embeddings APIs.
- **devin** — Devin coding agent API (skeleton; fill in TODOs from Devin API docs).
- **anthropic** — Anthropic Claude Messages API (chat + tool-calling; no embeddings).
- **llamacpp / mlx / ollama / lmstudio** — locally-hosted OpenAI-compatible
  servers (see [Local providers](#local-providers-llamacpp-mlx-lm-ollama-lm-studio)).

### Local providers (llama.cpp, MLX-LM, Ollama, LM Studio)

Four built-in presets connect to locally-hosted OpenAI-compatible servers — no
API key required:

| Preset | Default base URL | Server |
|---|---|---|
| `llamacpp` | `http://localhost:8080/v1` | llama.cpp `llama-server` |
| `mlx` | `http://localhost:8080/v1` | `mlx_lm.server` |
| `ollama` | `http://localhost:11434/v1` | Ollama |
| `lmstudio` | `http://localhost:1234/v1` | LM Studio local server |

```bash
llama-server -m model.gguf   # start your server, then:
rickshaw --provider llamacpp
```

- **Keyless by default** — validation checks that the server is reachable and
  lists at least one model (`GET /v1/models`) instead of requiring a key. If
  your server does use one (e.g. `llama-server --api-key`), set the preset's
  env var (`LLAMACPP_API_KEY`, `MLX_API_KEY`, `OLLAMA_API_KEY`,
  `LMSTUDIO_API_KEY`) and it will be sent.
- **Model resolution** — with no model configured, the server's `/v1/models` is
  queried on activation: a single model is selected automatically; several
  models open the interactive picker.
- **Non-default ports** — override with `LLAMACPP_BASE_URL`, `MLX_BASE_URL`,
  `OLLAMA_BASE_URL`, `LMSTUDIO_BASE_URL`, or a `providers.<name>.base_url`
  entry in `~/.rickshaw/settings.json`.
- **Fail-fast errors** — a stopped server fails immediately with an actionable
  hint (no retry/backoff) instead of generic retries.
- **Effort levels** are not applicable to local servers; `reasoning_effort` is
  never sent and a one-time note is shown instead of repeated warnings.
- **Timeouts** — generation defaults to 120s everywhere; raise it per provider
  with `providers.<name>.timeout` (seconds) in settings for big/cold models
  (applies to openai-wire profiles, which includes all local presets).
- Note: `llamacpp` and `mlx` share port 8080 — the preset name is a label;
  whichever compatible server is listening will be used.

Adding a new provider: subclass `rickshaw.providers.base.LLMProvider`, implement the abstract methods, and register it:

```python
from rickshaw.providers.factory import register
register("my_llm", MyLLMProvider)
```

## Usage

The single `rickshaw` command launches a full-screen Textual TUI:

```bash
# Launch — prompts for provider if none is configured
rickshaw

# Override the provider (optional)
rickshaw --provider openai --effort high

# Or via python -m
python -m rickshaw --provider openai

# Validate connectivity only (exits 0 on success, 1 on failure)
rickshaw --provider openai --validate-only

# Launch even if validation fails (by default, failure exits non-zero)
rickshaw --provider openai --allow-unvalidated
```

When launched without `--provider` and with no persisted provider in
`~/.rickshaw/settings.json`, the TUI opens a multi-step picker to choose
provider, model, and effort. The provider step lists all built-in providers
(from `rickshaw_ai`). Selecting an OAuth-capable provider (e.g. `anthropic`,
`openai`, `copilot`) triggers an in-TUI OAuth login flow before continuing to
model selection. `--provider` remains available as an optional override for
backward compatibility.

### Terminal UI

A deliberately minimalist full-screen terminal UI (built on
[Textual](https://textual.textualize.io/)), in the spirit of Claude Code /
Codex: a scrollable transcript with hairline rules between turns, a borderless
pinned input, **streaming** replies rendered as Markdown, a faint "thinking"
hint, and slash-command autocomplete. Near-monochrome with a single amber accent
(the `›` marker on your messages) — no status bar or footer chrome. Every turn
is routed through the `Orchestrator`, so the semantic memory layer
(`remember`/`recall`/`forget`) and graceful-degradation info are active and
surfaced.

- **Streaming:** when the provider supports it *and* isn't advertising tools,
  replies stream token by token; otherwise the final answer is rendered once
  generation completes. (Streaming through the tool-call loop is a provider-side
  follow-up — the provider's `stream()` doesn't yet parse tool calls.)
- **Memory** persists to a local SQLite file (`--db-path`, default
  `rickshaw_memory.db`) so context carries across sessions.
- **Slash-commands:** `/help`, `/status` (provider · model · effort),
  `/settings` (interactive provider/model/effort picker), `/provider` (open the
  provider picker), `/provider add` (register a custom OpenAI-compatible
  endpoint), `/model` (open the model picker for the current provider), `/effort`
  (open the effort picker for the current provider/model), `/login` (authenticate
  the active provider via OAuth), `/models` (list current provider's models),
  `/memory`, `/clear` (clear the transcript), `/keybindings` (open keybinding
  overlay), `/quit`. `/engine` is still accepted as a deprecated alias for
  `/provider`. Type `/` for inline autocomplete.
- **Keys:** `Esc` interrupts an in-flight turn, closes the slash menu, clears the
  prompt, or exits a focused trace; `Ctrl+L` redraws the screen; `Ctrl+C` quits.

### LLM visibility and traces

A live phase-aware spinner replaces the generic `Thinking…` indicator. It updates
through phases such as `Assembling context…`, `Calling LLM…`,
`Calling recall…`, `Retry 1/2…`, and `Streaming answer…` so you can see why a
turn is taking time.

Once a turn completes, a collapsed trace block appears under the assistant
message. The collapsed summary counts grouped display lines as `steps`.
Expanding the trace shows a chronological, color-coded timeline of the full turn
lifecycle.

The transcript is a single focus ring. From the prompt, `Tab` moves focus to the
newest turn block; `Shift+Tab` moves to the oldest. Repeated `Tab`/`Shift+Tab`
step through turn blocks in reverse chronological order and wrap back to the
prompt. `Enter` on a collapsed turn block expands its trace and focuses the first
trace event; `Escape` collapses the trace and returns focus to the turn block.

Inside an expanded trace, `Tab`/`Shift+Tab` move between events and continue to
the next/previous turn block at the boundaries. `Enter` on a focused event
toggles its full payload (raw JSON for non-delta events, generated content for
answer/thinking blocks).

- **Navigate turns and events:** `Tab` moves focus forward, `Shift+Tab` moves
  focus backward. The ring runs `prompt -> newest turn -> older turns -> oldest
  turn -> prompt`.
- **Expand/collapse trace:** `Enter` on a collapsed turn block expands it and
  focuses the first trace event; `Enter` on a focused event expands or collapses
  that event's payload; `Escape` collapses the trace and returns focus to the
  turn block.
- **Human-readable view:** expanded traces render as a chronological list of
  grouped `[answer]` and `[thinking]` blocks (or `[partial answer]` /
  `[partial thinking]` when a turn is interrupted) with bracket-label summaries
  for every non-delta event, e.g. `[context]`, `[llm]`, `[tool]`, and
  `[retry]`. Each line is prefixed with a relative timestamp and color-coded by
  event type.
- **Raw JSON toggle:** when a trace is expanded, press `R` to switch the entire
  trace between the human-readable summary and the canonical raw JSON event
  payloads, and back.
- **Long payloads:** long summary values are truncated to fit the terminal and
  can be expanded inline to show the full value. Very long answer or thinking
  blocks cap at 30% of the terminal height and can be expanded into a scrollable
  region.
- **Persistence:** traces are stored in the same SQLite database as memory
  (`rickshaw_memory.db` by default, or the path passed to `--db-path`) in the
  `traces` and `trace_events` tables. They survive `/clear` and can be queried
  outside the TUI.

### `/settings` — interactive provider/model/effort picker

`/settings` opens a centered modal picker with up to three steps:

1. **Pick a provider** — lists all built-in providers (from `rickshaw_ai`), with
   the active one highlighted. OAuth-capable providers are tagged with `(oauth)`.
   Custom providers registered via `/provider add` are not shown here; set them
   in `~/.rickshaw/settings.json` and relaunch.
2. **OAuth login** — if the chosen provider supports OAuth and no credential is
   present, an in-TUI login flow is triggered (browser-based PKCE or device-code).
   Credentials are persisted to `~/.rickshaw/credentials.json`.
3. **Pick a model** — lists the chosen provider's `available_models()` with the
   active model highlighted. Local providers always show this step, even when
   only one model is available.
4. **Pick effort** — shows only the effort levels the chosen provider/model
   supports. If just one level is available it is shown with a note.

Use `Up`/`Down`/`Tab`/`Shift+Tab` to move the highlight, `Enter` to select, and
`Esc` to go back one step or cancel the picker.

If the chosen provider does not support the current effort level, effort is
automatically reset to `medium` and a warning is shown.

### `/models` — list available models

`/models` non-interactively lists the **current** provider's `available_models()`
with the active one marked — a quick discoverability shortcut.

### `/provider` command

Use `/provider` to open the interactive provider picker, or `/provider add` to
register a custom OpenAI-compatible endpoint step by step. The deprecated
`/engine` alias still works for backward compatibility and opens the same picker.
Changes are saved to `~/.rickshaw/settings.json` and take effect immediately.

### `/model` — pick a model for the current provider

`/model` opens the model picker for the currently active provider. It lists the
provider's `available_models()` and, after a selection, advances to the effort
picker if the model supports multiple effort levels.

### `/effort` — pick the reasoning effort

`/effort` opens the effort picker for the current provider/model. Only effort
levels supported by the active model are shown. If the selected effort is not
supported by a later model switch, it is reset to `medium` with a warning.

### `/login` — re-authenticate via OAuth

`/login` triggers the OAuth login flow for the currently active provider. Use it
to refresh an expired token or to authenticate a freshly selected OAuth provider.
Key-based providers (no OAuth support) are told to set their API key env var
instead.

### Persistent settings (`~/.rickshaw/settings.json`)

Rickshaw persists user preferences in `~/.rickshaw/settings.json`.  The file is
created with defaults on first launch.  Schema:

```json
{
  "version": 1,
  "provider": "openai",
  "effort": "medium",
  "embedding_provider": "openai",
  "embedding_model": "text-embedding-3-small",
  "providers": {
    "deepseek": {
      "base_url": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "api_key_env": "DEEPSEEK_API_KEY",
      "wire_format": "openai",
      "timeout": 300
    }
  }
}
```

Resolution order (later wins): `config.yaml` → `~/.rickshaw/settings.json` →
environment variables.

**API keys are never written to disk.**  Only the *name* of the environment
variable holding the key (`api_key_env`) is stored in the settings file.

### Effort levels

Rickshaw normalizes reasoning effort into three levels: **low**, **medium**, **high**.

- Set the session default with `--effort`:
  ```bash
  rickshaw --effort high
  ```
- Override per-turn inside the REPL:
  ```
  you> /effort low
    Effort set to low for subsequent turns.
  you> Summarize this document
  ```
- Each turn displays the effort used:
  ```
  [effort: high]  (gpt-4o)
  Here is the response...
  ```
- If the active provider does not honor the chosen effort level, a warning is shown.

### Provider capabilities

Each provider reports its capabilities via `provider.capabilities()`:

```python
from rickshaw.providers import get_provider

p = get_provider("openai", api_key="sk-...")
caps = p.capabilities()
print(caps.streaming)    # True
print(caps.embeddings)   # True
print(caps.effort_levels)  # [Effort.LOW, Effort.MEDIUM, Effort.HIGH]
```

## Normalized Tool Calling

The provider interface supports normalized tool calls via `ToolSpec` and `ToolCall`:

```python
from rickshaw.providers import ToolSpec, ToolCall, get_provider

provider = get_provider("openai", api_key="sk-...")
tools = [
    ToolSpec(
        name="remember",
        description="Store a fact in memory.",
        parameters={
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": ["fact"],
        },
        category="memory",   # classification hint ("memory" | "general")
        side_effect=True,     # read-only tools set this False
    )
]

# tools *advertises* which tools are available; tool_choice controls whether the
# model is encouraged/required/forbidden to use them ("auto" | "none" | "required").
response = provider.complete(messages, tools=tools, tool_choice="auto")
for tc in response.tool_calls:
    print(tc.name, tc.arguments)  # e.g. "remember" {"fact": "..."}
```

- `ToolCall` is a pure normalized dataclass; provider-specific parsing lives on
  each provider (`OpenAIProvider._parse_tool_calls`), not on the base type.
- `tool_choice` defaults to `None` (provider decides). `OpenAIProvider` forwards
  it to the API; `DevinProvider` accepts but ignores it.
- `Response.tool_calls` defaults to `[]` — existing code is unaffected.
- Providers without function-calling (e.g. Devin) accept the `tools` parameter but ignore it.

### Tool registry (generalized dispatch)

Tool dispatch is decoupled from any specific backend via `ToolRegistry`, which
validates arguments against each tool's JSON schema and supports sync **and**
async handlers:

```python
from rickshaw.memory import MemoryService
from rickshaw.memory.tools import build_memory_registry
from rickshaw.providers.base import ToolCall

registry = build_memory_registry(MemoryService())
# register additional (even async) tools: registry.register(name, handler, spec)

result = registry.dispatch(ToolCall(id="1", name="recall", arguments={"query": "prefs"}))
# or: await registry.async_dispatch(tool_call)
```

The `Orchestrator` accepts a `ToolRegistry` via DI (defaulting to the memory
registry) and returns a structured `TurnResult(text, warnings, tool_calls_made,
degraded, model, usage)` so callers can detect degradation without parsing the
text. `run_turn(task_input, on_delta=...)` optionally streams the final answer:
with a streaming provider that isn't advertising tools it yields real token
deltas via `provider.stream()`; otherwise it delivers the final text as a single
delta. Passing `on_delta=None` preserves the original non-streaming behavior.

## Semantic Memory Layer

A fully offline semantic memory layer enables persistent, ranked context retrieval:

```python
from rickshaw.memory import MemoryService
from rickshaw.memory.embedder import TFIDFEmbedder

memory = MemoryService(embedder=TFIDFEmbedder())

# Store a fact
record = memory.write("User prefers dark mode")

# Retrieve relevant context (sensitive records are excluded here, before ranking)
context = memory.assemble_context("What are the user's preferences?")
```

The default `TFIDFEmbedder` is an offline, semantically-meaningful embedder
(fit-on-the-fly TF-IDF + feature hashing, L2-normalized) — a stepping stone
toward learned embeddings (see [FUTURE.md](FUTURE.md)).

### Architecture

| Component | Module | Description |
|---|---|---|
| **MemoryRecord** | `rickshaw/memory/record.py` | Core data unit with scope, type, importance, embedding |
| **Embedder** | `rickshaw/memory/embedder.py` | `TFIDFEmbedder` (offline, semantic) or `ProviderEmbedder` (API-backed) |
| **Store** | `rickshaw/memory/store.py` | SQLite persistence (source of truth); scope-filtered KNN search via ChromaDB with brute-force cosine fallback |
| **Ranker** | `rickshaw/memory/ranker.py` | Weighted-sum scoring (relevance + recency + importance) with MMR diversity |
| **MemoryService** | `rickshaw/memory/service.py` | Facade: dedupe-on-write, sensitive filtering, ranked retrieval, `remember`/`recall`/`forget` |
| **Memory Tools** | `rickshaw/memory/tools.py` | Tool specs + `build_memory_registry` wiring memory ops into a `ToolRegistry` |
| **ToolRegistry** | `rickshaw/tool_registry.py` | Backend-agnostic tool dispatch with schema validation + sync/async handlers |
| **PromptBuilder** | `rickshaw/prompt/builder.py` | Token-budgeted prompt assembly (sensitive records already excluded upstream) |
| **Orchestrator** | `rickshaw/orchestrator.py` | Turn loop with retry/backoff; returns a `TurnResult` |
| **Worker** | `rickshaw/worker.py` | Deferred importance scoring, compaction/reflection, TTL eviction |
| **JobQueue** | `rickshaw/queue.py` | In-memory FIFO queue for deferred work items |

### Offline demo

```bash
python examples/offline_demo.py
```

Runs a full turn cycle using `TFIDFEmbedder` and a fake provider — no API keys needed.

### Optional: indexed vector search

`MemoryStore` keeps SQLite as the source of truth and mirrors embeddings into a
[ChromaDB](https://www.trychroma.com/) index for indexed KNN search (scope
filtering is applied via Chroma metadata). When ChromaDB isn't installed it
transparently falls back to a brute-force cosine scan (a warning is logged). To
enable it:

```bash
pip install -e ".[vector]"   # installs chromadb
```

### Optional extras

| Extra | Install | Purpose |
|---|---|---|
| `vector` | `pip install -e ".[vector]"` | Indexed KNN search via ChromaDB (brute-force fallback otherwise). |
| `schema` | `pip install -e ".[schema]"` | JSON-schema validation of tool-call arguments. |
| `dev` | `pip install -e ".[dev]"` | Test toolchain (pytest, respx). |

## Tests

```bash
pytest
```

## License

MIT
