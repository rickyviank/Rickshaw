# PRD: TUI keybinding redesign

Status: **pending approval**  
Interview record: [DRAFT.md](DRAFT.md)

## 1. Problem Statement

Rickshaw's terminal UI currently mixes two navigation metaphors:

1. A **selected turn** cursor (`Ctrl+Up/Down`) that controls `Ctrl+O` and `Tab`.
2. A separate **keyboard focus** inside a trace (`Tab`/`Up`/`Down`) that controls event navigation.

These two indicators can drift apart, so users see an amber border on one turn and a blue highlight on another. `Ctrl+O` then acts on the wrong turn, the bottom hint text is misleading (it said "tab expand event" while `Enter` actually expands), and the user cannot always tell which key will do what.

The goal of this PRD is to redesign the keymap so that:
- There is a single, predictable "active" object at any moment.
- Navigation follows industry conventions (Claude Code / Codex CLI) where applicable.
- The bottom hint always tells the truth for the current context.
- Users have a single place (`/keybindings`) to learn every shortcut.

This work will be folded into the existing **PR #32** (`fix/trace-hints`).

## 2. Proposed Solution

### 2.1 Core interaction model

Adopt a **Tab/Shift+Tab focus ring** through the transcript:

- The transcript is treated as a circular list of focusable objects: `prompt -> newest turn block -> ... -> oldest turn block -> newest turn block -> ...`.
- `Tab` moves to the next object in the ring; `Shift+Tab` moves to the previous object.
- From the prompt, `Tab` goes to the **newest** turn first (reverse chronological order).
- A turn block is a focusable tab stop when **collapsed**.
- `Enter` on a collapsed turn block expands its trace and focuses the **first trace event**.
- When expanded, the turn block is **not** a tab stop; focus lives inside the events.
- Inside an expanded trace, `Tab`/`Shift+Tab` move between events. At the last/first event, the ring continues to the next/previous turn block.
- `Enter` on a trace event toggles that event's own content.
- `Escape` inside a trace collapses the trace and returns focus to the turn block.
- `Escape` at the prompt clears the prompt text or closes the slash menu.

### 2.2 Retained and remapped keys

| Key | Context | Action |
|-----|---------|--------|
| `Ctrl+C` | Global | Cancel running turn, or double-tap to quit. |
| `Ctrl+L` | Global | Redraw the screen (Claude Code convention). |
| `Escape` | Global / prompt | Interrupt running turn, close slash menu, or clear prompt text. |
| `Enter` | Prompt | Submit message. If slash menu is open, accept the selected item. |
| `Shift+Enter` / `Ctrl+J` | Prompt | Insert a newline. |
| `Tab` | Prompt, no menu | Move focus to the newest turn block. |
| `Tab` | Slash menu open | Cycle the menu highlight. |
| `Enter` | Slash menu open | Accept the selected command. |
| `Up` / `Down` | Prompt, no menu | Navigate command history. |
| `/` | Prompt | Open slash-command autocomplete. |
| `Tab` / `Shift+Tab` | Turn block / event | Move focus forward/backward in the transcript ring. |
| `Enter` | Collapsed turn block | Expand trace and focus first event. |
| `Enter` | Trace event | Toggle the event's content. |
| `Escape` | Trace event | Collapse trace and focus the turn block. |
| `R` | Trace event | Toggle raw JSON for the whole trace block. |
| `/keybindings` | Prompt | Open a modal overlay listing all keybindings. |
| `/clear` | Prompt | Clear the transcript (replaces `Ctrl+L` clear). |

### 2.3 Removed keys

- `Ctrl+Up/Down` — turn selection cursor is replaced by the Tab focus ring.
- `Ctrl+O` — trace toggle is replaced by `Enter` on a turn block.

### 2.4 Context-sensitive bottom hint

The footer hint updates for each context:

