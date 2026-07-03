"""Provider/model descriptors, OAuth config, and retry policy."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from rickshaw_ai.generate import Pricing

Protocol = Literal["anthropic", "openai", "openai_compatible", "google"]
AuthMethod = Literal["api_key", "oauth"]


class OAuthConfig(BaseModel):
    """OAuth endpoints and client settings for a provider."""

    authorize_url: str
    token_url: str
    client_id: str
    scopes: list[str] = Field(default_factory=list)
    use_pkce: bool = True
    mode: Literal["auth_code", "device_code"] = "auth_code"
    redirect_uri: str | None = None
    device_code_url: str | None = None
    #: Seconds before expiry at which a token is treated as stale and refreshed.
    refresh_leeway_seconds: int = 60


class ModelInfo(BaseModel):
    """Static description of one model.

    Only tool-calling models are registered: ``supports_tools`` MUST be True or
    factory construction rejects the model.
    """

    id: str  # "<provider_id>/<model>"
    provider_id: str
    model: str  # wire model name
    context_window: int = 0
    max_output_tokens: int = 4096
    supports_tools: bool = True
    supports_vision_input: bool = False
    supports_image_output: bool = False
    supports_reasoning: bool = False
    pricing: Pricing = Field(default_factory=Pricing)
    modalities: list[str] = Field(default_factory=lambda: ["text"])


class ProviderInfo(BaseModel):
    """Static description of one provider and the models it serves."""

    id: str
    base_url: str
    protocol: Protocol
    auth_methods: list[AuthMethod] = Field(default_factory=lambda: ["api_key"])
    #: Env vars checked as a fallback (in order) when nothing is stored.
    env_keys: list[str] = Field(default_factory=list)
    #: Header name for an API key. Bearer-style providers use Authorization.
    api_key_header: str = "Authorization"
    api_key_prefix: str = "Bearer "
    oauth: OAuthConfig | None = None
    models: list[ModelInfo] = Field(default_factory=list)


class RetryPolicy(BaseModel):
    """Exponential-backoff retry policy for retryable errors."""

    max_retries: int = 2
    initial_backoff: float = 0.5
    max_backoff: float = 8.0
    multiplier: float = 2.0
    jitter: float = 0.1

    def backoff_for(self, attempt: int) -> float:
        """Backoff (seconds) before retry *attempt* (1-based)."""
        delay = self.initial_backoff * (self.multiplier ** (attempt - 1))
        return min(delay, self.max_backoff)
