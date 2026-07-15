# PRD: Human-readable turn traces

Status: **approved**
Interview record: [DRAFT.md](DRAFT.md)

## 1. Problem Statement

Rickshaw already captures a rich per-turn trace of the orchestrator lifecycle (`rickshaw/events.py`) and persists it in SQLite (`rickshaw/trace_store.py`). In the TUI, expanding a turn trace with `Ctrl+O` currently dumps every `TurnEvent` as raw indented JSON:

```text
--- turn_start ---
turn_id:
  "abc123"
task_input:
  "what do I like?"

--- text_delta ---
text:
  "You"

--- text_delta ---
text:
  " previously"
```

This is hard to read because:

1. **Streamed text and thinking tokens are fragmented.** A single answer can be hundreds of `text_delta` events, each rendered as a separate JSON blob.
2. **Operational events are noisy and repetitive.** `record_count`, `token_estimate`, `attempt`, and similar fields are printed with full JSON structure for every event.
3. **There is no visual hierarchy.** Multi-round tool loops and retries appear as a flat list of raw objects.
4. **Large payloads are not summarized.** Tool-call arguments/results and error strings can fill the screen.
5. **Raw JSON is the only view.** There is no quick human-readable summary, and no way to drill into the canonical event payload on demand.

The goal is to make the expanded trace readable enough that a user can understand what happened during a turn at a glance, while preserving access to the raw event data for debugging.

## 2. Proposed Solution

Keep the existing event schema and persistence exactly as they are. This work is a pure presentation-layer change inside the TUI's `TraceBlock`:

1. **Group consecutive streamed deltas** into single `[answer]` and `[thinking]` blocks (with `[partial answer]` / `[partial thinking]` variants when a turn is interrupted).
2. **Render every non-delta event as a compact bracket-label line**, e.g. `[context] 3 memories, ~120 tokens` or `[tool] recall({ "query": "..." }) → <result> (45ms)`.
3. **Prefix each line with a relative timestamp** since turn start and a color-coded label.
4. **Truncate long payloads** in summaries to a terminal-width-aware limit, with an inline `[+]` expand that reveals the raw JSON for that line.
5. **Cap very long answer/thinking blocks** at 30% of terminal height, with an expand that reveals the full text in a scrollable region.
6. **Provide a global raw toggle** (`R` when a trace is expanded) that swaps every summary line to its canonical raw JSON, and back.
7. **Update the collapsed summary** to count grouped display lines and label them `steps`.
8. **Make the bottom hint line contextual**, showing trace-specific keys when a trace is expanded.

### Affected modules

- `rickshaw/tui.py` — core rendering, grouping, toggles, keyboard focus, and hint updates.
- `tests/test_tui.py` — update and add tests for the new behavior.
- `README.md` — document the new trace behavior and keybindings.
- `rickshaw/events.py` — no schema changes; optional small display helpers if the formatter needs them.
- `rickshaw/trace_store.py` — no changes; continues to store raw JSON only.

## 3. User Journeys

### J1 — Understand a completed turn at a glance

User submits a prompt and the turn completes. They press `Ctrl+O` on the assistant turn. The trace expands to:

```text
"what do I like?" · completed · 1.24s
openai/gpt-4o

+0.01s [context] 3 memories, ~120 tokens
+0.02s [prompt] 4 messages, ~340 tokens
+0.05s [llm] openai (attempt 1) → gpt-4o, 210 tokens
+0.42s [tool] recall({ "query": "what do I like?" }) → 3 records (45ms)
+0.48s [llm] openai (attempt 1) → gpt-4o, 89 tokens
+1.10s [answer] (12 Δ, 347 tokens)
You previously told me you prefer dark mode and short answers.
+1.12s [memory] wrote 2 records
+1.12s [job] enqueued importance_scoring for record_1
```

The user can read the sequence without parsing JSON.

### J2 — Inspect a tool call

User wants to verify which tool was called and with what arguments. They Tab into the expanded trace, arrow down to the `[tool]` line, and press Enter. The line expands to show the raw `TurnToolCallStart` and `TurnToolCallDone` JSON. If the result is too long to fit the summary, the truncated preview ends with `… (+412 chars)`.

### J3 — Follow a multi-round tool loop

The model calls `recall`, then `remember`, then answers. The trace shows three LLM-call lines interleaved with two tool-call lines in chronological order. Each round is a distinct `[llm]` line, so the user sees the loop clearly.

### J4 — Inspect retries

