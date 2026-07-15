# DRAFT: Human-readable turn traces

> Working document for the spec interview. Updated after each user decision.
> Status: **interview complete — synthesized into [PRD.md](PRD.md), approved**

## Request (verbatim)

"I feel that the current traces are not human readable, but I can't quite put my finger as to how exactly it should look like yet. any thoughts?"

## Current state (codebase findings)

Rickshaw already records a per-turn trace (see PRD `llm-visibility`). The data model and persistence are in place:

- `rickshaw/events.py` defines `TurnEvent` types: `TurnStart`, `ContextStart`, `ContextDone`, `PromptBuilt`, `LLMCallStart`, `LLMCallDone`, `TurnToolCallStart`, `TurnToolCallDone`, `Retry`, `Degraded`, `MemoryWrite`, `JobEnqueue`, `TurnTextDelta`, `TurnThinkingDelta`, `TurnDone`, `Error`.
- `rickshaw/trace_store.py` persists each turn and its events to SQLite (`rickshaw_memory.db`) asynchronously.
- `rickshaw/tui.py` renders a collapsed `TraceBlock` under each assistant turn.
  - Collapsed summary: e.g. `7 events · 1 tool call · 0 retries · 1.2s`.
  - Expanded details: a raw JSON dump of every event field, indented two spaces, prefixed by `--- {type} ---`.

The current expanded trace looks roughly like:

```text
--- turn_start ---
turn_id:
  "abc123"
task_input:
  "what do I like?"

--- context_done ---
record_count:
  3
token_estimate:
  120

--- llm_call_start ---
attempt:
  1
model:
  "openai"
```

## Gaps found

1. **Raw JSON is verbose and repetitive.** Every event repeats the full Pydantic field dump. Keys such as `record_count`, `token_estimate`, `attempt` are printed even when the value is self-explanatory.
2. **No natural language framing.** A user sees `tool_call_start` and a JSON blob instead of a sentence like "Called tool `recall(query='what do I like?')`".
3. **Large payloads are not summarized.** Tool-call arguments and results, thinking deltas, and raw error strings can be hundreds of lines, making the trace wall-of-text.
4. **No visual hierarchy or grouping.** Multi-round tool loops, retries, and LLM calls appear as a flat list, so the causal sequence is hard to follow.
5. **Thinking/reasoning tokens and raw metadata are mixed with operational events.** The expanded trace does not distinguish "what Rickshaw did" from "what the model thought".
6. **Only the TUI is affected for now.** The SQLite `get_trace()` API still returns the raw JSON list; there is no CLI command or exported view.

## Best-guess approach (to be confirmed)

Keep the existing event schema and persistence unchanged; focus on **rendering** the same events in a more human-readable way inside the TUI expanded trace block. Introduce a small presentation layer that maps each `TurnEvent` to a short, natural-language line, with optional detail folding for large payloads. Consider a CLI/export view later if needed.

## Assumptions (to be interviewed)

- **A1. Scope and surface:** Is this about the TUI expanded trace block only, or also CLI/API?  
  **RESOLVED: TUI expanded trace block only. Reusable formatting or CLI commands are explicitly out of scope for this PRD.**
- **A2. "Human-readable" style / core fix:** The user believes the biggest win is **grouping consecutive `text_delta` and `thinking_delta` events into continuous blocks** instead of dumping each chunk as a separate raw JSON event. This is the primary direction, with other formatting improvements secondary.  
  **RESOLVED: group streamed deltas into continuous blocks.**
- **A3. Display of grouped text/thinking deltas:** Should the merged blocks be shown in full, truncated, summarized, or collapsible?  
  **RESOLVED: show the full merged text block by default, but if it exceeds a terminal-height-aware cap (30% of the visible height), render a capped preview with a "… (+N chars/lines)" hint and an expand control that reveals the full text in a scrollable region.**
- **A4. Display of grouped thinking deltas:** Should thinking/reasoning be merged with text, shown separately, or hidden by default?  
  **RESOLVED: show merged thinking deltas in a separate "Thinking" block from the answer text, with the same cap/expand behavior.**
