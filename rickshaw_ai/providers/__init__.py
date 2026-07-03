"""Provider adapters and the protocol → adapter mapping."""

from rickshaw_ai.providers.anthropic import AnthropicAdapter
from rickshaw_ai.providers.base import ProviderAdapter, ProviderRuntime
from rickshaw_ai.providers.google import GoogleAdapter
from rickshaw_ai.providers.openai_compatible import OpenAICompatibleAdapter

#: One shared adapter instance per protocol. The OpenAI-compatible adapter
#: serves native OpenAI, the OpenAI-protocol fleet, and gateways alike.
ADAPTERS: dict[str, ProviderAdapter] = {
    "openai": OpenAICompatibleAdapter(),
    "openai_compatible": OpenAICompatibleAdapter(),
    "anthropic": AnthropicAdapter(),
    "google": GoogleAdapter(),
}


def adapter_for(protocol: str) -> ProviderAdapter:
    try:
        return ADAPTERS[protocol]
    except KeyError as exc:
        raise ValueError(f"no adapter for protocol {protocol!r}") from exc


__all__ = [
    "ProviderAdapter",
    "ProviderRuntime",
    "AnthropicAdapter",
    "GoogleAdapter",
    "OpenAICompatibleAdapter",
    "ADAPTERS",
    "adapter_for",
]
