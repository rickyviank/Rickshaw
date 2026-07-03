"""Adapter for the Google Gemini ``generateContent`` API."""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import httpx

from rickshaw_ai.generate import GenerateRequest, GenerateResult, StopReason, Usage
from rickshaw_ai.messages import (
    ImageBlock,
    Message,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
)
from rickshaw_ai.providers.base import ProviderAdapter, aiter_sse
from rickshaw_ai.registry import ModelInfo, ProviderInfo
from rickshaw_ai.streaming import (
    StreamDone,
    StreamEvent,
    TextDelta,
    ToolCallEnd,
    ToolCallStart,
)
from rickshaw_ai.tools import Tool, ToolCall

_EFFORT_BUDGET = {"low": 1024, "medium": 8192, "high": 24576}

_STOP_MAP = {
    "STOP": StopReason.end_turn,
    "MAX_TOKENS": StopReason.max_output_tokens,
    "SAFETY": StopReason.content_filter,
    "RECITATION": StopReason.content_filter,
}

_TOOL_MODE = {"auto": "AUTO", "required": "ANY", "none": "NONE"}


def _parts_for(blocks: list) -> list[dict[str, Any]]:
    parts: list[dict[str, Any]] = []
    for b in blocks:
        if isinstance(b, TextBlock):
            if b.text:
                parts.append({"text": b.text})
        elif isinstance(b, ImageBlock) and b.source == "base64":
            parts.append({"inlineData": {"mimeType": b.media_type, "data": b.data}})
        elif isinstance(b, ToolUseBlock):
            parts.append({"functionCall": {"name": b.name, "args": b.arguments}})
    return parts


def _wire_contents(req: GenerateRequest) -> list[dict[str, Any]]:
    contents: list[dict[str, Any]] = []
    for msg in req.messages:
        tool_results = [b for b in msg.content if isinstance(b, ToolResultBlock)]
        if tool_results:
            parts = [
                {
                    "functionResponse": {
                        "name": tr.tool_use_id,
                        "response": {
                            "content": "".join(
                                b.text for b in tr.content if isinstance(b, TextBlock)
                            )
                        },
                    }
                }
                for tr in tool_results
            ]
            contents.append({"role": "user", "parts": parts})
            continue
        role = "model" if msg.role == "assistant" else "user"
        contents.append({"role": role, "parts": _parts_for(msg.content)})
    return contents


def _parse_usage(data: dict[str, Any], model: ModelInfo) -> Usage:
    u = data.get("usageMetadata") or {}
    usage = Usage(
        input_tokens=u.get("promptTokenCount", 0),
        output_tokens=u.get("candidatesTokenCount", 0),
        reasoning_tokens=u.get("thoughtsTokenCount", 0),
        cache_read_tokens=u.get("cachedContentTokenCount", 0),
    )
    usage.cost_usd = usage.compute_cost(model.pricing)
    return usage


def _parse_candidate(data: dict[str, Any]) -> tuple[list, str | None]:
    candidates = data.get("candidates") or [{}]
    cand = candidates[0]
    content: list = []
    for part in cand.get("content", {}).get("parts", []) or []:
        if "text" in part:
            content.append(TextBlock(text=part["text"]))
        elif "functionCall" in part:
            fc = part["functionCall"]
            content.append(
                ToolUseBlock(
                    id=fc.get("name", ""),
                    name=fc.get("name", ""),
                    arguments=fc.get("args") or {},
                )
            )
    return content, cand.get("finishReason")


class GoogleAdapter(ProviderAdapter):
    protocol = "google"

    def endpoint(self, provider: ProviderInfo, model: ModelInfo, *, stream: bool) -> str:
        verb = "streamGenerateContent" if stream else "generateContent"
        suffix = "?alt=sse" if stream else ""
        return f"{provider.base_url.rstrip('/')}/v1beta/models/{model.model}:{verb}{suffix}"

    def build_body(
        self, req: GenerateRequest, model: ModelInfo, *, stream: bool
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"contents": _wire_contents(req)}
        if req.system:
            body["systemInstruction"] = {"parts": [{"text": req.system}]}

        gen: dict[str, Any] = {}
        if req.max_output_tokens is not None:
            gen["maxOutputTokens"] = req.max_output_tokens
        if req.temperature is not None:
            gen["temperature"] = req.temperature
        if req.stop_sequences:
            gen["stopSequences"] = req.stop_sequences
        if model.supports_reasoning and req.reasoning is not None:
            budget = req.reasoning.budget_tokens
            if budget is None and req.reasoning.effort is not None:
                budget = _EFFORT_BUDGET.get(req.reasoning.effort)
            if budget is not None:
                gen["thinkingConfig"] = {"thinkingBudget": budget}
        if gen:
            body["generationConfig"] = gen

        if req.tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        {
                            "name": t.name,
                            "description": t.description,
                            "parameters": t.parameters,
                        }
                        for t in req.tools
                    ]
                }
            ]
            if req.tool_choice is not None:
                body["toolConfig"] = {
                    "functionCallingConfig": {"mode": _TOOL_MODE[req.tool_choice]}
                }
        body.update(req.provider_options)
        return body

    def parse_response(
        self, data: dict[str, Any], model: ModelInfo, provider: ProviderInfo
    ) -> GenerateResult:
        content, raw_stop = _parse_candidate(data)
        stop = _STOP_MAP.get(raw_stop, StopReason.end_turn)
        if any(isinstance(b, ToolUseBlock) for b in content):
            stop = StopReason.tool_use
        return GenerateResult(
            message=Message(role="assistant", content=content),
            stop_reason=stop,
            usage=_parse_usage(data, model),
            model_id=model.id,
            provider_id=provider.id,
            metadata={
                "raw_stop_reason": raw_stop,
                "response_model": data.get("modelVersion", model.model),
                "raw": data,
            },
        )

    async def parse_stream(
        self, response: httpx.Response, model: ModelInfo, provider: ProviderInfo
    ) -> AsyncIterator[StreamEvent]:
        text_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        raw_stop: str | None = None
        usage_data: dict[str, Any] = {}

        async for payload in aiter_sse(response):
            chunk = json.loads(payload)
            if chunk.get("usageMetadata"):
                usage_data = chunk
            content, finish = _parse_candidate(chunk)
            if finish:
                raw_stop = finish
            for block in content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
                    yield TextDelta(text=block.text)
                elif isinstance(block, ToolUseBlock):
                    yield ToolCallStart(id=block.id, name=block.name)
                    tool_calls.append(
                        ToolCall(id=block.id, name=block.name, arguments=block.arguments)
                    )

        content: list = []
        if text_parts:
            content.append(TextBlock(text="".join(text_parts)))
        for call in tool_calls:
            yield ToolCallEnd(call=call)
            content.append(
                ToolUseBlock(id=call.id, name=call.name, arguments=call.arguments)
            )

        stop = _STOP_MAP.get(raw_stop, StopReason.end_turn)
        if tool_calls:
            stop = StopReason.tool_use
        yield StreamDone(
            result=GenerateResult(
                message=Message(role="assistant", content=content),
                stop_reason=stop,
                usage=_parse_usage(usage_data, model),
                model_id=model.id,
                provider_id=provider.id,
                metadata={"raw_stop_reason": raw_stop},
            )
        )
