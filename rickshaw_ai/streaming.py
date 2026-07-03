"""Streaming event types.

An adapter's ``stream()`` yields a sequence of these events. The terminal
:class:`StreamDone` carries the same :class:`~rickshaw_ai.generate.GenerateResult`
that a non-streaming ``generate()`` would return, so callers can rely on
identical canonical output either way.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import BaseModel, Field

from rickshaw_ai.generate import GenerateResult
from rickshaw_ai.tools import ToolCall


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
    type: Literal["error"] = "error"
    message: str


class StreamDone(BaseModel):
    type: Literal["done"] = "done"
    result: GenerateResult


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
