"""Conversation session with cross-provider handoff and cost tracking.

A :class:`Session` owns the canonical history. Each turn may target any model;
switching models mid-session is a *handoff*, implemented by re-serializing the
same canonical history to the new provider. Usage is aggregated across the whole
session and broken down per model.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, AsyncIterator

from pydantic import BaseModel, Field

from rickshaw_ai.errors import InvalidRequestError
from rickshaw_ai.generate import GenerateRequest, GenerateResult, Reasoning, Usage
from rickshaw_ai.messages import (
    ContentBlock,
    ImageBlock,
    Message,
    ThinkingBlock,
    normalize_content,
)
from rickshaw_ai.streaming import StreamDone, StreamEvent
from rickshaw_ai.tools import Tool

if TYPE_CHECKING:
    from rickshaw_ai.factory import Models


class SessionUsage(BaseModel):
    """Aggregated usage: a running total plus a per-model breakdown."""

    total: Usage = Field(default_factory=Usage)
    per_model: dict[str, Usage] = Field(default_factory=dict)

    def add(self, model_id: str, usage: Usage) -> None:
        self.total = self.total + usage
        self.per_model[model_id] = self.per_model.get(model_id, Usage()) + usage


class Session:
    def __init__(
        self,
        models: "Models",
        *,
        system: str | None = None,
        tools: list[Tool] | None = None,
        messages: list[Message] | None = None,
    ) -> None:
        self._models = models
        self.system = system
        self.tools = tools or []
        self.messages: list[Message] = messages or []
        self.usage = SessionUsage()

    # -- turn execution ----------------------------------------------------

    def _build_request(
        self, provider_id: str, model_supports_vision: bool, overrides: dict[str, Any]
    ) -> GenerateRequest:
        messages = self._prepare_for(provider_id, model_supports_vision)
        reasoning = overrides.pop("reasoning", None)
        if isinstance(reasoning, dict):
            reasoning = Reasoning(**reasoning)
        return GenerateRequest(
            messages=messages,
            system=self.system,
            tools=self.tools,
            reasoning=reasoning,
            tool_choice=overrides.pop("tool_choice", None),
            max_output_tokens=overrides.pop("max_output_tokens", None),
            temperature=overrides.pop("temperature", None),
            stop_sequences=overrides.pop("stop_sequences", []),
            provider_options=overrides.pop("provider_options", {}),
        )

    async def run(
        self,
        user_input: "str | ContentBlock | list[ContentBlock] | None",
        *,
        model: str,
        **overrides: Any,
    ) -> GenerateResult:
        """Run one turn on *model*, appending input and the reply to history."""
        handle = self._models.get(model)
        if user_input is not None:
            self.messages.append(
                Message(role="user", content=normalize_content(user_input))
            )
        req = self._build_request(
            handle.info.provider_id, handle.info.supports_vision_input, overrides
        )
        result = await handle.generate(req)
        self.messages.append(result.message)
        self.usage.add(handle.info.id, result.usage)
        return result

    async def stream(
        self,
        user_input: "str | ContentBlock | list[ContentBlock] | None",
        *,
        model: str,
        **overrides: Any,
    ) -> AsyncIterator[StreamEvent]:
        handle = self._models.get(model)
        if user_input is not None:
            self.messages.append(
                Message(role="user", content=normalize_content(user_input))
            )
        req = self._build_request(
            handle.info.provider_id, handle.info.supports_vision_input, overrides
        )
        async for event in handle.stream(req):
            if isinstance(event, StreamDone):
                self.messages.append(event.result.message)
                self.usage.add(handle.info.id, event.result.usage)
            yield event

    def add_tool_result(
        self, tool_use_id: str, content: "str | ContentBlock | list[ContentBlock]",
        *, is_error: bool = False,
    ) -> None:
        """Append a tool result message referencing *tool_use_id*."""
        from rickshaw_ai.messages import ToolResultBlock

        block = ToolResultBlock(
            tool_use_id=tool_use_id,
            content=normalize_content(content),
            is_error=is_error,
        )
        self.messages.append(Message(role="tool", content=[block]))

    # -- handoff preparation ----------------------------------------------

    def _prepare_for(
        self, target_provider: str, model_supports_vision: bool
    ) -> list[Message]:
        """Return history re-serialized for *target_provider*.

        Strips provider-scoped reasoning signatures on handoff, drops redacted
        thinking, and raises if the target lacks a capability the history needs.
        """
        prepared: list[Message] = []
        for msg in self.messages:
            new_content: list[ContentBlock] = []
            for block in msg.content:
                if isinstance(block, ThinkingBlock):
                    if block.provider and block.provider != target_provider:
                        if block.redacted:
                            continue  # cannot be replayed elsewhere
                        new_content.append(
                            block.model_copy(update={"signature": None})
                        )
                        continue
                if isinstance(block, ImageBlock) and block.origin == "input":
                    if not model_supports_vision:
                        raise InvalidRequestError(
                            f"target model on provider {target_provider!r} does not "
                            f"support image input, but the conversation contains an "
                            f"image; switch to a vision-capable model",
                            provider_id=target_provider,
                        )
                new_content.append(block)
            prepared.append(Message(role=msg.role, content=new_content))
        return prepared

    # -- persistence -------------------------------------------------------

    def dump(self) -> dict[str, Any]:
        """Return a JSON-serializable snapshot of the canonical conversation."""
        return {
            "system": self.system,
            "tools": [
                {
                    "name": t.name,
                    "description": t.description,
                    "parameters": t.parameters,
                    "category": t.category,
                    "side_effect": t.side_effect,
                }
                for t in self.tools
            ],
            "messages": [m.model_dump() for m in self.messages],
            "usage": self.usage.model_dump(),
        }

    @classmethod
    def load(cls, data: dict[str, Any], models: "Models") -> "Session":
        """Rebuild a session from :meth:`dump` output.

        Tool *handlers* are not serialized; loaded tools carry only their specs.
        """
        tools = [Tool(**spec) for spec in data.get("tools", [])]
        messages = [Message.model_validate(m) for m in data.get("messages", [])]
        session = cls(
            models,
            system=data.get("system"),
            tools=tools,
            messages=messages,
        )
        if data.get("usage"):
            session.usage = SessionUsage.model_validate(data["usage"])
        return session