The provider returns a 429. The trace shows:

```text
+0.05s [llm] openai (attempt 1) → failed
+0.05s [retry] attempt 1/2 in 1.0s
+1.05s [llm] openai (attempt 2) → gpt-4o, 210 tokens
```

The full error string is hidden behind the `[+]` on the `[llm] failed` or `[retry]` line.

### J5 — Review thinking tokens

A provider emits thinking deltas. The trace shows a separate `[thinking]` block before the `[answer]` block, containing the merged reasoning text. If the model has no thinking capability, the trace shows `[thinking] (none)` for transparency, and that placeholder does not count toward the step total.

### J6 — View raw JSON for debugging

A developer expands a trace and presses `R`. Every bracket-label line is replaced by its raw JSON payload. Pressing `R` again reverts to the human-readable view. They can also Tab into the trace, arrow to a specific line, and press Enter to expand just that line.

### J7 — Interrupted turn

User presses `Esc` while the assistant is streaming text. The trace shows a `[partial answer]` block containing the text generated before interruption. The turn status is `interrupted` in the header.

### J8 — Long answer overflow

The assistant produces a response that would exceed 30% of the terminal height. The `[answer]` block renders a capped preview (e.g. first 18 lines) and a `… (+127 lines)` hint. The user expands the block to read the full text in a scrollable sub-region.

### J9 — Non-streaming provider

A provider returns the full answer in one shot. The orchestrator emits a single `TurnTextDelta` (or the final text via `TurnDone`), and the trace groups it into a single `[answer]` block.

### J10 — No thinking capability

The model produces no `TurnThinkingDelta` events. The trace renders `[thinking] (none)` so the user knows the trace did not skip reasoning, and the step count excludes the placeholder.

## 4. Constraints

- **Scope is TUI-only.** No CLI command, no reusable formatting API, no export. `TraceStore.get_trace()` continues to return raw JSON.
- **Keyboard-first.** Mouse clicks on toggles may work, but every interaction must be reachable from the keyboard.
- **Backward compatibility.** The event schema and SQLite persistence do not change. Existing `Orchestrator` callers and `TraceStore` consumers are unaffected.
- **Terminal-aware sizing.** Long payload summaries truncate to ~50% of terminal width; long answer/thinking blocks cap at 30% of terminal height.
- **Existing keybindings remain.** `Ctrl+O` expands/collapses the selected trace; `Ctrl+Up`/`Ctrl+Down` navigate turns; `Esc` interrupts or returns focus to the prompt.
- **Color palette uses existing TUI colors.** Labels and event types are color-coded, but the implementation must reuse the existing `$rk-*` CSS variables where possible.
- **No new dependencies.** The implementation must work with Textual and Rich, which are already project dependencies.

## 5. Decisions Log

