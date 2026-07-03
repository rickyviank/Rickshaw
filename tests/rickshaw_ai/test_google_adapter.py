"""Google Gemini adapter: translation, function calls, headers."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from rickshaw_ai import GenerateRequest, Message, StopReason, Tool
from tests.rickshaw_ai.conftest import make_models

URL = "https://gemini.test/v1beta/models/test-model:generateContent"

GEN = {
    "candidates": [{"content": {"role": "model", "parts": [{"text": "Hi from Gemini"}]},
                    "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 8, "candidatesTokenCount": 4},
}
FUNC = {
    "candidates": [{"content": {"role": "model", "parts": [
        {"functionCall": {"name": "get_weather", "args": {"city": "Kyoto"}}}]},
        "finishReason": "STOP"}],
    "usageMetadata": {"promptTokenCount": 12, "candidatesTokenCount": 6},
}


def _models():
    return make_models(protocol="google", provider_id="google", base_url="https://gemini.test",
                       api_key_header="x-goog-api-key", api_key_prefix="")


@respx.mock
async def test_generate_and_headers():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=GEN))
    result = await _models().get("google/test-model").generate(
        GenerateRequest(system="be brief", messages=[Message.user("hi")])
    )
    assert result.text == "Hi from Gemini"
    assert result.usage.input_tokens == 8
    assert route.calls[0].request.headers["x-goog-api-key"] == "test-key"
    sent = json.loads(route.calls[0].request.content)
    assert sent["systemInstruction"]["parts"][0]["text"] == "be brief"
    assert sent["contents"][0]["role"] == "user"


@respx.mock
async def test_function_call_parsed_and_tools_forwarded():
    route = respx.post(URL).mock(return_value=httpx.Response(200, json=FUNC))
    tool = Tool(name="get_weather", description="w",
                parameters={"type": "object", "properties": {"city": {"type": "string"}}})
    result = await _models().get("google/test-model").generate(
        GenerateRequest(messages=[Message.user("weather?")], tools=[tool], tool_choice="required")
    )
    sent = json.loads(route.calls[0].request.content)
    assert sent["tools"][0]["functionDeclarations"][0]["name"] == "get_weather"
    assert sent["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"

    assert result.stop_reason == StopReason.tool_use
    assert result.tool_calls[0].name == "get_weather"
    assert result.tool_calls[0].arguments == {"city": "Kyoto"}
