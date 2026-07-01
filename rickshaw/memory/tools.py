"""Memory tools — remember/recall/forget as normalized tool specs + dispatch."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from rickshaw.providers.base import ToolCall, ToolSpec

if TYPE_CHECKING:
    from rickshaw.memory.service import MemoryService


REMEMBER_SPEC = ToolSpec(
    name="remember",
    description="Store a fact or observation in long-term memory.",
    parameters={
        "type": "object",
        "properties": {
            "fact": {
                "type": "string",
                "description": "The fact or observation to remember.",
            },
        },
        "required": ["fact"],
    },
)

RECALL_SPEC = ToolSpec(
    name="recall",
    description="Retrieve relevant memories matching a query.",
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "A natural-language query to search memories.",
            },
        },
        "required": ["query"],
    },
)

FORGET_SPEC = ToolSpec(
    name="forget",
    description="Delete a memory record by its id.",
    parameters={
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "The id of the memory record to delete.",
            },
        },
        "required": ["id"],
    },
)

MEMORY_TOOL_SPECS: list[ToolSpec] = [REMEMBER_SPEC, RECALL_SPEC, FORGET_SPEC]


def dispatch_tool_call(
    tool_call: ToolCall,
    memory_service: MemoryService,
) -> str:
    """Map a normalized ToolCall to the corresponding memory operation.

    Returns a JSON-serialized result suitable for a tool/role="tool" message.
    """
    name = tool_call.name
    args = tool_call.arguments

    if name == "remember":
        result = memory_service.remember(args.get("fact", ""))
    elif name == "recall":
        result = memory_service.recall(args.get("query", ""))
    elif name == "forget":
        result = memory_service.forget(args.get("id", ""))
    else:
        result = f"unknown tool: {name}"

    return json.dumps(result)
