"""Tests for rickshaw event models."""

from __future__ import annotations

import pytest
from pydantic import TypeAdapter

from rickshaw import events
from rickshaw.providers.base import ToolCall, TokenUsage


def _stream_event_cases():
    return [
        events.TextDelta(text="hello world"),
        events.ThinkingDelta(text="thinking deeply"),
        events.ToolCallStart(id="tc1", name="recall"),
        events.ToolCallDelta(id="tc1", arguments_fragment='{"q'),
        events.ToolCallEnd(
            call=ToolCall(
                id="tc1",
                name="recall",
                arguments={"query": "prefs"},
                raw={"index": 0, "function": {"name": "recall"}},
            )
        ),
        events.StreamError(message="provider 429"),
        events.StreamDone(
            text="final answer",
            model="gpt-4o",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
            tool_calls=[
                ToolCall(id="tc2", name="remember", arguments={"fact": "x"}),
            ],
        ),
    ]


def _turn_event_cases():
    return [
        events.TurnStart(
            turn_id="t1",
            task_input="hello",
            timestamp="2026-07-15T00:00:00+00:00",
        ),
        events.ContextStart(),
        events.ContextDone(record_count=3, token_estimate=12),
        events.PromptBuilt(message_count=2, token_estimate=9),
        events.LLMCallStart(attempt=1, model="gpt-4o"),
        events.LLMCallDone(
            model="gpt-4o",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ),
        events.TurnToolCallStart(
            call_id="tc1",
            tool_name="recall",
            arguments={"query": "prefs"},
        ),
        events.TurnToolCallDone(
            call_id="tc1",
            tool_name="recall",
            result="found 2 records",
            duration_ms=120,
        ),
        events.Retry(
            attempt=1,
            max_retries=3,
            delay=1.5,
            error="429 Too Many Requests",
        ),
        events.Degraded(reason="falling back to local memory"),
        events.MemoryWrite(record_ids=["r1", "r2"]),
        events.JobEnqueue(job_type="score", payload={"record_id": "r1"}),
        events.TurnTextDelta(text="streaming text"),
        events.TurnThinkingDelta(text="reasoning token"),
        events.TurnDone(
            text="final",
            tool_calls_made=2,
            degraded=False,
            model="gpt-4o",
            usage=TokenUsage(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        ),
        events.Error(message="something went wrong"),
    ]


@pytest.mark.parametrize("event", _stream_event_cases(), ids=lambda e: type(e).__name__)
def test_stream_event_roundtrip_through_union(event: events.StreamEvent):
    """Every StreamEvent subclass serializes and deserializes via the union."""
    adapter = TypeAdapter(events.StreamEvent)
    data = adapter.dump_python(event)
    restored = adapter.validate_python(data)
    assert restored.type == event.type
    assert restored == event


@pytest.mark.parametrize("event", _turn_event_cases(), ids=lambda e: type(e).__name__)
def test_turn_event_roundtrip_through_union(event: events.TurnEvent):
    """Every TurnEvent subclass serializes and deserializes via the union."""
    adapter = TypeAdapter(events.TurnEvent)
    data = adapter.dump_python(event)
    restored = adapter.validate_python(data)
    assert restored.type == event.type
    assert restored == event


def test_tool_call_end_serializes_nested_tool_call():
    """ToolCallEnd preserves the nested ToolCall dataclass as a dict."""
    call = ToolCall(
        id="tc1",
        name="recall",
        arguments={"query": "prefs"},
        raw={
            "index": 0,
            "function": {
                "name": "recall",
                "arguments": '{"query":"prefs"}',
            },
        },
    )
    event = events.ToolCallEnd(call=call)
    data = TypeAdapter(events.StreamEvent).dump_python(event)
    assert data["type"] == "tool_call_end"
    assert data["call"] == {
        "id": "tc1",
        "name": "recall",
        "arguments": {"query": "prefs"},
        "raw": {
            "index": 0,
            "function": {
                "name": "recall",
                "arguments": '{"query":"prefs"}',
            },
        },
    }


def test_stream_done_serializes_nested_dataclasses():
    """StreamDone preserves TokenUsage and the tool_calls list as dicts."""
    usage = TokenUsage(prompt_tokens=5, completion_tokens=3, total_tokens=8)
    tool_call = ToolCall(
        id="tc1",
        name="remember",
        arguments={"fact": "important"},
        raw={"idx": 1},
    )
    event = events.StreamDone(
        text="done",
        model="gpt-4o",
        usage=usage,
        tool_calls=[tool_call],
    )
    data = TypeAdapter(events.StreamEvent).dump_python(event)
    assert data["type"] == "stream_done"
    assert data["usage"] == {
        "prompt_tokens": 5,
        "completion_tokens": 3,
        "total_tokens": 8,
    }
    assert data["tool_calls"] == [
        {"id": "tc1", "name": "remember", "arguments": {"fact": "important"}, "raw": {"idx": 1}}
    ]


@pytest.mark.parametrize("event", _turn_event_cases(), ids=lambda e: type(e).__name__)
def test_turn_event_json_roundtrip(event):
    """Each TurnEvent subclass round-trips through model_dump_json/model_validate_json."""
    json_str = event.model_dump_json()
    restored = type(event).model_validate_json(json_str)
    assert restored == event
