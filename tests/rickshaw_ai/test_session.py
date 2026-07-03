"""Session: cross-provider handoff, persistence, cost aggregation."""

from __future__ import annotations

import httpx
import pytest
import respx

from rickshaw_ai import (
    ApiKeyCredential,
    GenerateRequest,
    InMemoryCredentialStore,
    InvalidRequestError,
    Message,
    Pricing,
    create_models,
)
from rickshaw_ai.messages import ImageBlock, TextBlock
from rickshaw_ai.registry import ModelInfo, ProviderInfo, RetryPolicy

OAI_URL = "https://oai.test/chat/completions"
ANT_URL = "https://anthropic.test/v1/messages"


def _two_provider_models():
    oai = ProviderInfo(
        id="oai", base_url="https://oai.test", protocol="openai",
        models=[ModelInfo(id="oai/gpt", provider_id="oai", model="gpt",
                          supports_tools=True, supports_vision_input=True,
                          pricing=Pricing(input=1.0, output=1.0))],
    )
    ant = ProviderInfo(
        id="anthropic", base_url="https://anthropic.test", protocol="anthropic",
        api_key_header="x-api-key", api_key_prefix="",
        models=[
            ModelInfo(id="anthropic/claude", provider_id="anthropic", model="claude",
                      supports_tools=True, supports_vision_input=True,
                      pricing=Pricing(input=2.0, output=2.0)),
            ModelInfo(id="anthropic/text-only", provider_id="anthropic", model="text-only",
                      supports_tools=True, supports_vision_input=False,
                      pricing=Pricing(input=2.0, output=2.0)),
        ],
    )
    store = InMemoryCredentialStore({
        "oai": ApiKeyCredential(key="k1"),
        "anthropic": ApiKeyCredential(key="k2"),
    })
    return create_models(providers=[oai, ant], credentials=store,
                         retry=RetryPolicy(max_retries=0))


OAI_TOOL = {
    "model": "gpt",
    "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
        "role": "assistant", "content": None,
        "tool_calls": [{"id": "call_1", "type": "function",
                        "function": {"name": "get_time", "arguments": "{}"}}]}}],
    "usage": {"prompt_tokens": 100, "completion_tokens": 10},
}
ANT_TEXT = {
    "id": "m", "type": "message", "role": "assistant", "model": "claude",
    "content": [{"type": "text", "text": "It is noon."}],
    "stop_reason": "end_turn", "usage": {"input_tokens": 50, "output_tokens": 5},
}


@respx.mock
async def test_handoff_tool_turn_then_continue_on_other_provider():
    respx.post(OAI_URL).mock(return_value=httpx.Response(200, json=OAI_TOOL))
    ant_route = respx.post(ANT_URL).mock(return_value=httpx.Response(200, json=ANT_TEXT))

    models = _two_provider_models()
    session = models.session(system="be brief")

    r1 = await session.run("what time is it?", model="oai/gpt")
    assert r1.tool_calls[0].name == "get_time"
    session.add_tool_result("call_1", "12:00")

    r2 = await session.run(None, model="anthropic/claude")
    assert r2.text == "It is noon."

    # The Anthropic request carries the tool_use/tool_result pairing from OpenAI.
    import json
    ant_body = json.loads(ant_route.calls[0].request.content)
    roles = [m["role"] for m in ant_body["messages"]]
    assert roles == ["user", "assistant", "user"]  # q, tool_use, tool_result

    # Usage aggregated across providers with a per-model breakdown.
    assert set(session.usage.per_model) == {"oai/gpt", "anthropic/claude"}
    assert session.usage.total.input_tokens == 150
    assert session.usage.total.cost_usd == pytest.approx(
        (100 + 10) / 1e6 * 1.0 + (50 + 5) / 1e6 * 2.0
    )


@respx.mock
async def test_dump_load_round_trip():
    respx.post(OAI_URL).mock(return_value=httpx.Response(200, json=OAI_TOOL))
    models = _two_provider_models()
    session = models.session(system="sys")
    await session.run("hi", model="oai/gpt")

    data = session.dump()
    restored = __import__("rickshaw_ai").Session.load(data, models)
    assert restored.system == "sys"
    assert len(restored.messages) == len(session.messages)
    assert restored.messages[0].text == "hi"
    assert restored.usage.total.input_tokens == session.usage.total.input_tokens


@respx.mock
async def test_handoff_to_non_vision_model_raises():
    models = _two_provider_models()
    session = models.session()
    img = Message(role="user", content=[
        TextBlock(text="see this"),
        ImageBlock(media_type="image/png", source="base64", data="AAAA"),
    ])
    session.messages.append(img)
    with pytest.raises(InvalidRequestError, match="image input"):
        await session.run(None, model="anthropic/text-only")


@respx.mock
async def test_handoff_strips_foreign_thinking_signature():
    """Anthropic-origin thinking is not replayed to a different provider."""
    import json
    from rickshaw_ai.messages import ThinkingBlock

    route = respx.post(OAI_URL).mock(return_value=httpx.Response(200, json={
        "model": "gpt", "choices": [{"index": 0, "finish_reason": "stop",
            "message": {"role": "assistant", "content": "ok"}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1}}))
    models = _two_provider_models()
    session = models.session()
    session.messages.append(Message.user("q"))
    session.messages.append(Message(role="assistant", content=[
        ThinkingBlock(text="secret", signature="sig", provider="anthropic"),
        TextBlock(text="answer"),
    ]))
    await session.run("again", model="oai/gpt")
    body = json.loads(route.calls[0].request.content)
    # No thinking leaks into the OpenAI request; only text survives.
    assert "secret" not in route.calls[0].request.content.decode()
    assert any(m.get("content") == "answer" for m in body["messages"])