- **A5. Non-delta event formatting:** Should the other events (tool calls, retries, context assembly, LLM calls) be converted to human-readable lines, or left as raw JSON?  
  **RESOLVED: compact bracket-label summaries with raw JSON available via per-event expand. Tool calls and LLM calls are rendered as one combined line after they complete: e.g. `[tool] recall({...}) → <result> (45ms)` and `[llm] openai (attempt 1) → gpt-4o, 210 tokens`.**
- **A6. Event grouping:** Should the trace be a flat chronological list, or grouped by phase?  
  **RESOLVED: flat chronological list with per-event friendly summaries.**
- **A7. Timestamps and elapsed time:** Should each event or phase show a timestamp/duration?  
  **RESOLVED: prefix each event with a relative timestamp since turn start, and show tool-call durations where available.**
- **A8. Formatting richness:** Should the trace use Rich markup inside Textual?  
  **RESOLVED: full color coding — distinct colors per event type, bold labels, code spans for names/arguments.**
- **A9. Raw data access:** Should the human-readable view still expose the original raw JSON?  
  **RESOLVED: per-event expand — each event shows a friendly summary with an option to expand and see the raw JSON payload.**
- **A10. Summary line:** Should the collapsed summary stay as it is or change?  
  **RESOLVED: keep the existing structure but count grouped display lines and label them `steps`, e.g. `7 steps · 1 tool call · 0 retries · 1.2s`.**
- **A11. Backward compatibility / persistence:** Should `TraceStore.get_trace()` continue returning raw JSON?  
  **RESOLVED: keep raw JSON only; formatting is a TUI presentation concern.**
- **A12. Per-event expand interaction:** How does a user expand a single event to raw JSON?  
  **RESOLVED: inline `[+]` / `[-]` toggle on each event line. Keyboard users Tab from the prompt into the expanded trace, use arrow keys to move between toggles, and press Enter to expand/collapse; Esc returns focus to the prompt.**
- **A13. Long payload truncation:** How should long payloads be handled in the summary?  
  **RESOLVED: truncate with a hint and make the limit terminal-width aware (e.g. 50% of visible width). The full value is available by expanding the event.**
- **A14. Retry/error display:** Should the full error string appear in the summary or be hidden behind expand?  
  **RESOLVED: hide full error text in the summary; show retry count/delay (e.g. `[retry] attempt 1/2 in 1.0s`) and expose the full error by expanding the event.**
- **A15. Interrupted turns:** How should partial text deltas be handled when a turn is interrupted?  
  **RESOLVED: merge partial text/thinking deltas into a `[partial answer]` / `[partial thinking]` block so the user can see what was generated before the interrupt.**
- **A16. Empty placeholder blocks:** When a block such as `[thinking] (none)` is shown for transparency, should it count toward the collapsed step summary?  
  **RESOLVED: show the placeholder line for transparency, but do not count empty placeholders in the step total. This applies to `[answer] (empty)` as well.**
- **A17. Zero-data start events:** Events like `ContextStart` have no payload. Should they appear as their own line or be folded into their matching `Done`/`Built` event?  
  **RESOLVED: fold zero-data start events into a single line when the matching done/built event arrives, using the start event's timestamp as the line's relative time.**
- **A17a. Trace header fields:** Should the header include the turn ID?  
  **RESOLVED: no — hide the opaque turn ID from the header. The turn ID is still stored in `TraceStore` and is visible when the trace is toggled to raw JSON with `R`.**
- **A17b. Header layout:** How should task, provider/model, status, and duration be arranged?  
  **RESOLVED: two-line header. Line 1 shows the user task plus status and duration; line 2 shows provider/model. The assistant answer remains a separate `[answer]` block below the header. The `TurnStart` event is represented by the header and is not duplicated as a body line; its raw JSON is accessible through the global `R` raw toggle.**
- **A18. Global raw toggle:** Should there be a keybinding to toggle the entire expanded trace to raw JSON?  
  **RESOLVED: yes. Pressing `R` while a trace is expanded toggles all display lines between human-readable summaries and their raw JSON payloads. Pressing `R` again reverts.**
