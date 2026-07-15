"""Functional tests for the orchestrator — full run_turn cycle."""

from __future__ import annotations

import json
from typing import Any, Iterator

import httpx
import pytest

from rickshaw.memory.embedder import TFIDFEmbedder
from rickshaw.memory.service import MemoryService
from rickshaw.orchestrator import Orchestrator
from rickshaw.providers.base import (
    Capabilities,
    Effort,
    LLMProvider,
    Message,
    Response,
    TokenUsage,
    ToolCall,
    ToolSpec,
)
from rickshaw.queue import JobQueue
from rickshaw_ai.errors import ConnectionError as RAIConnectionError


class _FakeProvider(LLMProvider):
    """Fake provider for testing. Returns tool calls on first call."""

    def __init__(
        self,
        function_calling: bool = True,
        fail_on_call: bool = False,
    ) -> None:
        self._call_count = 0
        self._function_calling = function_calling
        self._fail_on_call = fail_on_call
        self.call_log: list[dict] = []

    @property
    def name(self) -> str:
        return "fake"

    def complete(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Response:
        self._call_count += 1
        self.call_log.append({
            "messages": messages,
            "effort": effort,
            "tools": tools,
            "call_number": self._call_count,
        })

        if self._fail_on_call:
            raise ConnectionError("provider unreachable")

        # First call with tools: return a tool call
        if self._call_count == 1 and tools:
            return Response(
                text="",
                model="fake",
                effort=effort,
                tool_calls=[
                    ToolCall(
                        id="tc1",
                        name="remember",
                        arguments={"fact": "test fact from provider"},
                    )
                ],
            )

        return Response(
            text="Final answer",
            model="fake",
            effort=effort,
        )

    def stream(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        resp = self.complete(messages, effort=effort, tools=tools, **kwargs)
        yield resp.text

    def available_models(self) -> list[str]:
        return ["fake"]

    def validate(self) -> None:
        pass

    def capabilities(self) -> Capabilities:
        return Capabilities(
            function_calling=self._function_calling,
            max_context_tokens=4096,
        )


def test_run_turn_with_tool_calls():
    """Full cycle: tool call dispatched, memory written, deferred job enqueued."""
    provider = _FakeProvider(function_calling=True)
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    queue = JobQueue()
    orch = Orchestrator(provider=provider, memory=memory, queue=queue)

    result = orch.run_turn("Remember something")

    assert result.text == "Final answer"
    assert result.tool_calls_made == 1
    assert result.degraded is False
    # Provider should be called exactly twice (initial + follow-up after tool)
    assert len(provider.call_log) == 2
    # Memory should have the fact stored via the tool call
    records = memory.store.all_records()
    texts = [r.text for r in records]
    assert "test fact from provider" in texts
    # Deferred job should be enqueued
    assert queue.pending_count > 0


def test_run_turn_no_function_calling():
    """Provider without function_calling: no tools advertised, no tool calls."""
    provider = _FakeProvider(function_calling=False)
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    queue = JobQueue()
    orch = Orchestrator(provider=provider, memory=memory, queue=queue)

    result = orch.run_turn("Hello")

    assert result.text == "Final answer"
    # Should be called exactly once (no tool rounds)
    assert len(provider.call_log) == 1
    # No tools should have been passed
    assert provider.call_log[0]["tools"] is None
    # A warning about missing function-calling should be surfaced (item 7)
    assert any("function-calling" in w for w in result.warnings)


def test_run_turn_provider_failure_degrades():
    """Provider raises — orchestrator degrades to local retrieval."""
    provider = _FakeProvider(fail_on_call=True)
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    queue = JobQueue()
    orch = Orchestrator(
        provider=provider, memory=memory, queue=queue, retry_backoff=0,
    )

    result = orch.run_turn("What do you know?")

    assert result.degraded is True
    assert "Provider unreachable" in result.text
    assert any("Provider unreachable" in w for w in result.warnings)
    # Transient error retried max_retries times before degrading (item 6).
    assert len(provider.call_log) == orch.max_retries + 1


def test_run_turn_provider_failure_returns_memory_if_available():
    """On provider failure, local memory results are returned if available."""
    provider = _FakeProvider(fail_on_call=True)
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    memory.write("important stored fact")
    queue = JobQueue()
    orch = Orchestrator(
        provider=provider, memory=memory, queue=queue, retry_backoff=0,
    )

    result = orch.run_turn("important stored fact")

    assert "Provider unreachable" in result.text
    assert "important stored fact" in result.text
    assert result.degraded is True


def test_sensitive_records_never_in_messages():
    """Sensitive records must not appear in the messages sent to the provider."""
    provider = _FakeProvider(function_calling=False)
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    memory.write("public info", sensitive=False)
    memory.write("TOP SECRET credential", sensitive=True)
    queue = JobQueue()
    orch = Orchestrator(provider=provider, memory=memory, queue=queue)

    orch.run_turn("Tell me everything")

    sent_messages = provider.call_log[0]["messages"]
    all_content = " ".join(m.content for m in sent_messages)
    assert "TOP SECRET" not in all_content


class _InfiniteToolProvider(_FakeProvider):
    """Always returns a tool call of *tool_name* (never terminates on its own)."""

    def __init__(self, tool_name: str, arguments: dict, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._tool_name = tool_name
        self._arguments = arguments

    def complete(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Response:
        self._call_count += 1
        self.call_log.append({"call_number": self._call_count})
        return Response(
            text="still calling tools",
            model="fake",
            effort=effort,
            tool_calls=[
                ToolCall(
                    id=f"tc{self._call_count}",
                    name=self._tool_name,
                    arguments=self._arguments,
                )
            ],
        )


def test_bounded_tool_rounds_side_effecting():
    """Side-effecting tool calls are bounded by max_tool_rounds."""
    provider = _InfiniteToolProvider(
        "remember", {"fact": "x"}, function_calling=True,
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    queue = JobQueue()
    orch = Orchestrator(
        provider=provider, memory=memory, queue=queue, max_tool_rounds=2,
    )

    orch.run_turn("go forever")

    # 1 initial + 2 side-effecting tool rounds = 3 total
    assert len(provider.call_log) == 3


def test_read_only_tools_exempt_from_round_limit():
    """Read-only tool calls (recall) don't count against max_tool_rounds (item 6).

    They are only bounded by the hard safety cap, so the number of calls
    exceeds ``max_tool_rounds + 1``.
    """
    from rickshaw.orchestrator import _HARD_ITERATION_CAP

    provider = _InfiniteToolProvider(
        "recall", {"query": "x"}, function_calling=True,
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    queue = JobQueue()
    orch = Orchestrator(
        provider=provider, memory=memory, queue=queue, max_tool_rounds=2,
    )

    orch.run_turn("recall forever")

    # Read-only calls bypass max_tool_rounds; bounded only by the hard cap.
    assert len(provider.call_log) == _HARD_ITERATION_CAP + 1


class _EndpointProvider(_FakeProvider):
    """Fake provider that always raises *exc*, optionally pinned to a base URL."""

    def __init__(
        self, exc: Exception, base_url: str | None = None, **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._exc = exc
        if base_url is not None:
            self._base_url = base_url

    def complete(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Response:
        self._call_count += 1
        self.call_log.append({"call_number": self._call_count})
        raise self._exc


class _StreamingEndpointProvider(_EndpointProvider):
    """Streaming-capable variant whose stream() raises *exc*."""

    def stream(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        self._call_count += 1
        self.call_log.append({"call_number": self._call_count})
        raise self._exc
        yield ""  # unreachable; makes this a generator like the base class

    def capabilities(self) -> Capabilities:
        return Capabilities(
            streaming=True,
            function_calling=False,
            max_context_tokens=4096,
        )


class _ToolThenFailProvider(_FakeProvider):
    """Returns a tool call on the first call, then raises *exc*."""

    def __init__(self, exc: Exception, base_url: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._exc = exc
        self._base_url = base_url

    def complete(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        **kwargs: Any,
    ) -> Response:
        self._call_count += 1
        self.call_log.append({"call_number": self._call_count})
        if self._call_count == 1:
            return Response(
                text="",
                model="fake",
                effort=effort,
                tool_calls=[
                    ToolCall(id="tc1", name="remember", arguments={"fact": "x"})
                ],
            )
        raise self._exc


@pytest.mark.parametrize(
    "original",
    [
        RAIConnectionError("All connection attempts failed"),
        httpx.ConnectError("[Errno 61] Connection refused"),
        httpx.ConnectTimeout("timed out"),
        ConnectionError("connection refused"),
    ],
)
def test_local_connection_error_fails_fast(monkeypatch, original):
    """Connection errors against a local endpoint: one attempt, no backoff."""
    provider = _EndpointProvider(original, base_url="http://localhost:8080/v1")
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(provider=provider, memory=memory, queue=JobQueue())

    sleeps: list[float] = []
    monkeypatch.setattr("rickshaw.orchestrator.time.sleep", sleeps.append)

    with pytest.raises(ConnectionError) as excinfo:
        orch.run_turn("hello")

    assert "http://localhost:8080/v1" in str(excinfo.value)
    assert excinfo.value.__cause__ is original
    assert len(provider.call_log) == 1
    assert sleeps == []


def test_local_connection_error_with_url_not_rewrapped():
    """An error that already names the base URL propagates unwrapped."""
    original = ConnectionError(
        "Could not reach local inference server at http://localhost:8080/v1"
    )
    provider = _EndpointProvider(original, base_url="http://localhost:8080/v1")
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(provider=provider, memory=memory, queue=JobQueue())

    with pytest.raises(ConnectionError) as excinfo:
        orch.run_turn("hello")

    assert excinfo.value is original
    assert len(provider.call_log) == 1


def test_hosted_connection_error_keeps_retries():
    """Hosted endpoints keep the existing retry/backoff-then-degrade behavior."""
    provider = _EndpointProvider(
        httpx.ConnectError("connection refused"),
        base_url="https://api.example.com/v1",
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(
        provider=provider, memory=memory, queue=JobQueue(), retry_backoff=0,
    )

    result = orch.run_turn("What do you know?")

    assert result.degraded is True
    assert len(provider.call_log) == orch.max_retries + 1


def test_local_non_connection_error_still_retried():
    """Fail-fast applies only to connection errors; others retry as before."""
    provider = _EndpointProvider(
        TimeoutError("read timed out"), base_url="http://localhost:8080/v1",
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(
        provider=provider, memory=memory, queue=JobQueue(), retry_backoff=0,
    )

    result = orch.run_turn("What do you know?")

    assert result.degraded is True
    assert len(provider.call_log) == orch.max_retries + 1


def test_provider_without_base_url_unchanged():
    """Providers lacking ``_base_url`` keep the existing retry behavior."""
    provider = _EndpointProvider(httpx.ConnectError("connection refused"))
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(
        provider=provider, memory=memory, queue=JobQueue(), retry_backoff=0,
    )

    result = orch.run_turn("What do you know?")

    assert result.degraded is True
    assert len(provider.call_log) == orch.max_retries + 1


def test_streaming_local_connection_error_fails_fast(monkeypatch):
    """Streaming turns also fail fast on local connection errors."""
    original = RAIConnectionError("All connection attempts failed")
    provider = _StreamingEndpointProvider(
        original, base_url="http://localhost:1234/v1",
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(provider=provider, memory=memory, queue=JobQueue())

    sleeps: list[float] = []
    monkeypatch.setattr("rickshaw.orchestrator.time.sleep", sleeps.append)

    with pytest.raises(ConnectionError) as excinfo:
        orch.run_turn("hello", on_delta=lambda _chunk: None)

    assert "http://localhost:1234/v1" in str(excinfo.value)
    assert len(provider.call_log) == 1
    assert sleeps == []


def test_tool_followup_local_connection_error_fails_fast():
    """A local connection error on the follow-up call also fails the turn."""
    original = RAIConnectionError("All connection attempts failed")
    provider = _ToolThenFailProvider(
        original, base_url="http://localhost:11434/v1", function_calling=True,
    )
    memory = MemoryService(embedder=TFIDFEmbedder(dim=32))
    orch = Orchestrator(provider=provider, memory=memory, queue=JobQueue())

    with pytest.raises(ConnectionError) as excinfo:
        orch.run_turn("Remember something")

    assert "http://localhost:11434/v1" in str(excinfo.value)
    assert len(provider.call_log) == 2