| # | Decision | Alternatives considered | Reasoning |
|---|----------|------------------------|-----------|
| D1 | Scope: TUI expanded trace block only | TUI + CLI command; TUI + reusable formatter; all of the above | The user's complaint is about what they see when expanding a trace. CLI/export can be a follow-up. |
| D2 | Core fix: group consecutive `text_delta` and `thinking_delta` events into continuous blocks | Reformat every event as prose; keep JSON but prettier; collapsible JSON tree | User explicitly identified the fragmented deltas as the biggest readability problem. |
| D3 | Merged text/thinking blocks shown full by default, with a terminal-height cap (30%) and scrollable expand | Always full; always truncated; collapsible sections | User wants the full response in the trace for a self-contained story, but agreed a cap is needed for very long outputs. |
| D4 | Thinking deltas in a separate `[thinking]` block | Merged with answer; hidden by default; shown as inline interleaved chunks | Separates the model's reasoning from its final answer, making each block easier to read. |
| D5 | Empty thinking block shown as `[thinking] (none)` | Omit entirely | User wants transparency that reasoning was not skipped, but the placeholder must not count as a step. |
| D6 | Non-delta events as compact bracket-label summaries with raw JSON behind per-event expand | Full prose sentences; keep raw JSON; JSON tree with better labels | Bracket labels balance readability with density and preserve raw JSON access. |
| D7 | Tool calls and LLM calls rendered as one combined line after completion | Two separate start/done lines; hide result until expanded | Combining reduces line count while keeping all useful data in the summary. |
| D8 | Tool-call arguments shown as inline JSON | Key=value pairs; omit arguments | Inline JSON preserves structure for nested arguments while remaining compact. |
| D9 | Flat chronological list | Group by phase; tree view | User preferred a simple timeline they can scan top-to-bottom. |
| D10 | Relative timestamps on every line; tool-call duration in parentheses | No per-event timing; absolute timestamps | Relative timing helps users spot slow phases; tool durations are already available data. |
| D11 | Full color coding per event type | Plain text; minimal markup | User chose full color coding to make event types instantly distinguishable. |
| D12 | Per-event raw JSON expand via inline `[+]` / `[-]` toggle | No raw access; global toggle only; separate raw panel | Gives granular debug access without requiring a global mode switch. |
| D13 | Global raw toggle bound to `R` | `Ctrl+R`; `Ctrl+Shift+O`; no global toggle | `R` is a simple mnemonic and is only active when a trace is expanded/focused. |
| D14 | Keyboard navigation: Tab from prompt into expanded trace, arrows between toggles, Enter to expand, Esc to return focus | Focus mode via Enter; mouse-only | Matches standard terminal focus patterns and the TUI's keyboard-first design. |
| D15 | Long payload summaries truncate at ~50% of terminal width with a `… (+N chars)` hint | Full value; fixed char limit; no preview at all | Terminal-width awareness keeps the summary readable on all window sizes; full value is one expand away. |
| D16 | Retry/error summary hides the full error string | Show full error; show only status code | Error strings can contain URLs/endpoints; hiding them avoids accidental leakage while keeping retry visibility. |
| D17 | Interrupted turns render `[partial answer]` / `[partial thinking]` blocks | Discard partial text; show as `[answer] (interrupted)` | User wants to see what was generated before the interrupt. |
| D18 | Collapsed summary counts grouped display lines and labels them `steps` | Keep raw event count; drop the count entirely | Grouped display lines match what the user sees when expanded, avoiding confusion after delta grouping. |
| D19 | Empty placeholders (`[thinking] (none)`, `[answer] (empty)`) are shown but not counted | Count them; omit them | Transparency without inflating the step count. |
| D20 | Zero-data start events (e.g. `ContextStart`) are folded into their matching done/built line | Show start and done separately; omit start | Keeps the trace compact while using the start timestamp for the line's relative time. |
| D21 | Trace header is two lines: task + status/duration, then provider/model | Single line; multi-line labeled fields; include turn ID | User wants task separated from metadata; turn ID is hidden because it is opaque to users. |
| D22 | `TurnStart` represented by the header; no separate `[start]` body line | Include `[start]` line in body | Avoids duplicating the task in both header and body. Raw `TurnStart` is still reachable via the global raw toggle. |
| D23 | Bottom hint line becomes contextual when a trace is expanded | Always show all keys; never update hint | Helps users discover new trace keys without cluttering the default hint. |
| D24 | Persistence stays raw JSON; formatting is TUI-only | Add formatted column; add `get_trace(format='human')` | Keeps the store a simple source of truth and preserves backward compatibility. |

## 6. Out of Scope

- CLI command or flag to print a formatted trace outside the TUI.
- Reusable formatting module/API for programmatic callers.
- Exporting traces to files or external systems.
- Searching, filtering, or editing traces.
- Replaying past sessions from the trace store on TUI startup.
- Live updating the trace while a turn is in progress (the trace is rendered after the turn completes).
- Adding raw request/response metadata events to the trace (the current event schema does not include them).
- Mouse-only interactions (mouse clicks may work, but are not required).
- User-configurable color schemes or trace layouts.
- Rendering the answer inside the trace as Markdown or with syntax highlighting (it is plain text).

## 7. Implementation Notes (non-normative)

- The formatter should consume `list[TurnEvent]` (or `(event, timestamp)` pairs) and produce a list of display lines. Each display line carries:
  - relative timestamp string,
  - event type label and color class,
  - summary string (possibly truncated),
  - raw JSON string(s) for the `[+]` expand,
  - a flag indicating whether it is a placeholder (for step counting),
  - optional cap/expand state for long answer/thinking blocks.
- `TraceBlock._details_text()` should delegate to the formatter and, if needed, track which lines are expanded.
- `R` and per-event toggles must not fire while the prompt has focus. Use Textual focus state or a focused trace sub-widget.
- The collapsed summary should be computed from the display line list, excluding lines marked as placeholders and lines that are raw-only expansions.
- The hint line can be updated by `RickshawTUI._set_hint()` based on whether the selected turn's trace is expanded and whether trace focus is active.