- **Prompt:** `enter submit · tab newest turn · shift+tab oldest · / menu · esc interrupt · ctrl+c quit`
- **Collapsed turn block:** `tab next/prev turn · enter expand trace · esc prompt`
- **Trace event:** `tab next/prev event · enter toggle · esc collapse trace · r raw`
- **Slash menu:** `tab cycle · enter accept · esc close`
- **`/keybindings` overlay:** `esc/q close`

### 2.5 `/keybindings` overlay

A new slash command `/keybindings` opens a centered modal overlay (not a transcript line) that lists the complete keymap grouped by context. It can be dismissed with `Esc`, `q`, or `?`.

The overlay uses the same key groups as the bottom hint but includes every shortcut in full, including slash commands. It does not pollute the transcript.

### 2.6 Affected modules

- `rickshaw/tui.py` — focus management, key handlers, hint updates, `/keybindings` overlay, turn block focusability.
- `tests/test_tui.py` — update and add tests for the new focus ring, hint contexts, slash command, and removed bindings.
- `README.md` — document the new keymap.

## 3. User Journeys

### J1 — Review the most recent turn

1. The assistant finishes a turn; a collapsed trace block appears below its message.
2. The user presses `Tab` from the prompt.
3. Focus moves to the newest turn block; the bottom hint changes to the turn-block hint.
4. The user presses `Enter`.
5. The trace expands and focus moves to the first event.
6. The user presses `Tab` to walk through events.
7. To collapse, the user presses `Escape`; focus returns to the turn block.

### J2 — Review an older turn

1. The user presses `Tab` from the prompt to focus the newest turn block.
2. The user presses `Tab` repeatedly (or `Shift+Tab` from the prompt) to reach the desired older turn.
3. The user presses `Enter` to expand and inspect.
4. `Escape` collapses and returns focus to the block.

### J3 — Reference a trace while typing a follow-up

1. The user expands a trace with `Enter`.
2. The user reads the events.
3. The user wants to keep the trace visible but type a follow-up.
4. Current behavior: `Escape` collapses the trace and returns focus to the prompt. If the user wants the trace to remain open, they must leave it expanded while the prompt is focused. This is acceptable because the trace is a collapsed/expandable block and does not steal screen space when collapsed.

### J4 — Use slash commands

1. The user types `/` in the prompt.
2. The slash menu appears.
3. The user presses `Tab` to cycle through commands.
4. The user presses `Enter` to accept and run the command.
5. If the user changes their mind, `Escape` closes the menu.

### J5 — Clear the screen

1. The user types `/clear` and presses `Enter`.
2. The transcript UI is cleared.
3. The conversation memory and persisted traces are unaffected.

### J6 — Discover all keybindings

1. The user types `/keybindings` and presses `Enter`.
2. A modal overlay appears listing every shortcut by context.
3. The user presses `Esc` or `q` to close the overlay.

### J7 — Interrupt a running turn

1. The assistant is generating a response.
2. The user presses `Escape` (or `Ctrl+C`).
3. The turn is cancelled and focus returns to the prompt.

### Edge cases

- **Empty transcript:** `Tab` from the prompt does nothing because there are no turn blocks.
- **Running turn with disabled prompt:** `Tab` from the prompt is ignored while a turn is active.
- **Expanded trace with one event:** `Tab` from the event moves to the next turn; `Shift+Tab` moves to the previous turn.
- **No trace events (empty trace):** `Enter` on the turn block expands it but focus remains on the block (or moves to the prompt if no events exist).
- **Menu open while a turn block is focused:** The slash menu overrides all transcript navigation; `Tab` cycles menu items, `Enter` accepts, `Escape` closes the menu.

## 4. Constraints

- **Hardcoded keymap:** User-configurable keybindings are out of scope for this PRD, but the implementation should keep bindings as structured data (not inline conditionals) so a config file can be added later.
- **Claude Code primary:** Where Claude Code and Codex CLI differ, Claude Code is the reference.
- **PR #32 fold:** All implementation will be committed to the existing `fix/trace-hints` branch and PR #32 will be retitled/rewritten.
- **No new global shortcut keys:** `/keybindings` is the only new discovery mechanism; we are not adding `Ctrl+R`, `Ctrl+G`, `Ctrl+D`, or `?`.
- **Textual framework:** The implementation must work within Textual's focus system, `VerticalScroll`, and `Static`/`Vertical` widgets.

