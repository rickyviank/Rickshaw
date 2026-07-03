"""Anthropic adapter: translation, thinking/reasoning, streaming, headers."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from rickshaw_ai import GenerateRequest, Message, Reasoning, StopReason, Tool
from rickshaw_ai.messages import ThinkingBlock, ToolUseBlock
from tests.rickshaw_ai.conftest import make_models

URL = "https://anthropic.test/v1/messages"

MESSAGES = {
    "id": "msg", "type": "message", "role": "assistant", "model": "test-model",
    "content": [{"type": "text", "text": "Hello from Claude"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 12, "output_tokens": 6},
}

TOOL_USE = {
    "id": "msg2", "type": "message", "role": "assistant", "model": "test-model",
    "content": [
        {"type": "thinking", "thinking": "let me think", "signature": "sig-abc"},
        {"type": "text", "text": "calling tool"},
        {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 20, "output_tokens": 10},
}


def _models(**kw):
    return make_models(
        protocol="anthropic", provider_id="anthropic", base_url="https://anthropic.test",
        api_key_header="x-api-key", api_key_prefix="", **kw,
    )


@respx.mock
async def test_generate_normalizes_and_maps_stop():
    respx.post(URL).mock(return_value=httpx.Response(200, json=MESSAGES))
    result = await _models().get("anthropic/test-model").generate(
        GenerateRequest(messages=[Message.user("hi")])
    )
    assert result.text == "Hello from Claude"
    assert result.stop_reason == StopReason.end_turn
    assert result.usage.input_tokens == 12
    assert result.usage.output_tokens == 6


@respx.mock
async def test_system_hoisted_and_string_content():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=MESSAGES))
    await _models().get("anthropic/test-model").generate(
        GenerateRequest(system="You are helpful.", messages=[Message.user("Hello")])
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["system"] == "You are helpful."
    assert sent["messages"] == [{"role": "user", "content": "Hello"}]
    assert sent["max_tokens"] > 0


@respx.mock
async def test_headers_use_x_api_key_and_version():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=MESSAGES))
    await _models().get("anthropic/test-model").generate(
        GenerateRequest(messages=[Message.user("hi")])
    )
    headers = route.calls[0].request.headers
    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers
    assert "anthropic-beta" not in headers  # api-key path, not oauth


@respx.mock
async def test_tool_use_parsed_and_choice_mapped():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=TOOL_USE))
    tool = Tool(name="get_weather", description="w",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}})
    result = await _models().get("anthropic/test-model").generate(
        GenerateRequest(messages=[Message.user("weather?")], tools=[tool], tool_choice="required")
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"][0] == {
        "name": "get_weather", "description": "w",
        "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
    }
    assert sent["tool_choice"] == {"type": "any"}

    assert result.stop_reason == StopReason.tool_use
    assert result.tool_calls[0].arguments == {"city": "Paris"}
    thinking = result.message.thinking
    assert thinking and thinking[0].signature == "sig-abc"
    assert thinking[0].provider == "anthropic"


@respx.mock
async def test_reasoning_budget_forwarded():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=MESSAGES))
    await _models(reasoning=True).get("anthropic/test-model").generate(
        GenerateRequest(messages=[Message.user("hi")], reasoning=Reasoning(effort="high"))
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["thinking"] == {"type": "enabled", "budget_tokens": 16384}


@respx.mock
async def test_signed_thinking_replayed_same_provider():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=MESSAGES))
    history = [
        Message.user("weather?"),
        Message(role="assistant", content=[
            ThinkingBlock(text="hmm", signature="sig-1", provider="anthropic"),
            ToolUseBlock(id="toolu_1", name="get_weather", arguments={"city": "Paris"}),
        ]),
    ]
    await _models(reasoning=True).get("anthropic/test-model").generate(
        GenerateRequest(messages=history)
    )
    sent = json.loads(route.calls[0].request.content)
    assistant = sent["messages"][1]["content"]
    assert assistant[0] == {"type": "thinking", "thinking": "hmm", "signature": "sig-1"}
    assert assistant[-1]["type"] == "tool_use"


def _sse(events):
    body = "".join(f"event: {e['type']}\ndata: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


@respx.mock
async def test_streaming_assembles_text_and_tool_use():
    events = [
        {"type": "message_start", "message": {"usage": {"input_tokens": 5}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "there"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1,
         "content_block": {"type": "tool_use", "id": "toolu_9", "name": "get_weather"}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": '{"city":'}},
        {"type": "content_block_delta", "index": 1,
         "delta": {"type": "input_json_delta", "partial_json": ' "Rome"}'}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 12}},
        {"type": "message_stop"},
    ]
    respx.post(URL).mock(return_value=_sse(events))
    collected = []
    async for ev in _models().get("anthropic/test-model").stream(
        GenerateRequest(messages=[Message.user("weather?")])
    ):
        collected.append(ev)
    done = collected[-1]
    assert done.type == "done"
    assert done.result.text == "Hi there"
    assert done.result.tool_calls[0].arguments == {"city": "Rome"}
    assert done.result.stop_reason == StopReason.tool_use
    assert done.result.usage.output_tokens == 12
