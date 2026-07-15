# PRD: LLM engagement visibility / transparency

Status: **approved 2026-07-14**
Interview record: [DRAFT.md](DRAFT.md)

## 1. Problem Statement

Rickshaw currently shows a single generic spinner (`Thinking…` / `Streaming…`) while a turn is in progress. Behind that spinner the orchestrator performs a long chain of work: embedding the user query, searching memory, ranking context, building the prompt, calling the LLM, parsing and dispatching tool calls, retrying transient errors, writing observations to memory, and enqueuing deferred jobs. The user cannot see which of these is happening, how many tool rounds are running, whether retries are occurring, or why a response is delayed. This opacity makes the tool feel slow and unpredictable, and it makes debugging provider or memory issues unnecessarily hard.

## 2. Proposed Solution

Expose the full turn lifecycle as a stream of **structured events** from the `Orchestrator`, render them live in the TUI, and persist a per-turn trace in SQLite. The user gets:

1. A **live spinner** that shows the current phase (`Assembling context…` → `Calling LLM…` → `Calling recall…` → `Retry 1/2…` → `Streaming answer…`).
2. A **collapsible trace block** under every assistant turn, collapsed by default, showing a brief summary. The user navigates between turns with `Ctrl+Up` / `Ctrl+Down` and expands/collapses the selected turn's trace with `Ctrl+O`.
3. **Full transparency** inside the expanded trace: all lifecycle events, including tool name + full arguments + full results, reasoning/thinking tokens, and raw request/response metadata. Reasoning and raw request/response details are hidden by default inside the collapsed block and only visible once the trace is expanded.
4. **Persisted trace history** in the same SQLite database as memory (`rickshaw_memory.db`), so traces survive `/clear` and can be queried later.
5. **Streaming tool events** where the provider supports them (`ToolCallStart`, `ToolCallDelta`, `ToolCallEnd`, `TextDelta`), with a fallback to complete+batched tool calls for providers that cannot stream tool events.

### Affected modules

- `rickshaw/orchestrator.py` — emit `TurnEvent`s; drive streaming tool-event path and fallback.
- `rickshaw/providers/base.py` — extend `LLMProvider.stream()` to yield structured `StreamEvent`s (or keep `Iterator[str]` with a new optional `stream_events()` method).
- `rickshaw/tui.py` — consume events, update spinner, render trace blocks, handle `Ctrl+Up`/`Ctrl+Down`/`Ctrl+O` navigation and expansion.
- `rickshaw/memory/store.py` or new `rickshaw/trace_store.py` — persist traces in SQLite.
- `rickshaw/events.py` (new) — `TurnEvent` / `StreamEvent` dataclasses/Pydantic models.
- `tests/test_orchestrator.py`, `tests/test_tui.py`, `tests/test_trace_store.py` — new coverage.
- `README.md` — document keybindings and trace behavior.

## 3. User Journeys

**J1 — Simple question, no tools (happy path).**
User submits a prompt. The TUI spinner updates through `Assembling context…` → `Calling LLM…` → `Streaming answer…` and tokens appear as they are generated. When the turn completes, a collapsed trace block appears under the assistant message with a summary like `2 events · 0 tool calls · 0.8s`. The user can navigate to it with `Ctrl+Up` and expand with `Ctrl+O` to see the `context_assembled` and `llm_call_done` events plus the token usage.

**J2 — Memory recall (single tool call).**
User asks a question that causes the model to call `recall(query="…")`. The spinner changes to `Calling recall…` and the trace block (when expanded) shows the tool call with full arguments and the result summary. The final answer then streams. The collapsed summary shows `4 events · 1 tool call · 1.4s`.

**J3 — Multi-round tool loop (recall then remember).**
The model calls `recall`, then `remember`, then answers. The trace shows each `llm_call_start`, `tool_call_start`, `tool_call_done`, and the final `llm_call_done` with streaming text. The user sees the live phase changes in the spinner and the full sequence in the expanded trace.

**J4 — Retry then success (transient provider error).**
The provider returns a 429. The spinner shows `Retry 1/2 in 1.0s`, then `Retry 2/2 in 2.0s`, then `Calling LLM…`. The expanded trace includes the full error messages for each retry attempt and the successful final response. The collapsed summary shows `5 events · 2 retries · 2.1s`.

**J5 — Provider unreachable (degraded fallback).**
The provider fails after all retries. The trace shows each retry, the final error, and a `degraded` event with `Falling back to local memory`. The TUI shows the existing `DEGRADED` banner and the local memory results. The collapsed summary shows `6 events · 3 retries · degraded · 3.4s`.

**J6 — User interrupts during a tool loop.**
User presses `Esc` while the model is in a tool-call loop. The orchestrator cancels the in-flight work. The trace block contains all events emitted up to the interruption point, and the TUI shows `(interrupted)`. The user can later expand the trace to see what had happened before the cancel.

