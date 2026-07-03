# Build Prompt: `rickshaw-ai` — a unified, provider-agnostic LLM package for Python

> Hand this document to an implementer (human or coding agent). It is a complete build
> specification written as a prompt. This is a Python port of the design patterns in
> `@earendil-works/pi-ai` (TypeScript), translated to Pythonic idioms.
>
> **Working directory:** `~/p_workspaces/Rickshaw` (the Rickshaw harness repo).
> **Distribution name:** `rickshaw-ai` · **import name:** `rickshaw_ai`.

---

## 0. Role & Goal

You are building **`rickshaw-ai`**, a small, dependency-light Python package that abstracts LLM
API providers behind one interface. It is the **extraction and generalization of the existing
`rickshaw/providers/` code** (currently `base.py` with `ToolCall`/`ToolSpec`, `factory.py`, and
the provider modules, plus `rickshaw/tool_registry.py`) into a standalone, importable library so
that the **Rickshaw harness** — and any other agentic app — can `import rickshaw_ai`, pick a
model, run tool-calling turns, track cost, persist a conversation, and hand that same
conversation off to a *different* model/provider mid-session.

The Rickshaw harness must then consume `rickshaw_ai` in place of its in-tree provider code (keep
a thin compatibility shim in `rickshaw/providers/` re-exporting from `rickshaw_ai` during the
transition).

The library exists to make this true:

> **Unified LLM API with provider collections, automatic auth resolution, token & cost
> tracking, and simple context persistence + hand-off to other models mid-session.**

### Hard constraints (non-negotiable)

1. **Tool-calling only.** The built-in model registry MUST include *only* models that support
   tool/function calling. A model with no tool-calling support is not registered and cannot be
   constructed via `builtin_models()`. This is the load-bearing capability for agentic use.
2. **Python 3.10+** (match the harness's `requires-python`), typed throughout (`py.typed`
   shipped). Public async API (`async def generate` / `async def stream`); a thin sync facade is
   optional, not required.
