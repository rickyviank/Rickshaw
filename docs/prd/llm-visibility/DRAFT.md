# DRAFT: LLM engagement visibility / transparency

> Working document for the spec interview. Updated after each user decision.
> Status: **interview complete — synthesized into [PRD.md](PRD.md), awaiting sign-off**

## Request (verbatim)

"I feel that when Rickshaw is engaging with LLM, it's not surfacing to the user what is actually happening, and it can get quite opaque for the users."

## Current state (codebase findings)

A single turn hides a long chain of work behind a generic spinner:

- `Orchestrator.run_turn()` (`rickshaw/orchestrator.py`):
  1. Assembles context from memory (`MemoryService.assemble_context`).
  2. Builds the prompt within a token budget (`PromptBuilder.build`).
  3. Calls the provider (possibly with tool specs). Retries transient errors with exponential backoff.
  4. Dispatches tool calls in a loop (up to `_MAX_TOOL_ROUNDS=3`).
  5. Writes observations to memory and enqueues deferred jobs.
- `TUI._start_turn()` / `_run_turn()` (`rickshaw/tui.py`):
  - Shows a spinner: `Thinking…` or `Streaming…` plus elapsed seconds, estimated tokens, and `esc to interrupt`.
  - Final meta line shows `N tok`, `N tool calls`, or a `DEGRADED` banner if the provider was unreachable.
- Streaming only exists for the final answer text (`TextDelta`/`StreamDone` path in `rickshaw_ai/streaming.py`). Tool-call rounds, retries, context assembly, and memory operations are completely invisible to the user.
- The only failure visibility is a generic `Error: {exc}` or the degraded fallback banner.

## Gaps found

1. **No visibility into the turn pipeline.** The user sees `Thinking…` while the system is: embedding the query, searching memory, ranking results, building the prompt, calling the LLM, parsing tool calls, dispatching tools, and re-calling the LLM.
2. **Tool calls are opaque.** When the model calls `remember`/`recall`/`forget` (or future non-memory tools), the user does not see the tool name, arguments, or the fact that a second/final LLM call is pending.
3. **Retries are hidden.** Transient provider errors trigger exponential backoff; the user just sees the spinner keep spinning.
4. **No streaming during tool-call loops.** The final answer is delivered as a single delta after all tool rounds complete, even when the provider supports streaming.
5. **Status bar is static.** It shows provider/model/effort/tokens/price but not the live phase of the turn (e.g., `recalling`, `calling remember(...)`, `retry #2`).
6. **Memory operations are silent.** `write_observations` and deferred job enqueueing happen after the answer is shown.
7. **Reasoning/thinking content is discarded.** Providers that expose chain-of-thought/reasoning tokens do not surface them in the TUI.
8. **No per-turn log or trace.** After the turn completes, there is no way to inspect what happened.

## Best-guess approach (to be confirmed)

Introduce a small set of **turn lifecycle events** emitted by the `Orchestrator` and consumed by the TUI to update the status indicator and optionally append lightweight transcript lines. Keep the TUI minimal; do not turn it into a debug console. Events should be structured enough to support future CLI/programmatic consumers.

## Assumptions (to be interviewed)

- **A1. Scope**: Is this TUI-only, or should the same visibility events be available to the CLI and to programmatic callers of `Orchestrator`?  
  **RESOLVED: TUI + orchestrator events + persisted trace.**  
- **A2. Event granularity**: Which of these should be surfaced? (context assembly, prompt-build summary, LLM call start/end, tool-call start/end, retry attempts, memory write, fallback/degradation, reasoning/thinking deltas, deferred jobs, token usage.)  
  **RESOLVED: All phases are visible, including reasoning/thinking tokens and raw request/response metadata. Reasoning and raw request/response details are hidden by default and expandable with `Ctrl+O`.**  
- **A3. UX pattern**: Should activity appear in the spinner text, as expandable inline lines, in a separate transcript panel, or only in a `/trace` transcript command?  
  **RESOLVED: Each turn gets an expandable trace block in the transcript, collapsed by default, expanded with `Ctrl+O`.**  
- **A4. Tool call transparency**: Should the transcript show tool names and arguments? What about tool results? (Privacy/security concern: arguments may contain sensitive text.)  
  **RESOLVED: Show tool name, full arguments, and full raw results. User accepted the privacy risk for full transparency.**  
