"""Tool definition, validation, and streaming assembly."""

from __future__ import annotations

import pytest

from rickshaw_ai import Tool, ToolInputError, tool, validate_arguments
from rickshaw_ai.tools import ToolCall, ToolCallAssembler, validate_call


def test_tool_decorator_derives_schema_and_description():
    @tool
    def get_weather(city: str, units: str = "c") -> str:
        """Get weather for a city."""
        return f"{city}:{units}"

    assert get_weather.name == "get_weather"
    assert get_weather.description == "Get weather for a city."
    assert get_weather.parameters["required"] == ["city"]
    assert get_weather.parameters["properties"]["city"]["type"] == "string"


async def test_tool_invoke_sync_and_async():
    @tool
    def add(a: int, b: int) -> int:
        return a + b

    assert await add.invoke({"a": 2, "b": 3}) == 5

    @tool
    async def amul(a: int, b: int) -> int:
        return a * b

    assert await amul.invoke({"a": 2, "b": 3}) == 6


def test_validate_arguments_ok_and_missing():
    schema = {"type": "object", "properties": {"x": {"type": "string"}}, "required": ["x"]}
    validate_arguments(schema, {"x": "hi"})  # no raise
    with pytest.raises(ToolInputError):
        validate_arguments(schema, {})


def test_validate_arguments_type_mismatch():
    schema = {"type": "object", "properties": {"n": {"type": "integer"}}}
    with pytest.raises(ToolInputError):
        validate_arguments(schema, {"n": "not-an-int"})


def test_validate_call_unknown_tool():
    t = Tool(name="known")
    with pytest.raises(ToolInputError, match="unknown tool"):
        validate_call([t], ToolCall(id="1", name="mystery", arguments={}))


def test_assembler_builds_multiple_calls():
    asm = ToolCallAssembler()
    asm.start("0", call_id="a", name="f")
    asm.delta("0", '{"x":')
    asm.delta("0", " 1}")
    asm.start("1", call_id="b", name="g")
    asm.delta("1", '{"y": 2}')
    calls = asm.finish()
    assert [c.name for c in calls] == ["f", "g"]
    assert calls[0].arguments == {"x": 1}
    assert calls[1].arguments == {"y": 2}


def test_assembler_malformed_json_raises():
    asm = ToolCallAssembler()
    asm.start("0", call_id="a", name="f")
    asm.delta("0", '{"x": ')  # incomplete
    with pytest.raises(ToolInputError):
        asm.finish()


def test_assembler_empty_arguments_defaults_to_object():
    asm = ToolCallAssembler()
    asm.start("0", call_id="a", name="noargs")
    calls = asm.finish()
    assert calls[0].arguments == {}
