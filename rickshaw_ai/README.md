# rickshaw-ai

A unified, provider-agnostic LLM package for Python. One interface over many
providers, with automatic auth resolution, token & cost tracking, and simple
context persistence + hand-off to other models mid-session.

**Only tool-calling models are registered** — this library targets agentic
workflows, where function calling is the load-bearing capability.

Built by extracting and generalizing the Rickshaw harness's provider layer; the
harness's `rickshaw/providers/` is a thin sync facade over this package.

## Quick start

```python
from rickshaw_ai import builtin_models

models = builtin_models()                 # env-based auth by default
session = models.session(system="You are helpful.")

r1 = await session.run("Hi", model="anthropic/claude-sonnet-4-20250514")
r2 = await session.run("Say more", model="openai/gpt-4o")   # handoff, same history

print(session.usage.total.cost_usd)       # cost across the whole session
print(session.usage.per_model)            # per-model breakdown
```

`builtin_models()` and `create_models()` take the same options:

```python
models = create_models(
    credentials=my_store,        # a CredentialStore (default: in-memory)
    http_client=my_async_client, # inject httpx.AsyncClient for tests/proxies
    retry=RetryPolicy(...),      # retry policy for transient errors
    providers=[my_provider],     # extra providers (appended to built-ins)
)
```

## Tools

```python
from rickshaw_ai import tool

@tool
def get_weather(city: str, units: str = "c") -> str:
    """Get the weather for a city."""
    ...

session = models.session(tools=[get_weather])
```

Arguments the model returns are validated against the tool's JSON Schema
(`jsonschema` when installed, else a required-fields check). Streaming assembles
partial tool-call JSON into validated calls, and `stream()`'s final result
equals what `generate()` returns.

## Credentials

Stored credentials (interactively entered API keys, OAuth tokens) live in a
`CredentialStore` — one type-tagged credential per provider. The package ships
an in-memory default; apps inject persistent storage:

```python
from rickshaw_ai import builtin_models, FileCredentialStore

models = builtin_models(credentials=FileCredentialStore("~/.rickshaw/auth.json"))
```

The contract is small — `read`, `modify`, `delete`:

- `read(provider_id)` — return the stored credential, or `None`.
- `modify(provider_id, fn)` — **the only write path**: a serialized
  read-modify-write. OAuth refresh runs *inside* `modify`, so concurrent
  requests and processes cannot double-refresh a rotated token.
- `delete(provider_id)` — remove the credential.

**A stored credential owns its provider.** Environment variables are consulted
*only* when nothing is stored, and a failed OAuth refresh raises `AuthError` —
it never silently falls back to an env key.

API-key credentials use the same `type` discriminator as pi's `auth.json` and
can carry provider-scoped env/config (e.g. a Cloudflare AI Gateway):

```python
from rickshaw_ai import ApiKeyCredential

credential = ApiKeyCredential(
    key="...",
    env={"CLOUDFLARE_ACCOUNT_ID": "account-id", "CLOUDFLARE_GATEWAY_ID": "gateway-id"},
)
```

## OAuth

Providers that support subscription auth (Anthropic Claude Pro/Max, OpenAI
ChatGPT, GitHub Copilot) declare an `OAuthConfig`. Run the interactive flow and
the resulting token is persisted via `modify` and refreshed lazily:

```python
import webbrowser

await models.login(
    "anthropic",
    open_browser=lambda url: webbrowser.open(url),
    prompt_code=my_async_paste_prompt,     # capture the redirect / paste the code
)
# device-code providers pass show_user_code=... instead
```

## What's unified

| Concern         | How |
|-----------------|-----|
| Messages        | Canonical content blocks (text, image, tool_use, tool_result, thinking) |
| Reasoning       | One `Reasoning(effort | budget_tokens)`; provider escape hatch via `provider_options` |
| Stop reasons    | Canonical `StopReason` enum; raw kept in `metadata["raw_stop_reason"]` |
| Errors          | `AuthError`, `RateLimitError`, `OverloadedError`, `InvalidRequestError`, … with a `retryable` flag |
| Usage & cost    | Per-response `Usage` with `cost_usd`; session totals + per-model breakdown |
| Handoff         | `session.dump()` / `Session.load()`; signatures stripped, tool pairing preserved on switch |

## Providers

OAuth-first (Anthropic, OpenAI, Copilot), the OpenAI-compatible fleet (Groq,
xAI, Mistral, DeepSeek, Together, Fireworks), native Google Gemini, and gateways
(OpenRouter, Cloudflare AI Gateway). Add your own by passing `ProviderInfo`
objects to `create_models`/`builtin_models`.

Provider SDKs are **not** dependencies — adapters talk to the REST APIs directly
so translation, error mapping, and streaming stay under our control. The only
hard dependencies are `pydantic` and `httpx`.
```
