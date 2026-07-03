"""Provider adapter contract and the shared request runtime.

An adapter is a thin translator: canonical request → wire body, and wire
response → canonical result. All transport concerns (auth resolution, retries,
error mapping, streaming) live in :class:`ProviderRuntime` so every provider
gets them for free.
"""

from __future__ import annotations

import asyncio
import random
from abc import ABC, abstractmethod
from typing import Any, AsyncIterator

import httpx

from rickshaw_ai.auth.resolver import resolve_auth
from rickshaw_ai.credentials.store import CredentialStore
from rickshaw_ai.errors import (
    ConnectionError as RAIConnectionError,
    RickshawAIError,
    TimeoutError as RAITimeoutError,
    classify_status,
)
from rickshaw_ai.generate import GenerateRequest, GenerateResult, ResolvedAuth
from rickshaw_ai.registry import ModelInfo, ProviderInfo, RetryPolicy
from rickshaw_ai.streaming import StreamDone, StreamEvent


class ProviderAdapter(ABC):
    """Translates between canonical types and one provider's wire format."""

    protocol: str = ""

    @abstractmethod
    def endpoint(self, provider: ProviderInfo, model: ModelInfo, *, stream: bool) -> str:
        """Return the full URL for a (streaming or not) completion request."""

    @abstractmethod
    def build_body(
        self, req: GenerateRequest, model: ModelInfo, *, stream: bool
    ) -> dict[str, Any]:
        """Translate a canonical request into the wire request body."""

    def extra_headers(self, provider: ProviderInfo, auth: ResolvedAuth) -> dict[str, str]:
        """Provider-specific static headers (e.g. ``anthropic-version``)."""
        return {}

    @abstractmethod
    def parse_response(
        self, data: dict[str, Any], model: ModelInfo, provider: ProviderInfo
    ) -> GenerateResult:
        """Translate a wire response body into a canonical result."""

    @abstractmethod
    def parse_stream(
        self, response: httpx.Response, model: ModelInfo, provider: ProviderInfo
    ) -> AsyncIterator[StreamEvent]:
        """Yield canonical stream events from a streaming HTTP response."""

    def map_error(
        self, response: httpx.Response, provider: ProviderInfo, model: ModelInfo
    ) -> RickshawAIError:
        """Map a non-2xx response onto the unified error taxonomy."""
        cls = classify_status(response.status_code)
        message = _error_message(response)
        retry_after = _retry_after(response)
        return cls(
            message,
            provider_id=provider.id,
            model_id=model.id,
            status_code=response.status_code,
            request_id=_request_id(response),
            retry_after=retry_after,
            raw=_safe_json(response),
        )


class ProviderRuntime:
    """Executes requests for one provider: auth, retries, errors, streaming."""

    def __init__(
        self,
        provider: ProviderInfo,
        adapter: ProviderAdapter,
        *,
        store: CredentialStore,
        http: httpx.AsyncClient,
        retry: RetryPolicy,
    ) -> None:
        self.provider = provider
        self.adapter = adapter
        self.store = store
        self.http = http
        self.retry = retry

    async def _auth(self) -> ResolvedAuth:
        return await resolve_auth(self.provider, self.store, self.http)

    def _headers(self, auth: ResolvedAuth) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        headers.update(auth.headers)
        headers.update(self.adapter.extra_headers(self.provider, auth))
        return headers

    async def generate(self, req: GenerateRequest, model: ModelInfo) -> GenerateResult:
        raw_url = self.adapter.endpoint(self.provider, model, stream=False)
        body = self.adapter.build_body(req, model, stream=False)

        last_exc: RickshawAIError | None = None
        for attempt in range(self.retry.max_retries + 1):
            auth = await self._auth()
            url = _expand_url(raw_url, auth.extra)
            try:
                resp = await self.http.post(
                    url, headers=self._headers(auth), json=body
                )
            except httpx.TimeoutException as exc:
                last_exc = RAITimeoutError(
                    str(exc), provider_id=self.provider.id, model_id=model.id
                )
            except httpx.HTTPError as exc:
                last_exc = RAIConnectionError(
                    str(exc), provider_id=self.provider.id, model_id=model.id
                )
            else:
                if resp.status_code < 400:
                    return self.adapter.parse_response(resp.json(), model, self.provider)
                last_exc = self.adapter.map_error(resp, self.provider, model)

            if not last_exc.retryable or attempt >= self.retry.max_retries:
                raise last_exc
            await self._sleep(attempt + 1, last_exc)

        assert last_exc is not None  # pragma: no cover
        raise last_exc

    async def stream(
        self, req: GenerateRequest, model: ModelInfo
    ) -> AsyncIterator[StreamEvent]:
        raw_url = self.adapter.endpoint(self.provider, model, stream=True)
        body = self.adapter.build_body(req, model, stream=True)
        auth = await self._auth()
        url = _expand_url(raw_url, auth.extra)
        headers = self._headers(auth)

        async with self.http.stream("POST", url, headers=headers, json=body) as resp:
            if resp.status_code >= 400:
                await resp.aread()
                raise self.adapter.map_error(resp, self.provider, model)
            async for event in self.adapter.parse_stream(resp, model, self.provider):
                yield event

    async def _sleep(self, attempt: int, exc: RickshawAIError) -> None:
        if exc.retry_after is not None:
            delay = exc.retry_after
        else:
            base = self.retry.backoff_for(attempt)
            delay = base + random.uniform(0, self.retry.jitter)
        await asyncio.sleep(delay)


# ---------------------------------------------------------------------------
# helpers used by the default error mapper
# ---------------------------------------------------------------------------


def _expand_url(url: str, values: dict[str, str]) -> str:
    """Substitute ``{VAR}`` placeholders in *url* from provider-scoped env.

    Used by gateways (e.g. Cloudflare) whose base URL embeds account/gateway ids
    carried on the credential's ``env``. Leaves unknown placeholders untouched.
    """
    if "{" not in url:
        return url
    for key, value in values.items():
        url = url.replace("{" + key + "}", value)
    return url


def _safe_json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text


def _error_message(response: httpx.Response) -> str:
    data = _safe_json(response)
    if isinstance(data, dict):
        err = data.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or data)
        if isinstance(err, str):
            return err
        if "message" in data:
            return str(data["message"])
    return f"request failed with status {response.status_code}"


def _request_id(response: httpx.Response) -> str | None:
    for h in ("x-request-id", "request-id", "cf-ray", "anthropic-request-id"):
        if h in response.headers:
            return response.headers[h]
    return None


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


async def aiter_sse(response: httpx.Response) -> AsyncIterator[str]:
    """Yield ``data:`` payloads from a Server-Sent-Events response."""
    async for line in response.aiter_lines():
        if not line or line.startswith(":"):
            continue
        if line.startswith("data:"):
            yield line[len("data:"):].strip()


__all__ = [
    "ProviderAdapter",
    "ProviderRuntime",
    "aiter_sse",
    "StreamDone",
]
