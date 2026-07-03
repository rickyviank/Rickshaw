"""Shared builders for rickshaw_ai adapter tests."""

from __future__ import annotations

from rickshaw_ai import (
    ApiKeyCredential,
    InMemoryCredentialStore,
    Models,
    Pricing,
    create_models,
)
from rickshaw_ai.registry import ModelInfo, ProviderInfo, RetryPolicy


def make_models(
    *,
    protocol: str,
    provider_id: str,
    base_url: str,
    model: str = "test-model",
    api_key_header: str = "Authorization",
    api_key_prefix: str = "Bearer ",
    reasoning: bool = False,
    vision: bool = False,
    pricing: Pricing | None = None,
    retry: RetryPolicy | None = None,
) -> Models:
    provider = ProviderInfo(
        id=provider_id,
        base_url=base_url,
        protocol=protocol,
        env_keys=[],
        api_key_header=api_key_header,
        api_key_prefix=api_key_prefix,
        models=[
            ModelInfo(
                id=f"{provider_id}/{model}",
                provider_id=provider_id,
                model=model,
                context_window=128_000,
                supports_tools=True,
                supports_reasoning=reasoning,
                supports_vision_input=vision,
                pricing=pricing or Pricing(input=1.0, output=2.0),
            )
        ],
    )
    store = InMemoryCredentialStore({provider_id: ApiKeyCredential(key="test-key")})
    return create_models(
        providers=[provider],
        credentials=store,
        retry=retry or RetryPolicy(max_retries=0),
    )