3. **Pydantic v2** for all wire/canonical models and tool schemas. **`httpx`** for transport
   (already a harness dependency). Keep the hard dependency set to `pydantic`, `httpx`, and
   stdlib. Provider SDKs (anthropic, openai, google-genai) are NOT dependencies — talk to the
   REST APIs directly so translation, error mapping, and streaming stay under our control.
   (JSON-schema tool validation may reuse the harness's optional `jsonschema` extra.)
4. **Provider-neutral canonical types are the source of truth.** Every provider is an adapter
   that translates canonical → wire and wire → canonical. Nothing provider-specific leaks into
   the canonical layer except through an explicit `provider_options` escape hatch and opaque
   `metadata`.
5. **A stored credential owns its provider.** Environment variables are consulted *only* when
   nothing is stored. A failed OAuth refresh raises — it never silently falls back to an env key.

### Non-goals (do not build)

Embeddings, reranking, fine-tuning, local/self-hosted model serving, batch API, audio/video
modalities, and non-tool-calling chat models. Image *input* is required; image *output* is
supported generically but not a focus.

---

## 1. Package layout

The package lives alongside the harness in `~/p_workspaces/Rickshaw`. Recommended: a `src/`
layout (`src/rickshaw_ai/`) published as the `rickshaw-ai` distribution, with the `rickshaw`
harness depending on it.

```
rickshaw_ai/
  __init__.py            # public exports: create_models, builtin_models, tool, Session, types, errors
  factory.py             # create_models(), builtin_models(), Models / ProviderHandle / ModelHandle
  registry.py            # ModelInfo, ProviderInfo, capability flags, pricing table
  credentials/
    store.py             # CredentialStore Protocol, InMemoryCredentialStore
    types.py             # ApiKeyCredential, OAuthCredential (discriminated by `type`)
  auth/
    resolver.py          # auth resolution order + refresh-inside-modify
    oauth.py             # OAuth (auth-code + PKCE, device flow), interactive login helper
  messages.py            # canonical Message + ContentBlock union (text/image/tool/thinking)
  tools.py               # Tool, @tool decorator, ToolCall, validation, streaming assembly
  generate.py            # GenerateRequest / GenerateResult / Usage / StopReason / Reasoning
  streaming.py           # stream event union + provider stream parsers
  errors.py              # error taxonomy + retry classification
  session.py             # Session: canonical history, run(), handoff, usage, dump/load
  providers/
    base.py              # Provider adapter Protocol (translate / parse / parse_stream / map_error)
    openai_compatible.py # shared adapter for the OpenAI-protocol fleet + gateways
    anthropic.py
    openai.py            # native OpenAI (Responses API + reasoning)
    google.py            # Gemini
    _builtins.py         # the shipped ProviderInfo/ModelInfo definitions (tool-calling only)
  py.typed
tests/
  ...
```

**Migration note:** carry over the existing `ToolCall` / `ToolSpec` semantics from
`rickshaw/providers/base.py` and the argument-validation logic from `rickshaw/tool_registry.py`
into `rickshaw_ai/tools.py`, generalized per §6. Existing provider modules
(`anthropic_provider.py`, `openai_provider.py`, `devin_provider.py`) become the seed adapters.

---

## 2. Provider factory & collections

Model ids are namespaced: **`"<provider_id>/<model_id>"`** (e.g. `"anthropic/claude-opus-4"`).

```python
from rickshaw_ai import create_models, builtin_models, CredentialStore

# builtin_models: ships the curated, tool-calling-only provider collection.
models = builtin_models(credentials=my_store)

# create_models: same options, but you supply / extend the provider collection.
models = create_models(credentials=my_store, providers=[my_custom_provider])
```

Both factories accept the same options:

```python
def builtin_models(
    *,
    credentials: CredentialStore | None = None,   # default: InMemoryCredentialStore()
    http_client: httpx.AsyncClient | None = None, # injectable for testing / proxies
    retry: RetryPolicy | None = None,
    providers: list[ProviderInfo] | None = None,  # extra providers appended to built-ins
) -> "Models": ...
```

`Models` is the collection handle:

```python
class Models:
    def list(self) -> list[ModelInfo]: ...                 # all tool-calling models
    def get(self, model_id: str) -> "ModelHandle": ...     # "provider/model"; raises if unknown
    def provider(self, provider_id: str) -> "ProviderHandle": ...
    def session(self, *, system: str | None = None,
                tools: list[Tool] | None = None) -> "Session": ...
    async def login(self, provider_id: str, **opts) -> None:   # OAuth interactive flow (§11)
        ...

class ModelHandle:
    info: ModelInfo
    async def generate(self, req: GenerateRequest) -> GenerateResult: ...
    def stream(self, req: GenerateRequest) -> AsyncIterator[StreamEvent]: ...
```

**Registry / capabilities.** Each model is described by a `ModelInfo` and every model in the
built-in registry has `supports_tools == True`. Factory construction MUST reject a `ModelInfo`
with `supports_tools=False`.

```python
class ModelInfo(BaseModel):
    id: str                    # "anthropic/claude-opus-4"
    provider_id: str
    model: str                 # wire model name
    context_window: int
    max_output_tokens: int
    supports_tools: bool       # MUST be True to be registered
    supports_vision_input: bool
    supports_image_output: bool
    supports_reasoning: bool
    pricing: Pricing           # per-token, incl. cache + reasoning tiers (§10)
    modalities: list[str]      # ["text","image"]

class ProviderInfo(BaseModel):
    id: str                    # "anthropic"
    base_url: str
    protocol: Literal["anthropic","openai","openai_compatible","google"]
    auth_methods: list[Literal["api_key","oauth"]]
    env_keys: list[str]        # env vars checked as fallback, in order (e.g. ["ANTHROPIC_API_KEY"])
    oauth: OAuthConfig | None
    models: list[ModelInfo]
```

A **Provider adapter** (in `providers/`) implements the translation contract:

```python
class ProviderAdapter(Protocol):
    def build_request(self, req: GenerateRequest, auth: ResolvedAuth) -> httpx.Request: ...
    def parse_response(self, raw: httpx.Response) -> GenerateResult: ...
    def parse_stream(self, raw: httpx.Response) -> AsyncIterator[StreamEvent]: ...
    def map_error(self, raw: httpx.Response | Exception) -> RickshawAIError: ...
```

The `openai_compatible` adapter is written once and reused for the whole OpenAI-protocol fleet
and the gateways (§14), parameterized by `base_url` + provider-scoped config.

---

## 3. Auth resolution

Resolution runs **per request** (so a refresh can happen just-in-time) and follows a strict
order:

1. **Stored credential** for `provider_id` (via `CredentialStore.read`). If present, it *owns*
   the provider.
   - `api_key` → use its `key` and inject its `env`/`config` (e.g. Cloudflare account/gateway ids).
   - `oauth` → if `access` is valid, use it. If expired, **refresh inside `store.modify`** (§4).
     If refresh fails → raise `AuthError`. **Do not fall back to env.**
2. **Environment variables** (`ProviderInfo.env_keys`, in order — e.g. `OPENAI_API_KEY`,
   `ANTHROPIC_API_KEY`, matching the harness's `.env.example`) — consulted *only* when step 1
   produced nothing at all (no stored credential).
3. Otherwise → raise `AuthError` naming the provider and how to authenticate (which env key /
   `models.login(...)`).

```python
class ResolvedAuth(BaseModel):
    headers: dict[str, str]            # e.g. {"Authorization": "Bearer ..."} or {"x-api-key": ...}
    query: dict[str, str] = {}
    extra_env: dict[str, str] = {}     # provider-scoped env from the credential (e.g. CLOUDFLARE_*)
```

---

## 4. Credential store

The contract is deliberately tiny. **`modify` is the only write path** — a serialized
read-modify-write — so concurrent requests and concurrent processes cannot double-refresh a
rotated OAuth token.

```python
class CredentialStore(Protocol):
    async def read(self, provider_id: str) -> Credential | None: ...

    async def modify(
        self,
        provider_id: str,
        fn: Callable[[Credential | None], Awaitable[Credential | None] | Credential | None],
    ) -> Credential | None:
        """Serialized read-modify-write. Acquire the provider lock, read current value,
        call fn(current), persist the result (None => delete), release. OAuth refresh runs
        INSIDE fn so only one caller refreshes a rotated token."""

    async def delete(self, provider_id: str) -> None: ...
```

- Ships **`InMemoryCredentialStore`** (default, backed by a dict + `asyncio.Lock` per provider).
- Apps inject a persistent store (file-backed, keyring, DB). A file-backed reference impl in
  docs must use a **cross-process lock** (e.g. `fcntl`/lockfile) around `modify` so multiple
  processes serialize, mirroring pi's `auth.json` semantics.
- One **type-tagged** credential per provider, discriminated by `type` (same discriminator as
  pi's `auth.json`), so old files interoperate:

```python
class ApiKeyCredential(BaseModel):
    type: Literal["api_key"] = "api_key"
    key: str
    env: dict[str, str] = {}      # provider-scoped env injected at request time
    config: dict[str, Any] = {}   # provider-scoped config (e.g. gateway routing)

class OAuthCredential(BaseModel):
    type: Literal["oauth"] = "oauth"
    access: str
    refresh: str | None = None
    expires: int | None = None    # epoch millis
    env: dict[str, str] = {}

Credential = Annotated[ApiKeyCredential | OAuthCredential, Field(discriminator="type")]
```

Example (api-key credential carrying provider-scoped env, e.g. a Cloudflare AI Gateway):

```python
credential = ApiKeyCredential(
    key="...",
    env={"CLOUDFLARE_ACCOUNT_ID": "account-id", "CLOUDFLARE_GATEWAY_ID": "gateway-id"},
)
```

---

## 5. Canonical messages & content blocks

Conversations are stored as provider-neutral messages. This canonical form is what gets
persisted and what enables cross-provider handoff (§9).

```python
class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str

class ImageBlock(BaseModel):                 # input AND model-produced output
    type: Literal["image"] = "image"
    media_type: str                          # "image/png", ...
    source: Literal["base64","url"]
    data: str                                # base64 payload or URL
    origin: Literal["input","output"] = "input"

class ToolUseBlock(BaseModel):               # assistant asks to call a tool
    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    arguments: dict[str, Any]

class ToolResultBlock(BaseModel):            # result handed back to the model
    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: list["ContentBlock"]            # text and/or image
    is_error: bool = False

class ThinkingBlock(BaseModel):              # unified reasoning (§6)
    type: Literal["thinking"] = "thinking"
    text: str
    signature: str | None = None            # provider-specific; preserved for same-provider turns
    redacted: bool = False
    provider: str | None = None             # which provider produced it (drives handoff handling)

ContentBlock = Annotated[
    TextBlock | ImageBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock,
    Field(discriminator="type"),
]

class Message(BaseModel):
    role: Literal["system","user","assistant","tool"]
    content: list[ContentBlock]
```

---

## 6. Tools: define, validate, handle, stream

**Definition.** Two ways, both producing the same `Tool` (generalizing the existing `ToolSpec`):

```python
from rickshaw_ai import tool, Tool

@tool
async def get_weather(city: str, units: str = "c") -> str:
    """Get current weather for a city."""      # docstring => description
    ...                                          # signature + type hints => JSON Schema

# or explicit, schema from a Pydantic model:
Tool(
    name="get_weather",
    description="...",
    parameters=WeatherArgs,        # Pydantic model OR raw JSON Schema dict
    handler=get_weather,           # optional; harness may execute tools itself
)
```

- The `@tool` decorator derives a JSON Schema from the signature/type hints (Pydantic
  `TypeAdapter`), using the docstring as the description.
- Each provider adapter translates `Tool` → its wire tool format (Anthropic `tools`,
  OpenAI `tools[type=function]`, Google `functionDeclarations`).

**Handling.** Tool calls appear in output as `ToolUseBlock` (and as `ToolCall` in results):

```python
class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]

def validate_arguments(tool: Tool, call: ToolCall) -> dict[str, Any]:
    """Validate call.arguments against tool.parameters. Raise ToolInputError on mismatch.
    (Port the jsonschema-with-required-fields-fallback logic from rickshaw/tool_registry.py.)"""
```

The harness owns execution, but the package provides `tool.handler` invocation helpers (sync +
async, as the existing registry already supports) and turns handler exceptions into
`ToolResultBlock(is_error=True)`.

**Streaming tools.** Providers stream tool arguments as partial JSON deltas. The package MUST:

- Emit `ToolCallStart(id, name)`, then `ToolCallDelta(id, arguments_json_fragment)` events as
  fragments arrive, accumulating per-tool-call buffers keyed by call id/index.
- On stream completion, parse each accumulated buffer as JSON, **validate against the tool
  schema**, and emit `ToolCallEnd(ToolCall)`. Malformed/again-partial JSON → `ToolInputError`
  surfaced as a stream error event, not a crash.
- Assembled tool calls also land in the final `GenerateResult` so `stream()` and `generate()`
  produce identical canonical output.

---

## 7. Image handling

- **Input:** `ImageBlock(origin="input")` in user/tool messages. Adapters convert base64/URL to
  each provider's format (Anthropic `image` source base64/url; OpenAI `image_url`; Gemini
  `inline_data`). Enforce `ModelInfo.supports_vision_input` — reject with `InvalidRequestError`
  if the model can't see images.
- **Output:** models that emit images produce `ImageBlock(origin="output")` in the assistant
  message. Guard with `supports_image_output`. Keep output images as canonical blocks so they
  persist and survive handoff (as opaque data, not re-interpreted by the next provider).

---

## 8. Unifying thinking / reasoning

One request-side knob and one canonical output block, with a per-provider escape hatch. This
generalizes the harness's existing **effort levels** (low/medium/high, per `.env.example`).

```python
class Reasoning(BaseModel):
    effort: Literal["low","medium","high"] | None = None   # OpenAI/Gemini style (harness effort)
    budget_tokens: int | None = None                        # Anthropic style
    visible: bool = True                                     # request thinking summaries/blocks
```

- Adapters normalize between `effort` and `budget_tokens` when a provider only supports one
  (document the mapping table used, e.g. low/med/high → token budgets for Anthropic). Preserve
  the harness's existing effort semantics as the canonical front door.
- Provider-specific settings that don't generalize go through
  `GenerateRequest.provider_options` (opaque dict merged into the wire body).
- Output reasoning becomes `ThinkingBlock`. **Anthropic signatures** are stored on the block and
  replayed verbatim on subsequent *same-provider* turns (required by the API). On handoff to a
  different provider, thinking signatures are dropped/converted (§9).
- Reasoning token counts flow into `Usage.reasoning_tokens` and cost (§10).

---

## 9. Stop reasons (canonical)

Map every provider's finish reason to one enum; keep the raw string in metadata.

```python
class StopReason(str, Enum):
    end_turn = "end_turn"                 # natural stop (Anthropic end_turn / OpenAI stop)
    max_output_tokens = "max_output_tokens"  # length / max_tokens
    tool_use = "tool_use"                 # model wants tools (tool_calls / tool_use)
    stop_sequence = "stop_sequence"
    content_filter = "content_filter"
    refusal = "refusal"
    pause = "pause"                       # provider pause/continue turns
    error = "error"
```

`GenerateResult.stop_reason: StopReason` plus `GenerateResult.metadata["raw_stop_reason"]`.

---

## 10. Token & cost tracking

```python
class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None        # computed from ModelInfo.pricing

class Pricing(BaseModel):                 # USD per 1M tokens
    input: float
    output: float
    cache_read: float | None = None
    cache_write: float | None = None
    reasoning: float | None = None
```

- Every `GenerateResult` carries a `Usage` with `cost_usd` computed from the model's pricing.
- The **`Session`** aggregates usage across turns *and across providers*, exposing a total and a
  per-model breakdown so a handoff conversation shows where cost was spent.

---

## 11. OAuth (subscription auth instead of static keys) — **priority provider set**

Providers declare OAuth capability; the package runs the flow and stores an `OAuthCredential`.

```python
class OAuthConfig(BaseModel):
    authorize_url: str
    token_url: str
    client_id: str
    scopes: list[str]
    use_pkce: bool = True
    mode: Literal["auth_code","device_code"] = "auth_code"
    redirect_uri: str | None = None
```

Interactive login:

```python
await models.login(
    "anthropic",
    open_browser=lambda url: webbrowser.open(url),   # app-supplied
    prompt_code=async_paste_code_callback,           # app-supplied (for paste/redirect capture)
)
```

- Implement **authorization-code + PKCE** and **device-code** flows. The app injects the UX
  (open a browser, capture the redirect, or prompt the user to paste a code) — the package owns
  the protocol, token exchange, and persistence via `store.modify`.
- **Refresh** happens lazily at request time, inside `store.modify` (§3/§4): if `access` is
  expired and a `refresh` token exists, exchange it and persist atomically. On failure raise
  `AuthError` — never fall back to env.
- Target OAuth-first providers first: **Anthropic Claude Pro/Max OAuth, OpenAI ChatGPT OAuth,
  GitHub Copilot.** Support static API keys for the same providers as an alternative credential.

---

## 12. Cross-provider handoff (switch model mid-conversation)

The `Session` holds canonical history and lets each turn pick any model. Handoff means
re-serializing the *same* canonical history to a different provider's wire format.

```python
class Session:
    system: str | None
    tools: list[Tool]
    messages: list[Message]
    usage: SessionUsage                     # total + per-model breakdown (§10)

    async def run(self, user_input: str | list[ContentBlock], *,
                  model: str, **overrides) -> GenerateResult: ...
    def stream(self, user_input, *, model: str, **overrides) -> AsyncIterator[StreamEvent]: ...

    def dump(self) -> dict: ...             # JSON-serializable canonical snapshot
    @classmethod
    def load(cls, data: dict, models: Models) -> "Session": ...
```

Handoff rules the adapters MUST enforce when translating canonical → target wire:

- **Reasoning signatures are provider-scoped.** When the target provider differs from the one
  that produced a `ThinkingBlock`, strip the `signature` (and drop `redacted` blocks); optionally
  demote thinking to plain context if the target can't accept it. Same-provider continuation
  replays signatures verbatim.
- **Tool-call ids** get normalized/regenerated to the target's id format while preserving the
  `tool_use` ↔ `tool_result` pairing.
- **System prompt** placement differs (top-level field vs. system message) — adapters place it
  correctly per provider.
- **Images / unsupported modalities:** if the target model lacks a capability the history
  requires (e.g. vision input), raise a clear `InvalidRequestError` naming the offending block
  rather than silently dropping content.
- Persist/restore is lossless for canonical data: `Session.load(session.dump(), models)` yields
  an equivalent session that can continue on any model. (This is the persistence primitive the
  harness's memory layer builds on.)

---

## 13. Unified error handling

```python
class RickshawAIError(Exception):
    provider_id: str | None
    model_id: str | None
    status_code: int | None
    request_id: str | None
    retryable: bool
    raw: Any

class AuthError(RickshawAIError): ...          # missing/invalid creds, failed refresh (never env fallback)
class RateLimitError(RickshawAIError):         # 429
    retry_after: float | None
class OverloadedError(RickshawAIError): ...    # 529 / 503 overloaded  (retryable)
class InvalidRequestError(RickshawAIError): ...# 400 / 422 (not retryable)
class NotFoundError(RickshawAIError): ...      # 404 model/route
class ContextLengthExceededError(InvalidRequestError): ...
class ContentFilterError(RickshawAIError): ...
class TimeoutError(RickshawAIError): ...       # retryable
class ConnectionError(RickshawAIError): ...    # retryable
class ProviderError(RickshawAIError): ...      # 5xx / unclassified
```

- Each adapter's `map_error` classifies provider status codes into this taxonomy and sets
  `retryable`, `retry_after`, `request_id`.
- A shared `RetryPolicy` (exponential backoff + jitter, honoring `retry_after`) retries only
  `retryable` errors up to a configurable cap. Auth and invalid-request errors are never retried.

---

## 14. Provider roadmap (build in this order)

1. **OAuth-first providers** — Anthropic (Claude Pro/Max OAuth), OpenAI (ChatGPT OAuth), GitHub
   Copilot. Establishes OAuth + refresh-inside-modify + the credential model end-to-end.
2. **OpenAI-compatible fleet** — Groq, xAI (Grok), Mistral, DeepSeek, Together, Fireworks via the
   single `openai_compatible` adapter (tool-calling models only).
3. **Native Anthropic / OpenAI / Google** — first-party APIs (Anthropic Messages, OpenAI
   Responses with reasoning, Gemini) with full vision + reasoning fidelity. (Seed from the
   existing `rickshaw/providers/anthropic_provider.py` and `openai_provider.py`.)
4. **Gateways / aggregators** — OpenRouter and Cloudflare AI Gateway, reusing the
   OpenAI-compatible adapter and the credential's provider-scoped `env`/`config`
   (`CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_GATEWAY_ID`, routing prefs).

For each provider, register only its tool-calling models with correct capability flags and
pricing.

---

## 15. Acceptance criteria (tests to write)

Use the harness's existing test stack (`pytest`, `pytest-asyncio`, `respx` for HTTP mocking).

- **Registry:** every built-in model has `supports_tools=True`; registering a non-tool model
  raises.
- **Auth order:** stored credential beats env; with no stored credential, env is used; a stored
  OAuth cred whose refresh fails raises `AuthError` and does **not** use env.
- **Concurrency:** two concurrent requests hitting an expired OAuth token trigger exactly **one**
  refresh (assert `modify` serialization / single token exchange), including a cross-process
  file-lock test for the file-backed store.
- **Tool translation round-trip:** `Tool` → each provider wire format → parsed `ToolCall`
  preserves name + arguments; argument validation rejects bad payloads with `ToolInputError`.
- **Streaming tools:** fragmented JSON deltas assemble into a valid, validated `ToolCall`; the
  streamed final result equals the non-streamed `generate()` result.
- **Reasoning:** `effort`↔`budget_tokens` normalization per provider; Anthropic signatures
  replayed on same-provider turns and stripped on handoff.
- **Handoff:** a tool-calling turn on provider A continues correctly on provider B;
  `Session.load(Session.dump())` round-trips; capability mismatch raises a clear error.
- **Stop reasons & errors:** provider finish reasons and status codes map to the canonical
  enums/exceptions; retry policy retries only `retryable` errors and honors `retry_after`.
- **Cost:** `Usage.cost_usd` matches pricing math; `Session` aggregates totals and per-model
  breakdown across a handoff.
- **Images:** base64 and URL inputs translate per provider; vision-unsupported model raises;
  output images persist and survive handoff.

---

## 16. Definition of done

`import rickshaw_ai` gives the Rickshaw harness: `builtin_models(credentials=...)`, a
tool-calling-only model registry, `@tool`, `session.run(..., model=...)` with mid-session
handoff, aggregated token/cost, `session.dump()/load()`, OAuth login + auto-refresh, static-key
fallback, unified stop reasons and errors — all provider-neutral, with providers added as thin
adapters. The harness's `rickshaw/providers/` is reduced to a compatibility shim re-exporting
from `rickshaw_ai`. Tests in §15 pass. `README` documents the `CredentialStore` contract
(`read` / `modify` / `delete`) and the "stored credential owns the provider; env is fallback
only" rule.
```