- **A19. Contextual hint line:** Should the bottom hint update when a trace is expanded to show trace-specific keys?  
  **RESOLVED: yes — the hint line becomes contextual, showing trace keys (`r raw`, `tab expand event`, `esc` to return focus) when a trace is expanded, and reverting to the default hint otherwise.**

## User journeys to cover

- **J1. Expand a completed trace to understand what happened.** User presses `Ctrl+O` on a turn and reads the expanded trace. The two-line header shows the task, status/duration, and provider/model. The body is a flat chronological list: context assembly, prompt build, LLM call, tool call, final answer, memory write, job enqueue. Each line has a relative timestamp and a bracket-label summary.
- **J2. Inspect a tool call.** User wants to see which tool was called, with what arguments, and what it returned. A tool call renders as one line: `[tool] recall({ "query": "..." }) → <result> (45ms)`. If the result is long, it is truncated with a hint; expanding the line reveals the raw `TurnToolCallStart` and `TurnToolCallDone` JSON.
- **J3. Follow a multi-round tool loop.** User sees the model call `recall`, then `remember`, then answer. Each round is a separate LLM call line and tool line in order, making the sequence easy to follow.
- **J4. Inspect retries.** A provider error shows `[retry] attempt 1/2 in 1.0s`. The full error message is hidden behind the line's `[+]` expand. After retries succeed, the trace continues with the successful LLM call.
- **J5. Review thinking/reasoning tokens.** A separate `[thinking]` block groups all thinking deltas. If the model produced no thinking, `[thinking] (none)` is shown for transparency (and not counted as a step).
- **J6. View raw JSON for debugging.** Developer expands a trace, Tabs into it, arrows to an event, and presses Enter to expand the raw JSON. Pressing `R` toggles the whole trace to raw JSON; pressing `R` again reverts.
- **J7. Interrupted turn.** User presses `Esc` while text is streaming. The trace shows a `[partial answer]` block with the text generated before interruption, followed by the final status.
- **J8. Long answer overflow.** Assistant produces a very long response. The `[answer]` block shows a preview capped at 30% of terminal height with a "… (+N lines)" hint; expanding it reveals the full text in a scrollable region.
- **J9. No thinking capability.** Model has no thinking tokens. The trace shows `[thinking] (none)` but the step count excludes it.
- **J10. Non-streaming provider.** The provider returns the full answer at once. The orchestrator emits one `TurnTextDelta`; the trace groups it into a single `[answer]` block.

## Rough proposed changes

- `rickshaw/tui.py`:
  - Replace the raw JSON dump in `TraceBlock._details_text()` with a presentation layer that renders `TurnEvent`s into bracket-label lines.
  - Add grouping of consecutive `TurnTextDelta` and `TurnThinkingDelta` events into `[answer]` / `[thinking]` / `[partial answer]` / `[partial thinking]` blocks.
  - Add a two-line header (task/status/duration, provider/model) and keep the existing collapsed summary but count grouped display lines as `steps`.
  - Add inline `[+]` / `[-]` toggles for per-event raw JSON expansion; support Tab/arrow/Enter keyboard navigation and mouse clicks.
  - Add a global `R` binding (active when a trace is expanded) to toggle the whole trace between human-readable and raw JSON.
  - Implement terminal-width-aware truncation for long payloads and terminal-height-aware cap (30%) with scrollable expand for long answer/thinking blocks.
  - Make the bottom hint line contextual, showing trace-specific keys when a trace is expanded.
  - Apply full color coding (distinct colors per event type, bold labels, code spans).
- `rickshaw/events.py`:
  - No schema changes. Optionally add small display helpers (e.g. labels, field selection) if the formatter needs them.
- `rickshaw/trace_store.py`:
  - Keep raw JSON as the canonical persistence format. `get_trace()` continues returning raw events.
- `tests/test_tui.py`:
  - Update existing trace block tests to assert on human-readable content, grouping, step counting, truncation, and raw JSON toggle.
  - Add tests for keyboard navigation, `R` raw toggle, and contextual hint.
- `README.md`:
  - Update the LLM visibility / traces section to describe the new rendering, keybindings (`Ctrl+O`, `R`, Tab/Enter), and the per-event expand behavior.
