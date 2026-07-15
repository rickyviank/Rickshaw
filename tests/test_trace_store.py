"""Tests for rickshaw.trace_store."""

from __future__ import annotations

import pytest

from rickshaw.events import (
    ContextDone,
    TurnDone,
    TurnStart,
    TurnTextDelta,
    TurnToolCallDone,
    TurnToolCallStart,
)
from rickshaw.trace_store import TraceStore, new_turn_id


@pytest.fixture
def trace_store(tmp_path):
    """A file-backed TraceStore that is closed after each test."""
    store = TraceStore(tmp_path / "trace.db")
    try:
        yield store
    finally:
        store.close()


def test_start_emit_finish_persist_trace(trace_store):
    """start_trace/emit/finish_trace persist a complete trace asynchronously."""
    turn_id = new_turn_id()
    trace_store.start_trace(turn_id, "hello")
    trace_store.emit(turn_id, TurnStart(turn_id=turn_id, task_input="hello"))
    trace_store.emit(turn_id, ContextDone(record_count=2, token_estimate=5))
    trace_store.emit(
        turn_id,
        TurnDone(text="done", tool_calls_made=0, degraded=False, model="m"),
    )
    trace_store.finish_trace(turn_id)

    assert trace_store.flush()
    trace = trace_store.get_trace(turn_id)

    assert trace is not None
    assert trace["turn_id"] == turn_id
    assert trace["task_input"] == "hello"
    assert trace["status"] == "completed"
    assert [e["type"] for e in trace["events"]] == [
        "turn_start",
        "context_done",
        "turn_done",
    ]


def test_flush_writes_queued_events(trace_store):
    """flush waits for all queued events to hit the database."""
    turn_id = new_turn_id()
    trace_store.start_trace(turn_id, "flush test")
    for i in range(10):
        trace_store.emit(turn_id, TurnTextDelta(text=f"chunk {i}"))
    trace_store.finish_trace(turn_id)

    assert trace_store.flush()
    trace = trace_store.get_trace(turn_id)

    assert len(trace["events"]) == 10
    assert [e["text"] for e in trace["events"]] == [f"chunk {i}" for i in range(10)]


def test_get_trace_returns_events_in_order(trace_store):
    """Events are returned in emission order."""
    turn_id = new_turn_id()
    trace_store.start_trace(turn_id, "order test")
    trace_store.emit(turn_id, TurnStart(turn_id=turn_id, task_input="order test"))
    trace_store.emit(
        turn_id,
        TurnToolCallStart(
            call_id="1",
            tool_name="recall",
            arguments={"q": "x"},
        ),
    )
    trace_store.emit(
        turn_id,
        TurnToolCallDone(
            call_id="1",
            tool_name="recall",
            result="ok",
            duration_ms=50,
        ),
    )
    trace_store.emit(
        turn_id,
        TurnDone(text="ok", tool_calls_made=1, degraded=False, model="m"),
    )
    trace_store.finish_trace(turn_id)

    assert trace_store.flush()
    trace = trace_store.get_trace(turn_id)

    types = [e["type"] for e in trace["events"]]
    assert types == ["turn_start", "tool_call_start", "tool_call_done", "turn_done"]
    assert trace["events"][1]["arguments"] == {"q": "x"}
    assert trace["events"][2]["duration_ms"] == 50


def test_close_stops_worker_cleanly(tmp_path):
    """close flushes queued work and stops the background thread."""
    store = TraceStore(tmp_path / "close.db")
    turn_id = new_turn_id()
    store.start_trace(turn_id, "close test")
    store.emit(
        turn_id,
        TurnDone(text="ok", tool_calls_made=0, degraded=False, model="m"),
    )
    store.finish_trace(turn_id)

    store.close()

    assert not store._thread.is_alive()
    # The database file is still readable after the worker exits.
    trace = store.get_trace(turn_id)
    assert trace is not None
    assert trace["status"] == "completed"


def test_multiple_turns_do_not_mix_events(trace_store):
    """Events from concurrent turns stay in their own traces."""
    t1 = new_turn_id()
    t2 = new_turn_id()

    trace_store.start_trace(t1, "turn one")
    trace_store.start_trace(t2, "turn two")

    trace_store.emit(t1, TurnTextDelta(text="one-a"))
    trace_store.emit(t2, TurnTextDelta(text="two-a"))
    trace_store.emit(t1, TurnTextDelta(text="one-b"))
    trace_store.emit(
        t2,
        TurnDone(text="two", tool_calls_made=0, degraded=False, model="m"),
    )
    trace_store.emit(
        t1,
        TurnDone(text="one", tool_calls_made=0, degraded=False, model="m"),
    )

    trace_store.finish_trace(t1)
    trace_store.finish_trace(t2)

    assert trace_store.flush()
    one = trace_store.get_trace(t1)
    two = trace_store.get_trace(t2)

    assert [e["text"] for e in one["events"] if e["type"] == "text_delta"] == [
        "one-a",
        "one-b",
    ]
    assert [e["text"] for e in two["events"] if e["type"] == "text_delta"] == ["two-a"]


def test_memory_store_works_for_single_connection_lifecycle(tmp_path):
    """A file-backed store with a short-lived path can start, emit, flush, and read."""
    # ``:memory:`` connections are private to each sqlite3 connection, so the
    # worker thread and a synchronous read cannot share one. A temp file still
    # exercises the same async lifecycle across connections.
    store = TraceStore(tmp_path / "trace_memory.db")
    try:
        turn_id = new_turn_id()
        store.start_trace(turn_id, "memory test")
        store.emit(turn_id, TurnStart(turn_id=turn_id, task_input="memory test"))
        store.emit(
            turn_id,
            TurnDone(text="ok", tool_calls_made=0, degraded=False, model="m"),
        )
        store.finish_trace(turn_id)

        assert store.flush()
        trace = store.get_trace(turn_id)

        assert trace is not None
        assert trace["task_input"] == "memory test"
        assert [e["type"] for e in trace["events"]] == ["turn_start", "turn_done"]
    finally:
        store.close()