## 5. Decisions Log

| ID | Decision | Alternatives | Reasoning |
|----|----------|--------------|-----------|
| D1 | Align with **Claude Code** conventions. | Codex CLI, blend, keep Rickshaw. | Most mature terminal AI tool; `Ctrl+O` maps to trace viewer. |
| D2 | `Ctrl+L` redraws screen; clear moves to `/clear`. | Keep clear shortcut, Codex clear-screen semantics. | Avoids destructive single-key shortcut. |
| D3 | No new `Ctrl+R/G/D/?`; use `/keybindings` slash command. | Add each missing key. | User prefers slash-command discovery. |
| D4 | `/keybindings` opens a modal overlay. | Print in transcript, context-aware only. | Doesn't pollute transcript. |
| D5 | Keymap is hardcoded but structured as data. | Configurable via JSON/TOML now. | Faster to ship; preserves future config. |
| D6 | Adopt **Model D** Tab/Shift+Tab focus ring. | Model A/B/C. | User wants standard focus semantics. |
| D7 | `Tab` from prompt focuses newest turn. | Oldest, selected, toggle latest. | User starts from latest reply. |
| D8 | Prompt-centered ring: prompt -> newest -> older -> ... -> oldest -> prompt. | Oldest-first, stop at boundaries, no ring. | Fast access to newest + bidirectional browsing. |
| D9 | `Enter` on turn block expands trace and focuses first event. | Keep focus on block, toggle only. | One-key entry into details. |
| D10 | Inside trace, `Tab` moves events; at boundaries continues to adjacent turn. | Wrap inside trace, stop, exit to prompt. | Continuous Tab navigation. |
| D11 | Expanded turn block is not a tab stop. | Keep block as tab stop. | Focus lives directly in events after D9. |
| D12 | Remove `Ctrl+Up/Down` and `Ctrl+O`. | Keep as alternatives. | Clean keymap without redundant shortcuts. |
| D13 | In slash menu, `Tab` cycles; `Enter` accepts. | Tab accepts, Enter only. | Aligns with Model D Tab = navigate, Enter = activate. |
| D14 | `Enter` on trace event toggles event content. | Collapse whole trace, do nothing. | Preserves per-event drill-down. |
| D15 | `Escape` in trace collapses trace and focuses turn block. | Return to prompt expanded, collapse+prompt. | Clear exit that lands on a focusable object. |
| D16 | Context-sensitive bottom hint per mode. | Minimal hint, no hint. | Full discoverability without clutter. |
| D17 | Fold implementation into PR #32. | New PR, merge first then redesign. | Single review unit for keybinding/hint work. |

## 6. Out of Scope

- User-configurable keybindings (intentionally deferred).
- `Ctrl+R` reverse history search.
- `Ctrl+G` external editor for prompts.
- `Ctrl+D` exit shortcut.
- `?` global help key (replaced by `/keybindings`).
- Continuous scroll / mouse-only transcript navigation improvements.
- Rewriting the trace formatter from PR #31; only keybindings and focus change.

## 7. Test Plan

- Update `test_tui.py` to assert the new `Tab`/`Shift+Tab` focus ring.
- Add tests for `Enter` expand/collapse on turn blocks and events.
- Add tests for `Escape` collapsing a trace and returning focus to the block.
- Add tests for slash-menu `Tab` cycling and `Enter` acceptance.
- Add tests for context-sensitive hints.
- Add tests for `/keybindings` overlay open/close.
- Remove or update tests that rely on `Ctrl+Up/Down` and `Ctrl+O`.
- Run `uv run pytest` before updating PR #32.
