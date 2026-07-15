"""Provider stream and orchestrator turn lifecycle events.

These Pydantic models are the public contract for:
* :meth:`rickshaw.providers.base.LLMProvider.stream_events` — yields
  :class:`StreamEvent` objects.
* :meth:`rickshaw.orchestrator.Orchestrator.run_turn` — calls ``on_event`` with
  :class:`TurnEvent` objects.

All events are JSON-serializable so the trace store can persist them.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from rickshaw.providers.base import ToolCall, TokenUsage


# ---------------------------------------------------------------------------
# Provider stream events
# ---------------------------------------------------------------------------


class TextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class ThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class ToolCallStart(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    id: str
    name: str


class ToolCallDelta(BaseModel):
    type: Literal["tool_call_delta"] = "tool_call_delta"
    id: str
    arguments_fragment: str


class ToolCallEnd(BaseModel):
    type: Literal["tool_call_end"] = "tool_call_end"
    call: ToolCall


class StreamError(BaseModel):
    type: Literal["stream_error"] = "stream_error"
    message: str


class StreamDone(BaseModel):
    type: Literal["stream_done"] = "stream_done"
    text: str
    model: str
    usage: TokenUsage = Field(default_factory=TokenUsage)
    tool_calls: list[ToolCall] = Field(default_factory=list)


StreamEvent = Annotated[
    Union[
        TextDelta,
        ThinkingDelta,
        ToolCallStart,
        ToolCallDelta,
        ToolCallEnd,
        StreamError,
        StreamDone,
    ],
    Field(discriminator="type"),
]


# ---------------------------------------------------------------------------
# Orchestrator turn lifecycle events
# ---------------------------------------------------------------------------


class TurnStart(BaseModel):
    type: Literal["turn_start"] = "turn_start"
    turn_id: str
    task_input: str
    timestamp: str = Field(default_factory=lambda: _now_iso())


class ContextStart(BaseModel):
    type: Literal["context_start"] = "context_start"


class ContextDone(BaseModel):
    type: Literal["context_done"] = "context_done"
    record_count: int
    token_estimate: int


class PromptBuilt(BaseModel):
    type: Literal["prompt_built"] = "prompt_built"
    message_count: int
    token_estimate: int


class LLMCallStart(BaseModel):
    type: Literal["llm_call_start"] = "llm_call_start"
    attempt: int
    model: str


class LLMCallDone(BaseModel):
    type: Literal["llm_call_done"] = "llm_call_done"
    model: str
    usage: TokenUsage | None = None


class TurnToolCallStart(BaseModel):
    type: Literal["tool_call_start"] = "tool_call_start"
    call_id: str
    tool_name: str
    arguments: dict


class TurnToolCallDone(BaseModel):
    type: Literal["tool_call_done"] = "tool_call_done"
    call_id: str
    tool_name: str
    result: str
    duration_ms: int


class Retry(BaseModel):
    type: Literal["retry"] = "retry"
    attempt: int
    max_retries: int
    delay: float
    error: str


class Degraded(BaseModel):
    type: Literal["degraded"] = "degraded"
    reason: str


class MemoryWrite(BaseModel):
    type: Literal["memory_write"] = "memory_write"
    record_ids: list[str]


class JobEnqueue(BaseModel):
    type: Literal["job_enqueue"] = "job_enqueue"
    job_type: str
    payload: dict


class TurnTextDelta(BaseModel):
    type: Literal["text_delta"] = "text_delta"
    text: str


class TurnThinkingDelta(BaseModel):
    type: Literal["thinking_delta"] = "thinking_delta"
    text: str


class TurnDone(BaseModel):
    type: Literal["turn_done"] = "turn_done"
    text: str
    tool_calls_made: int
    degraded: bool
    model: str
    usage: TokenUsage | None = None


class Error(BaseModel):
    type: Literal["error"] = "error"
    message: str


TurnEvent = Annotated[
    Union[
        TurnStart,
        ContextStart,
        ContextDone,
        PromptBuilt,
        LLMCallStart,
        LLMCallDone,
        TurnToolCallStart,
        TurnToolCallDone,
        Retry,
        Degraded,
        MemoryWrite,
        JobEnqueue,
        TurnTextDelta,
        TurnThinkingDelta,
        TurnDone,
        Error,
    ],
    Field(discriminator="type"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
