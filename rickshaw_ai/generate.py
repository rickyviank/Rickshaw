"""Request/response types for a single model turn.

These are provider-neutral: :class:`GenerateRequest` is what callers submit and
:class:`GenerateResult` is what every adapter returns after normalizing its
provider's wire response.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field

from rickshaw_ai.messages import Message
from rickshaw_ai.tools import Tool, ToolCall


class StopReason(str, Enum):
    """Canonical finish reason. The raw provider string is kept in metadata."""

    end_turn = "end_turn"
    max_output_tokens = "max_output_tokens"
    tool_use = "tool_use"
    stop_sequence = "stop_sequence"
    content_filter = "content_filter"
    refusal = "refusal"
    pause = "pause"
    error = "error"


class Reasoning(BaseModel):
    """Unified reasoning/thinking controls.

    ``effort`` is the OpenAI/Gemini-style knob; ``budget_tokens`` is the
    Anthropic-style knob. Adapters normalize between them where a provider only
    supports one. ``visible`` requests that thinking summaries/blocks be
    returned when the provider supports it.
    """

    effort: Literal["low", "medium", "high"] | None = None
    budget_tokens: int | None = None
    visible: bool = True


class Pricing(BaseModel):
    """Per-1M-token prices in USD used to compute :class:`Usage.cost_usd`."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float | None = None
    cache_write: float | None = None
    reasoning: float | None = None


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None

    def compute_cost(self, pricing: Pricing) -> float:
        """Return the USD cost of this usage under *pricing* (per 1M tokens)."""
        m = 1_000_000
        cost = self.input_tokens / m * pricing.input
        cost += self.output_tokens / m * pricing.output
        if pricing.cache_read is not None:
            cost += self.cache_read_tokens / m * pricing.cache_read
        if pricing.cache_write is not None:
            cost += self.cache_write_tokens / m * pricing.cache_write
        if pricing.reasoning is not None:
            cost += self.reasoning_tokens / m * pricing.reasoning
        return cost

    def __add__(self, other: "Usage") -> "Usage":
        cost: float | None
        if self.cost_usd is None and other.cost_usd is None:
            cost = None
        else:
            cost = (self.cost_usd or 0.0) + (other.cost_usd or 0.0)
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cost_usd=cost,
        )


ToolChoice = Literal["auto", "none", "required"]


class GenerateRequest(BaseModel):
    """A single generation request, submitted to a :class:`ModelHandle`."""

    model_config = {"arbitrary_types_allowed": True}

    messages: list[Message] = Field(default_factory=list)
    system: str | None = None
    tools: list[Tool] = Field(default_factory=list)
    tool_choice: ToolChoice | None = None
    reasoning: Reasoning | None = None
    max_output_tokens: int | None = None
    temperature: float | None = None
    stop_sequences: list[str] = Field(default_factory=list)
    #: Opaque, provider-specific settings merged into the wire body verbatim.
    provider_options: dict[str, Any] = Field(default_factory=dict)


class GenerateResult(BaseModel):
    """A normalized response from any provider."""

    message: Message
    stop_reason: StopReason = StopReason.end_turn
    usage: Usage = Field(default_factory=Usage)
    model_id: str = ""
    provider_id: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        return self.message.text

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [
            ToolCall(id=b.id, name=b.name, arguments=b.arguments)
            for b in self.message.tool_uses
        ]


class ResolvedAuth(BaseModel):
    """Concrete auth material for one request, produced by the resolver."""

    headers: dict[str, str] = Field(default_factory=dict)
    query: dict[str, str] = Field(default_factory=dict)
    #: Provider-scoped env/config carried by the credential (e.g. CLOUDFLARE_*).
    extra: dict[str, str] = Field(default_factory=dict)
