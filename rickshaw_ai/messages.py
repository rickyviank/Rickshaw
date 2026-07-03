"""Provider-neutral canonical messages and content blocks.

This is the *source of truth* for a conversation. Every provider adapter
translates these canonical types to/from its own wire format, which is what
makes cross-provider handoff (see :mod:`rickshaw_ai.session`) possible: the
history is stored once, canonically, and re-serialized per target provider.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field

Role = Literal["system", "user", "assistant", "tool"]


class TextBlock(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ImageBlock(BaseModel):
    """An image, either provided as input or produced by a model."""

    type: Literal["image"] = "image"
    media_type: str  # e.g. "image/png"
    source: Literal["base64", "url"]
    data: str  # base64 payload or a URL, per ``source``
    origin: Literal["input", "output"] = "input"


class ToolUseBlock(BaseModel):
    """An assistant's request to call a tool."""

    type: Literal["tool_use"] = "tool_use"
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolResultBlock(BaseModel):
    """The result of a tool call, handed back to the model."""

    type: Literal["tool_result"] = "tool_result"
    tool_use_id: str
    content: list["ContentBlock"] = Field(default_factory=list)
    is_error: bool = False


class ThinkingBlock(BaseModel):
    """Unified reasoning/thinking output.

    ``signature`` is provider-specific (e.g. Anthropic requires replaying the
    signed thinking block verbatim on same-provider turns). It is stripped on
    cross-provider handoff. ``provider`` records which provider produced it.
    """

    type: Literal["thinking"] = "thinking"
    text: str = ""
    signature: str | None = None
    redacted: bool = False
    provider: str | None = None


ContentBlock = Annotated[
    Union[TextBlock, ImageBlock, ToolUseBlock, ToolResultBlock, ThinkingBlock],
    Field(discriminator="type"),
]


class Message(BaseModel):
    role: Role
    content: list[ContentBlock] = Field(default_factory=list)

    # -- ergonomic constructors -------------------------------------------

    @classmethod
    def user(cls, text: str) -> "Message":
        return cls(role="user", content=[TextBlock(text=text)])

    @classmethod
    def assistant(cls, text: str) -> "Message":
        return cls(role="assistant", content=[TextBlock(text=text)])

    @classmethod
    def system(cls, text: str) -> "Message":
        return cls(role="system", content=[TextBlock(text=text)])

    # -- convenience accessors --------------------------------------------

    @property
    def text(self) -> str:
        """Concatenated text of all :class:`TextBlock`s in this message."""
        return "".join(b.text for b in self.content if isinstance(b, TextBlock))

    @property
    def tool_uses(self) -> list[ToolUseBlock]:
        return [b for b in self.content if isinstance(b, ToolUseBlock)]

    @property
    def thinking(self) -> list[ThinkingBlock]:
        return [b for b in self.content if isinstance(b, ThinkingBlock)]


def normalize_content(
    content: "str | ContentBlock | list[ContentBlock]",
) -> list[ContentBlock]:
    """Coerce a string / single block / list into a list of content blocks."""
    if isinstance(content, str):
        return [TextBlock(text=content)]
    if isinstance(content, BaseModel):
        return [content]  # type: ignore[list-item]
    return list(content)


ToolResultBlock.model_rebuild()
Message.model_rebuild()
