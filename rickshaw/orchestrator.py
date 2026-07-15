"""Orchestrator — owns the turn loop.

The only hot-path caller of the provider. Depends on LLMProvider via
dependency injection, forwards Effort, advertises tool specs from an injected
:class:`ToolRegistry`, and dispatches returned tool calls.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, Iterator

import httpx

from rickshaw import events
from rickshaw.config import is_local_url
from rickshaw.memory.service import MemoryService
from rickshaw.memory.tools import build_memory_registry
from rickshaw.prompt.builder import PromptBuilder, _estimate_tokens
from rickshaw.providers.base import (
    Effort,
    LLMProvider,
    Message,
    Response,
    TokenUsage,
    ToolCall,
    ToolSpec,
)
from rickshaw.queue import Job, JobQueue, JobType
from rickshaw.tool_registry import ToolRegistry
from rickshaw.trace_store import TraceStore, new_turn_id
from rickshaw_ai.errors import ConnectionError as RAIConnectionError

# Callback invoked with incremental text as a turn's final answer is produced.
StreamCallback = Callable[[str], None]

# Callback invoked with lifecycle events for a turn.
EventCallback = Callable[[events.TurnEvent], None]

logger = logging.getLogger(__name__)

_MAX_TOOL_ROUNDS = 3
_MAX_RETRIES = 2
_RETRY_BACKOFF = 1.0  # seconds; delay = backoff * 2**attempt (1s, 2s)
# Absolute safety cap on loop iterations, so a stream of read-only tool calls
# (which don't count against max_tool_rounds) can't spin forever.
_HARD_ITERATION_CAP = 20

_PROVIDER_UNREACHABLE_MSG = "Provider unreachable — showing cached results"

_DEFAULT_SYSTEM = (
    "You are a helpful assistant with access to a semantic memory layer. "
    "Use the provided tools to remember, recall, or forget information."
)


@dataclass
class TurnResult:
    """Structured result of a single turn.

    ``text`` is the assistant's final text. ``warnings`` surfaces degradation
    (provider unreachable, function-calling unsupported) so callers/CLIs can
    display it without parsing ``text``. ``tool_calls_made`` counts dispatched
    tool calls. ``degraded`` is True when the turn fell back to local memory.
    """

    text: str
    warnings: list[str] = field(default_factory=list)
    tool_calls_made: int = 0
    degraded: bool = False
    model: str = ""
    usage: TokenUsage | None = None

    def __str__(self) -> str:  # convenience for print()/logging
        return self.text


def _is_transient_error(exc: Exception) -> bool:
    """Whether *exc* is a transient provider error worth retrying."""
    if isinstance(exc, (httpx.TransportError, ConnectionError, TimeoutError)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code == 429 or code >= 500
    return False


_CONNECTION_ERRORS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    ConnectionError,
    RAIConnectionError,
)


def _local_connection_error(exc: Exception, provider: LLMProvider) -> Exception | None:
    """Return a fail-fast error when *exc* is a connection failure against a
    local endpoint, or ``None`` when normal retry handling should apply.

    A down local server is user-fixable, so it is surfaced immediately instead
    of retried. The returned error's message always contains the endpoint's
    base URL so the UI can attach hints; the original *exc* is preserved as
    ``__cause__`` when wrapping is needed.
    """
    if not isinstance(exc, _CONNECTION_ERRORS):
        return None
    base_url = getattr(provider, "_base_url", "")
    if not is_local_url(base_url):
        return None
    if base_url in str(exc):
        return exc
    wrapped = ConnectionError(f"cannot connect to {base_url}: {exc}")
    wrapped.__cause__ = exc
    return wrapped


class Orchestrator:
    """Turn loop with memory-augmented retrieval and tool dispatch.

    Degrades gracefully if:
    * The provider is unreachable (retries with backoff, then falls back to
      local remember/recall/ranking). Exception: connection failures against
      local endpoints are never retried — the turn fails immediately with an
      error naming the base URL.
    * The provider reports ``function_calling=False`` (skips tool advertising
      and surfaces a warning).

    The turn lifecycle is exposed as a stream of :class:`events.TurnEvent`
    objects via the optional ``on_event`` callback and persisted to the optional
    ``trace_store``.
    """

    def __init__(
        self,
        provider: LLMProvider,
        memory: MemoryService,
        prompt_builder: PromptBuilder | None = None,
        queue: JobQueue | None = None,
        registry: ToolRegistry | None = None,
        system: str = _DEFAULT_SYSTEM,
        effort: Effort = Effort.MEDIUM,
        max_tool_rounds: int = _MAX_TOOL_ROUNDS,
        max_retries: int = _MAX_RETRIES,
        retry_backoff: float = _RETRY_BACKOFF,
        on_event: EventCallback | None = None,
        trace_store: TraceStore | None = None,
    ) -> None:
        self.provider = provider
        self.memory = memory
        self.prompt_builder = prompt_builder or PromptBuilder()
        self.queue = queue or JobQueue()
        # Tool dispatch is decoupled from MemoryService via the registry. Memory
        # tools are registered here at construction; callers may inject a
        # pre-populated registry (e.g. with additional non-memory tools).
        self.registry = registry or build_memory_registry(memory)
        self.system = system
        self.effort = effort
        self.max_tool_rounds = max_tool_rounds
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self._on_event = on_event
        self._trace_store = trace_store

        # Session-start capability notice (item 7).
        if self.provider is not None and not self.provider.capabilities().function_calling:
            logger.info(
                "Provider '%s' does not support function-calling; memory tools "
                "will not be advertised to the model. Context retrieval is "
                "harness-driven.",
                self.provider.name,
            )

    def _emit(
        self,
        event: events.TurnEvent,
        turn_id: str,
        on_event: EventCallback | None,
        trace_store: TraceStore | None,
    ) -> None:
        """Deliver a turn event to the callback and/or trace store."""
        if on_event is not None:
            on_event(event)
        if trace_store is not None:
            trace_store.emit(turn_id, event)

    def _complete_with_retry(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None,
        turn_id: str,
        on_event: EventCallback | None,
        trace_store: TraceStore | None,
    ) -> Response:
        """Call the provider, retrying transient errors with exponential backoff.

        Connection failures against local endpoints are never retried: a down
        local server is user-fixable, so the error is re-raised immediately
        with the endpoint's base URL in its message.
        """
        attempt = 0
        while True:
            self._emit(
                events.LLMCallStart(attempt=attempt + 1, model=self.provider.name),
                turn_id,
                on_event,
                trace_store,
            )
            try:
                response = self.provider.complete(
                    messages, effort=self.effort, tools=tool_specs,
                )
            except Exception as exc:
                local_err = _local_connection_error(exc, self.provider)
                if local_err is not None:
                    logger.warning(
                        "Local provider unreachable, failing fast: %s", local_err,
                    )
                    raise local_err
                if _is_transient_error(exc) and attempt < self.max_retries:
                    delay = self.retry_backoff * (2 ** attempt)
                    logger.warning(
                        "Transient provider error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, self.max_retries, delay, exc,
                    )
                    self._emit(
                        events.Retry(
                            attempt=attempt + 1,
                            max_retries=self.max_retries,
                            delay=delay,
                            error=str(exc),
                        ),
                        turn_id,
                        on_event,
                        trace_store,
                    )
                    if delay > 0:
                        time.sleep(delay)
                    attempt += 1
                    continue
                raise
            self._emit(
                events.LLMCallDone(model=response.model, usage=response.usage),
                turn_id,
                on_event,
                trace_store,
            )
            return response

    def _provider_stream_events(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None,
    ) -> Iterator[events.StreamEvent]:
        """Return a stream-event iterator from the provider.

        Prefer the provider's native :meth:`stream_events` implementation when
        available. If only the legacy :meth:`stream` iterator is overridden,
        wrap its text chunks as ``TextDelta`` events followed by a synthetic
        ``StreamDone`` so the orchestrator can consume both uniformly.
        """
        provider_type = type(self.provider)
        stream_events_overridden = provider_type.stream_events is not LLMProvider.stream_events
        stream_overridden = provider_type.stream is not LLMProvider.stream

        if stream_events_overridden:
            return self.provider.stream_events(
                messages, effort=self.effort, tools=tool_specs,
            )

        if stream_overridden:
            def _wrap_stream() -> Iterator[events.StreamEvent]:
                parts: list[str] = []
                for chunk in self.provider.stream(
                    messages, effort=self.effort, tools=tool_specs,
                ):
                    parts.append(chunk)
                    yield events.TextDelta(text=chunk)
                yield events.StreamDone(
                    text="".join(parts),
                    model=self.provider.name,
                    usage=TokenUsage(),
                    tool_calls=[],
                )

            return _wrap_stream()

        # Neither method is overridden; fall back to the base stream_events
        # implementation, which calls complete().
        return self.provider.stream_events(
            messages, effort=self.effort, tools=tool_specs,
        )

    def _stream_events_with_retry(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None,
        turn_id: str,
        on_event: EventCallback | None,
        trace_store: TraceStore | None,
        on_delta: StreamCallback | None,
    ) -> Response:
        """Consume provider stream events, retrying only before any event.

        Transient errors are retried only before any event has been emitted;
        once the stream has started we can't safely restart. Connection
        failures against local endpoints are never retried.
        """
        attempt = 0
        while True:
            self._emit(
                events.LLMCallStart(attempt=attempt + 1, model=self.provider.name),
                turn_id,
                on_event,
                trace_store,
            )
            saw_event = False
            text_parts: list[str] = []
            active_tool_calls: dict[str, dict] = {}
            streamed_tool_calls: list[ToolCall] = []
            try:
                for event in self._provider_stream_events(messages, tool_specs):
                    saw_event = True
                    if isinstance(event, events.TextDelta):
                        text_parts.append(event.text)
                        if on_delta is not None:
                            on_delta(event.text)
                        self._emit(
                            events.TurnTextDelta(text=event.text),
                            turn_id,
                            on_event,
                            trace_store,
                        )
                    elif isinstance(event, events.ThinkingDelta):
                        self._emit(
                            events.TurnThinkingDelta(text=event.text),
                            turn_id,
                            on_event,
                            trace_store,
                        )
                    elif isinstance(event, events.ToolCallStart):
                        active_tool_calls[event.id] = {
                            "name": event.name,
                            "arguments": [],
                        }
                    elif isinstance(event, events.ToolCallDelta):
                        info = active_tool_calls.get(event.id)
                        if info is not None:
                            info["arguments"].append(event.arguments_fragment)
                    elif isinstance(event, events.ToolCallEnd):
                        if event.call is not None:
                            streamed_tool_calls.append(event.call)
                        elif event.id in active_tool_calls:
                            info = active_tool_calls[event.id]
                            args_str = "".join(info["arguments"])
                            try:
                                args = json.loads(args_str) if args_str else {}
                            except json.JSONDecodeError:
                                args = {}
                            streamed_tool_calls.append(
                                ToolCall(
                                    id=event.id,
                                    name=info["name"],
                                    arguments=args,
                                )
                            )
                    elif isinstance(event, events.StreamDone):
                        self._emit(
                            events.LLMCallDone(
                                model=event.model, usage=event.usage,
                            ),
                            turn_id,
                            on_event,
                            trace_store,
                        )
                        tool_calls = (
                            list(event.tool_calls)
                            if event.tool_calls
                            else streamed_tool_calls
                        )
                        if not tool_calls and active_tool_calls:
                            for tc_id, info in active_tool_calls.items():
                                args_str = "".join(info["arguments"])
                                try:
                                    args = json.loads(args_str) if args_str else {}
                                except json.JSONDecodeError:
                                    args = {}
                                tool_calls.append(
                                    ToolCall(
                                        id=tc_id,
                                        name=info["name"],
                                        arguments=args,
                                    )
                                )
                        return Response(
                            text=event.text,
                            model=event.model,
                            usage=event.usage,
                            tool_calls=tool_calls,
                        )
                    elif isinstance(event, events.StreamError):
                        raise RuntimeError(event.message)

                # The generator ended without a StreamDone. If we collected text,
                # return it as a partial response; otherwise treat as an error.
                if text_parts:
                    return Response(
                        text="".join(text_parts),
                        model=self.provider.name,
                        usage=TokenUsage(),
                        tool_calls=[],
                    )
                raise RuntimeError("stream ended without StreamDone")
            except Exception as exc:
                if not saw_event:
                    local_err = _local_connection_error(exc, self.provider)
                    if local_err is not None:
                        logger.warning(
                            "Local provider unreachable, failing fast: %s",
                            local_err,
                        )
                        raise local_err
                if not saw_event and _is_transient_error(exc) and attempt < self.max_retries:
                    delay = self.retry_backoff * (2 ** attempt)
                    logger.warning(
                        "Transient streaming error (attempt %d/%d), retrying in %.1fs: %s",
                        attempt + 1, self.max_retries, delay, exc,
                    )
                    self._emit(
                        events.Retry(
                            attempt=attempt + 1,
                            max_retries=self.max_retries,
                            delay=delay,
                            error=str(exc),
                        ),
                        turn_id,
                        on_event,
                        trace_store,
                    )
                    if delay > 0:
                        time.sleep(delay)
                    attempt += 1
                    continue
                if text_parts:
                    return Response(
                        text="".join(text_parts),
                        model=self.provider.name,
                        usage=TokenUsage(),
                        tool_calls=[],
                    )
                raise

    def _run_llm_loop(
        self,
        messages: list[Message],
        tool_specs: list[ToolSpec] | None,
        on_delta: StreamCallback | None,
        turn_id: str,
        on_event: EventCallback | None,
        trace_store: TraceStore | None,
    ) -> tuple[Response, int, list[str], bool]:
        """Run the provider/tool-call loop and return the final response.

        The returned boolean indicates whether the turn used a streaming
        provider path, so the caller knows not to deliver a final single delta.
        """
        warnings: list[str] = []
        tool_calls_made = 0
        rounds_used = 0
        iterations = 0
        caps = self.provider.capabilities()
        has_native_stream_events = (
            type(self.provider).stream_events is not LLMProvider.stream_events
        )
        use_tools = tool_specs is not None
        try_streaming = (
            (on_delta is not None or on_event is not None)
            and caps.streaming
            and (not use_tools or has_native_stream_events)
        )

        if try_streaming:
            response = self._stream_events_with_retry(
                messages, tool_specs, turn_id, on_event, trace_store, on_delta,
            )
        else:
            response = self._complete_with_retry(
                messages, tool_specs, turn_id, on_event, trace_store,
            )

        while rounds_used < self.max_tool_rounds and iterations < _HARD_ITERATION_CAP:
            iterations += 1
            if not response.tool_calls:
                break

            round_has_side_effect = False
            for tc in response.tool_calls:
                start = time.perf_counter()
                self._emit(
                    events.TurnToolCallStart(
                        call_id=tc.id,
                        tool_name=tc.name,
                        arguments=tc.arguments,
                    ),
                    turn_id,
                    on_event,
                    trace_store,
                )
                result = self.registry.dispatch(tc)
                duration_ms = int((time.perf_counter() - start) * 1000)
                self._emit(
                    events.TurnToolCallDone(
                        call_id=tc.id,
                        tool_name=tc.name,
                        result=result,
                        duration_ms=duration_ms,
                    ),
                    turn_id,
                    on_event,
                    trace_store,
                )
                tool_calls_made += 1
                spec = self.registry.get_spec(tc.name)
                # Unknown tools default to side-effecting (conservative).
                if spec is None or spec.side_effect:
                    round_has_side_effect = True
                messages.append(Message(
                    role="assistant",
                    content=f"[tool_call: {tc.name}({tc.arguments})]",
                ))
                messages.append(Message(role="tool", content=result))

            # Read-only rounds (e.g. only recall) don't consume the budget.
            if round_has_side_effect:
                rounds_used += 1

            try:
                if try_streaming:
                    response = self._stream_events_with_retry(
                        messages,
                        tool_specs,
                        turn_id,
                        on_event,
                        trace_store,
                        on_delta,
                    )
                else:
                    response = self._complete_with_retry(
                        messages, tool_specs, turn_id, on_event, trace_store,
                    )
            except Exception as exc:
                if _local_connection_error(exc, self.provider) is not None:
                    raise
                logger.warning("Follow-up provider call failed after retries: %s", exc)
                warnings.append(_PROVIDER_UNREACHABLE_MSG)
                break

        return response, tool_calls_made, warnings, try_streaming

    def run_turn(
        self,
        task_input: str,
        on_delta: StreamCallback | None = None,
        on_event: EventCallback | None = None,
        trace_store: TraceStore | None = None,
    ) -> TurnResult:
        """Execute a single conversational turn.

        1. Assemble context from memory.
        2. Build the prompt.
        3. Call the provider (with tool specs if supported), retrying transient
           errors with backoff.
        4. Dispatch any tool calls via the registry; loop up to
           *max_tool_rounds* (read-only calls are exempt from the count).
        5. Write observations to memory.
        6. Enqueue deferred jobs (importance scoring).
        7. Return a :class:`TurnResult`.

        If *on_delta* is provided it is called with incremental text as the
        final answer is produced. If *on_event* is provided it is called with
        every lifecycle :class:`events.TurnEvent`. If *trace_store* is provided
        the turn is persisted.
        """
        _on_event = on_event if on_event is not None else self._on_event
        _trace_store = trace_store if trace_store is not None else self._trace_store
        turn_id = new_turn_id()
        warnings: list[str] = []
        degraded = False
        tool_calls_made = 0

        try:
            self._emit(
                events.TurnStart(turn_id=turn_id, task_input=task_input),
                turn_id,
                _on_event,
                _trace_store,
            )
            if _trace_store is not None:
                _trace_store.start_trace(turn_id, task_input)

            self._emit(
                events.ContextStart(),
                turn_id,
                _on_event,
                _trace_store,
            )
            ctx = self.memory.assemble_context(task_input)
            self._emit(
                events.ContextDone(
                    record_count=len(ctx),
                    token_estimate=sum(_estimate_tokens(r.text) for r in ctx),
                ),
                turn_id,
                _on_event,
                _trace_store,
            )

            caps = self.provider.capabilities()
            use_tools = caps.function_calling
            tool_specs = self.registry.specs() if use_tools else None
            if not use_tools:
                warnings.append(
                    f"Provider '{self.provider.name}' does not support function-calling; "
                    "memory tools not advertised. Context retrieval is harness-driven."
                )

            messages = self.prompt_builder.build(
                system=self.system,
                tools=tool_specs,
                context=ctx,
                task_input=task_input,
            )
            self._emit(
                events.PromptBuilt(
                    message_count=len(messages),
                    token_estimate=sum(
                        _estimate_tokens(m.content) for m in messages
                    ),
                ),
                turn_id,
                _on_event,
                _trace_store,
            )

            try:
                (
                    response,
                    tool_calls_made,
                    loop_warnings,
                    was_streaming,
                ) = self._run_llm_loop(
                    messages,
                    tool_specs,
                    on_delta,
                    turn_id,
                    _on_event,
                    _trace_store,
                )
                warnings.extend(loop_warnings)
            except Exception as exc:
                if _local_connection_error(exc, self.provider) is not None:
                    raise
                logger.warning("Provider unreachable after retries: %s", exc)
                self._emit(
                    events.Degraded(reason="Falling back to local memory"),
                    turn_id,
                    _on_event,
                    _trace_store,
                )
                warnings.append(_PROVIDER_UNREACHABLE_MSG)
                results = self.memory.recall(task_input)
                if results:
                    text = (
                        f"{_PROVIDER_UNREACHABLE_MSG}:\n"
                        + "; ".join(r["text"] for r in results)
                    )
                else:
                    text = f"{_PROVIDER_UNREACHABLE_MSG} (no cached results found)."
                response = Response(
                    text=text,
                    model=self.provider.name,
                    usage=TokenUsage(),
                    tool_calls=[],
                )
                degraded = True

                self._emit(
                    events.TurnDone(
                        text=response.text,
                        tool_calls_made=0,
                        degraded=True,
                        model=response.model,
                        usage=response.usage,
                    ),
                    turn_id,
                    _on_event,
                    _trace_store,
                )
                if _trace_store is not None:
                    _trace_store.finish_trace(turn_id, "completed")
                return TurnResult(
                    text=text,
                    warnings=warnings,
                    tool_calls_made=0,
                    degraded=True,
                    model=response.model,
                    usage=response.usage,
                )

            # Uniform streaming interface: if this was a non-streaming path,
            # deliver the final answer as one delta so callers that passed
            # on_delta render through the same path.
            if not was_streaming and response.text:
                self._emit(
                    events.TurnTextDelta(text=response.text),
                    turn_id,
                    _on_event,
                    _trace_store,
                )
                if on_delta is not None:
                    on_delta(response.text)

            # Write observations
            records = self.memory.write_observations(response)
            self._emit(
                events.MemoryWrite(record_ids=[r.id for r in records]),
                turn_id,
                _on_event,
                _trace_store,
            )

            # Enqueue deferred jobs
            for rec in records:
                self.queue.enqueue(Job(
                    type=JobType.IMPORTANCE_SCORING,
                    payload={"record_id": rec.id},
                ))
                self._emit(
                    events.JobEnqueue(
                        job_type=JobType.IMPORTANCE_SCORING.value,
                        payload={"record_id": rec.id},
                    ),
                    turn_id,
                    _on_event,
                    _trace_store,
                )

            self._emit(
                events.TurnDone(
                    text=response.text,
                    tool_calls_made=tool_calls_made,
                    degraded=degraded,
                    model=response.model,
                    usage=response.usage,
                ),
                turn_id,
                _on_event,
                _trace_store,
            )
            if _trace_store is not None:
                _trace_store.finish_trace(turn_id, "completed")
            return TurnResult(
                text=response.text,
                warnings=warnings,
                tool_calls_made=tool_calls_made,
                degraded=degraded,
                model=response.model,
                usage=response.usage,
            )
        except Exception as exc:
            self._emit(
                events.Error(message=str(exc)),
                turn_id,
                _on_event,
                _trace_store,
            )
            if _trace_store is not None:
                _trace_store.finish_trace(turn_id, "failed")
            raise