- **A5. Reasoning content**: Should thinking/reasoning be shown to the user? If so, inline, collapsed, or in a separate section?  
  **RESOLVED: Reasoning/thinking tokens and raw request/response metadata are shown but hidden by default; the user expands them with `Ctrl+O`.**  
- **A6. Persistence**: Should the turn trace be part of the transcript history (so `/clear` removes it) or a separate, persisted log?  
  **RESOLVED: Persist traces in the same SQLite database as memory (rickshaw_memory.db). TUI renders recent ones in the transcript; the full history survives `/clear` and can be queried later.**  
- **A7. Configurability**: Should this be toggleable (e.g., `/verbose` or `--quiet`) or always on?  
  **RESOLVED: Trace blocks are always present for every turn, collapsed by default. User navigates between turns with Ctrl+Up/Ctrl+Down and expands/collapses the selected turn's trace with Ctrl+O. No global toggle.**  
- **A8. Streaming and tool-call loops**: Should we stream the final answer even when a multi-round tool loop was needed? The current architecture cannot stream tool calls; this would require a larger change.  
  **RESOLVED: Stream whenever the provider supports it, including tool-event streaming (ToolCallStart, ToolCallDelta, ToolCallEnd, TextDelta). This requires extending the provider streaming interface to emit structured `StreamEvent`s instead of plain text.**  
- **A9. Degeneration/fallback**: Is the current `DEGRADED` banner enough, or should the user see the exact retry/fallback steps (e.g., "Retry 1/2 in 1s", "Falling back to local memory")?  
  **RESOLVED: Show per-retry events with full error messages and the final fallback step. User accepted the noise and potential leak of URL/token fragments in error text.**  
- **A10. Performance**: Are there concerns about event overhead? The hot path is already chatty; we should avoid heavy serialization.  
  **RESOLVED: Emit events live for immediate TUI rendering, but persist the trace to SQLite asynchronously (background thread/worker) to avoid blocking the turn pipeline.**  
- **A11. Live spinner**: Should the spinner show the current phase or remain generic?  
  **RESOLVED: Update the spinner with the current phase (e.g., 'Assembling context…' → 'Calling LLM…' → 'Calling recall…' → 'Retry 1/2…' → 'Streaming answer…').**
- **A12. Collapsed trace summary**: What should the collapsed trace block display?  
  **RESOLVED: A brief summary line, e.g., '3 events · 2 tool calls · 1 retry · 1.2s'.**
- **A13. Tool-event streaming fallback**: What should happen if a provider supports streaming but not tool-event streaming?  
  **RESOLVED: Fall back to complete+batched tool calls: collect the full response, parse tool calls, emit the tool-call events at the end of the LLM round, then continue. The user sees a pause, then a burst of tool events, then the final answer streams if supported.**

## User journeys to cover

- **J1. Simple question, no tools.** User asks a question; sees the model is "thinking", then the answer streams.  
- **J2. Memory recall.** User asks something that triggers the model to call `recall`; the TUI shows the tool call, then the final answer.  
- **J3. Multi-round tool loop.** The model calls `recall`, then `remember`, then answers; the TUI shows the sequence of tool calls.  
- **J4. Retry then success.** Provider returns a transient error; the TUI shows retry 1/2, then succeeds.  
- **J5. Provider unreachable.** Provider fails after retries; the TUI shows retries and then the degraded fallback banner or local memory results.  
- **J6. User interrupts.** User presses Esc while a tool loop is in progress; the TUI shows the interruption cleanly.  
- **J7. Non-TUI / programmatic use.** A script calls `Orchestrator.run_turn()` and wants to log or surface events.

## Rough proposed changes

- `rickshaw/orchestrator.py`: Add an optional `on_event` callback (or extend `StreamCallback`) that emits lifecycle events (`TurnEvent` enum/union). Events cover: `context_start`, `context_done`, `prompt_built`, `llm_call_start`, `llm_call_done`, `tool_call_start`, `tool_call_done`, `retry`, `degraded`, `memory_write`, `job_enqueue`, `turn_done`, `error`.
- `rickshaw/tui.py`: Wire `on_event` to update the spinner text and optionally append inline meta lines to the transcript. Keep the spinner concise, but show the active phase and tool call name.
- `rickshaw/providers/base.py` (or new `rickshaw/events.py`): Define the `TurnEvent` dataclasses/pydantic models.
- `tests/test_orchestrator.py`, `tests/test_tui.py`: Add tests for event emission and TUI rendering.
- `README.md` / docs: Document the visibility behavior and any toggle commands.
