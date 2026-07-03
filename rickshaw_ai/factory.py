"""The public entry points: ``create_models`` and ``builtin_models``.

A :class:`Models` collection resolves ``"<provider>/<model>"`` ids to handles
that run generation. Only tool-calling models may be registered.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator, Awaitable, Callable

import httpx

from rickshaw_ai._builtins import default_providers
from rickshaw_ai.auth.oauth import OAuthClient
from rickshaw_ai.credentials.store import CredentialStore, InMemoryCredentialStore
from rickshaw_ai.credentials.types import Credential, OAuthCredential
from rickshaw_ai.errors import RickshawAIError
from rickshaw_ai.generate import GenerateRequest, GenerateResult
from rickshaw_ai.providers import ProviderRuntime, adapter_for
from rickshaw_ai.registry import ModelInfo, ProviderInfo, RetryPolicy
from rickshaw_ai.streaming import StreamEvent

if TYPE_CHECKING:
    from rickshaw_ai.session import Session
    from rickshaw_ai.tools import Tool


class ModelHandle:
    """A single model bound to its provider runtime."""

    def __init__(self, info: ModelInfo, runtime: ProviderRuntime) -> None:
        self.info = info
        self._runtime = runtime

    async def generate(self, req: GenerateRequest) -> GenerateResult:
        return await self._runtime.generate(req, self.info)

    def stream(self, req: GenerateRequest) -> AsyncIterator[StreamEvent]:
        return self._runtime.stream(req, self.info)


class ProviderHandle:
    def __init__(self, info: ProviderInfo, models: "Models") -> None:
        self.info = info
        self._models = models

    def models(self) -> list[ModelInfo]:
        return list(self.info.models)

    async def login(self, **opts: Any) -> None:
        await self._models.login(self.info.id, **opts)


class Models:
    """A collection of providers/models sharing credentials and transport."""

    def __init__(
        self,
        providers: list[ProviderInfo],
        *,
        credentials: CredentialStore,
        http_client: httpx.AsyncClient,
        retry: RetryPolicy,
    ) -> None:
        self.credentials = credentials
        self._http = http_client
        self._retry = retry
        self._providers: dict[str, ProviderInfo] = {}
        self._models: dict[str, ModelInfo] = {}
        self._runtimes: dict[str, ProviderRuntime] = {}
        for provider in providers:
            self._add_provider(provider)

    def _add_provider(self, provider: ProviderInfo) -> None:
        for model in provider.models:
            if not model.supports_tools:
                raise ValueError(
                    f"model {model.id!r} does not support tools; only tool-calling "
                    f"models may be registered"
                )
        self._providers[provider.id] = provider
        self._runtimes[provider.id] = ProviderRuntime(
            provider,
            adapter_for(provider.protocol),
            store=self.credentials,
            http=self._http,
            retry=self._retry,
        )
        for model in provider.models:
            self._models[model.id] = model

    # -- lookup ------------------------------------------------------------

    def list(self) -> list[ModelInfo]:
        return list(self._models.values())

    def get(self, model_id: str) -> ModelHandle:
        info = self._models.get(model_id)
        if info is None:
            raise RickshawAIError(
                f"unknown model {model_id!r}; known models: "
                f"{', '.join(sorted(self._models)) or '(none)'}"
            )
        return ModelHandle(info, self._runtimes[info.provider_id])

    def provider(self, provider_id: str) -> ProviderHandle:
        info = self._providers.get(provider_id)
        if info is None:
            raise RickshawAIError(f"unknown provider {provider_id!r}")
        return ProviderHandle(info, self)

    def provider_info(self, provider_id: str) -> ProviderInfo:
        return self._providers[provider_id]

    # -- sessions ----------------------------------------------------------

    def session(
        self,
        *,
        system: str | None = None,
        tools: "list[Tool] | None" = None,
    ) -> "Session":
        from rickshaw_ai.session import Session

        return Session(self, system=system, tools=tools or [])

    # -- oauth -------------------------------------------------------------

    async def login(
        self,
        provider_id: str,
        *,
        open_browser: Callable[[str], object] | None = None,
        prompt_code: Callable[[], Awaitable[str]] | None = None,
        show_user_code: Callable[[str, str], object] | None = None,
    ) -> None:
        """Run the provider's OAuth flow and persist the resulting credential."""
        provider = self._providers.get(provider_id)
        if provider is None or provider.oauth is None:
            raise RickshawAIError(f"provider {provider_id!r} has no OAuth support")
        client = OAuthClient(config=provider.oauth, http=self._http)

        if provider.oauth.mode == "device_code":
            if show_user_code is None:
                raise RickshawAIError("device-code login requires show_user_code")
            credential: Credential = await client.login_device_code(
                show_user_code=show_user_code
            )
        else:
            if open_browser is None or prompt_code is None:
                raise RickshawAIError(
                    "auth-code login requires open_browser and prompt_code"
                )
            credential = await client.login_auth_code(
                open_browser=open_browser, prompt_code=prompt_code
            )

        def _set(_: Credential | None) -> Credential:
            return credential

        await self.credentials.modify(provider_id, _set)

    async def aclose(self) -> None:
        await self._http.aclose()


def _new_http(http_client: httpx.AsyncClient | None) -> httpx.AsyncClient:
    return http_client or httpx.AsyncClient(timeout=httpx.Timeout(120.0))


def create_models(
    *,
    credentials: CredentialStore | None = None,
    http_client: httpx.AsyncClient | None = None,
    retry: RetryPolicy | None = None,
    providers: list[ProviderInfo] | None = None,
) -> Models:
    """Build a :class:`Models` collection from *providers* (or an empty set)."""
    return Models(
        list(providers or []),
        credentials=credentials or InMemoryCredentialStore(),
        http_client=_new_http(http_client),
        retry=retry or RetryPolicy(),
    )


def builtin_models(
    *,
    credentials: CredentialStore | None = None,
    http_client: httpx.AsyncClient | None = None,
    retry: RetryPolicy | None = None,
    providers: list[ProviderInfo] | None = None,
) -> Models:
    """Build a :class:`Models` collection from the curated built-in providers.

    Extra *providers* are appended to the built-in, tool-calling-only set.
    """
    all_providers = default_providers() + list(providers or [])
    return Models(
        all_providers,
        credentials=credentials or InMemoryCredentialStore(),
        http_client=_new_http(http_client),
        retry=retry or RetryPolicy(),
    )