**J7 — Non-TUI / programmatic use.**
A script calls `Orchestrator.run_turn(text, on_event=print)`. The orchestrator calls `on_event` with every `TurnEvent` in order. The script can render, log, or store the trace itself. No TUI is required.

**J8 — Reviewing an older trace.**
User presses `Ctrl+Up` to move the selection highlight up through the transcript. Each turn (user message or assistant message) becomes the selected turn. When the user lands on an assistant turn, `Ctrl+O` expands its trace. `Esc` or `Ctrl+Down` past the newest turn returns focus to the prompt.

**J9 — `/clear` and persistence.**
User runs `/clear`. The transcript is cleared, but the trace rows remain in `rickshaw_memory.db`. On restart, the TUI can optionally re-render traces for the previous session; this PRD does not require that, but the data must survive `/clear`.

**J10 — Provider supports streaming but not tool-event streaming.**
The provider streams text but returns complete tool-call objects. The orchestrator falls back to `complete()` for rounds that contain tool calls, emits the `tool_call_start`/`tool_call_done` events after the response is received, and then streams the final answer if the last response has no tool calls. The user notices a brief pause during tool rounds, but still sees the trace.

## 4. Constraints

- The TUI must remain keyboard-first; mouse-only interactions are not required.
- Existing keybindings (`Esc` to interrupt, `Ctrl+C` to quit, `Up`/`Down` for prompt history) must not be broken.
- `Ctrl+Up` / `Ctrl+Down` / `Ctrl+O` are reserved for trace navigation and expansion.
- Providers that cannot stream must keep working through the `complete()` fallback.
- No API keys, credentials, or raw response bodies are logged to persistent storage unless explicitly included by the user (this PRD explicitly includes raw request/response metadata and full error messages in the trace).
- Trace persistence must not block the hot path; events are emitted live but persisted asynchronously.
- Backward compatibility: existing `Orchestrator.run_turn(text, on_delta=...)` callers continue to work; the `on_event` callback is optional.

## 5. Decisions Log

| # | Decision | Alternatives rejected | Why |
|---|---|---|---|
| D1 | Scope: TUI + orchestrator events + persisted trace | TUI only; TUI + events without persistence | User wants the trace available outside the live session and reusable by programmatic callers |
| D2 | Surface all phases, including reasoning and raw request/response metadata | Tool calls and errors only; tool calls + retries; all phases except reasoning | User wants full transparency; reasoning and raw metadata are hidden by default inside the collapsed trace |
| D3 | Expandable per-turn trace blocks in the transcript, hidden by default | Inline meta lines; separate side panel; status bar only; `/trace` command only | Trace stays with its turn, can be reviewed later, and does not clutter the main transcript |
| D4 | Tool name + full arguments + full results | Tool name only; redacted arguments; args + summary | Maximum transparency; user accepted the privacy risk |
| D5 | Reasoning/thinking tokens and raw request/response metadata hidden by default, exposed inside the expanded trace | Always shown; shown in a separate panel; never shown | Reasoning can be long and noisy; raw metadata may contain credentials; the user can still inspect it on demand |
| D6 | Persist traces in `rickshaw_memory.db` | Transcript-only; separate log file | Reuses existing persistence layer; survives `/clear` and can be queried |
| D7 | Trace blocks always present, collapsed; no global toggle | Global toggle; always expanded; hidden by default | The user can always see that a trace exists and expand it, but the transcript stays clean |
| D8 | Navigate turns with `Ctrl+Up`/`Ctrl+Down`; expand selected turn with `Ctrl+O` | Mouse; `Alt+Up`/`Alt+Down`; `Ctrl+J`/`Ctrl+K` | User prefers keyboard and `Ctrl+Up`/`Ctrl+Down` are intuitive for vertical navigation |
| D9 | Stream tool events when provider supports it; fallback to complete+batched tool calls | Stream final answer only after tool loop; no streaming for tool loops; require all providers to emit tool events | Best UX for supported providers; pragmatic fallback preserves compatibility |
| D10 | Show per-retry events with full error messages | Final summary only; per-retry without messages | Full diagnostic value; user accepted the noise and potential leak of URL/token fragments |
| D11 | Emit events live, persist asynchronously | Synchronous persistence; batch and persist at turn end | Immediate TUI feedback without blocking the turn pipeline |
| D12 | Update live spinner with current phase | Concise short label; keep generic spinner | User wants immediate, clear feedback about what is happening right now |
| D13 | Collapsed trace shows a brief summary line | Nothing; only expand hint; last phase only | Summary gives useful context at a glance and encourages expansion |

## 6. Out of Scope

- Mouse support for trace navigation and expansion.
- Exporting traces to files or external systems.
- Replaying past sessions in the TUI on startup (traces are persisted, but restoring the transcript from them is not required).
- Editing traces or events from the UI.
- Filtering or searching traces.
- A separate debug console / raw wire log outside the per-turn trace block.
- Changing the fundamental prompt-building or memory-ranking algorithms; this PRD only surfaces what already happens.
