# DRAFT: TUI keybinding redesign

## Request (as understood)

Before merging PR #32, redesign Rickshaw's terminal keybindings so they align with the industry-standard conventions used by Claude Code and OpenAI Codex CLI. The goal is a more predictable, keyboard-first TUI where users always know where focus is and which keys do what.

## Current state

Keybindings are scattered across `rickshaw/tui.py`:

- **Global bindings:** `Ctrl+C` (cancel/quit), `Ctrl+L` (clear transcript), `Escape` (interrupt/cancel/close menu/return to prompt), `Ctrl+Up/Down` (select turn), `Ctrl+O` (toggle selected turn's trace), `R` (raw JSON toggle, trace-only).
- **Prompt input:** `Enter` submit, `Shift+Enter`/`Ctrl+J` newline, `Tab` accept autocomplete or focus trace, `Up/Down` history, `/` slash-menu, `Escape` close menu/clear prompt.
- **Inside a trace:** `Up/Down` navigate events, `Enter` expand/collapse, `R` raw, `Escape` return to prompt.

PR #32 already changes two things:
- Corrects the contextual hint text for trace mode.
- Makes `Ctrl+O` toggle the focused turn's trace when focus is inside a trace.

## Industry benchmarks

**Claude Code**
- `Ctrl+C` — cancel, or double-tap to quit.
- `Ctrl+L` — **redraw screen**, not clear context.
- `Ctrl+O` — toggle **transcript viewer** (detailed tool usage).
- `Ctrl+R` — reverse history search.
- `Ctrl+G` — open prompt in `$EDITOR`.
- `Ctrl+J` / `Shift+Enter` — newline.
- `Ctrl+D` — exit.
- Contextual help with `?` in transcript viewer.

**OpenAI Codex CLI**
- `Enter` — send / inject instructions while running.
- `Tab` — queue follow-up while running.
- `Ctrl+C` — cancel, double-tap quit.
- `Ctrl+D` — exit.
- `Ctrl+L` — clear terminal screen (but keep conversation context).
- `Esc` — interrupt.
- `Esc Esc` (empty composer) — edit previous message.
- `Ctrl+R` — reverse history search.
- `Ctrl+G` — external editor.
- `Shift+Tab` — cycle approval modes.
- `Up/Down` — draft history.
- `Ctrl+O` / `/copy` — copy latest response.

## Decisions made

### D1 — Design target
**Decision:** Align with **Claude Code conventions** as the primary reference.
**Rationale:** Claude Code is the most mature terminal AI assistant, has clear contextual keybindings, and its `Ctrl+O` "transcript viewer" concept maps naturally onto Rickshaw's per-turn trace block.

### D2 — Ctrl+L semantics
**Decision:** `Ctrl+L` will **redraw the screen** (Claude Code convention). The existing clear-transcript behavior moves to the `/clear` slash command only.
**Rationale:** Removes a destructive single-key shortcut, aligns with the Claude Code muscle memory, and `/clear` already exists for users who want to wipe the transcript.

### D3 — New keybinding discovery
**Decision:** Do not add `Ctrl+R`, `Ctrl+G`, `Ctrl+D`, or `?` as new shortcut keys in this redesign. Instead, add a `/keybindings` slash command that lists all available keybindings and hints.
**Rationale:** The user explicitly prefers discoverability through a slash command over expanding the shortcut surface. This keeps the keymap small and avoids terminal-emulator conflicts while still giving users a complete reference.

### D4 — /keybindings format
**Decision:** `/keybindings` opens a **modal overlay** (not a transcript line) that lists keybindings and can be dismissed with `Esc` or `?`/`q`.
**Rationale:** A modal does not pollute the transcript, can be updated per context later, and matches the "press ? for help" pattern in Claude Code without requiring a dedicated `?` keybinding.

### D5 — Configurability
**Decision:** Keybindings will be **hardcoded** to the standard Claude Code-aligned keymap. No user config file in this PRD, but the implementation should keep bindings as data rather than inline conditionals to make future configuration easier.
**Rationale:** The user wants to ship a clean standard keymap quickly. Building a config file system would expand scope and delay PR #32.

### D6 — Navigation model
**Decision:** Adopt **Model D — Tab/Shift+Tab focus ring** for navigating turns and trace events.
**Rationale:** The user explicitly wants to drop the selected/focused split and use standard Tab focus semantics.

### D7 — Tab from prompt
**Decision:** With the prompt focused and no slash menu open, `Tab` moves focus to the **newest turn block**.
**Rationale:** The user wants to start inspecting from the latest assistant reply.

### D8 — Focus ring direction
**Decision:** The Tab ring is **prompt-centered**: prompt -> newest turn -> older turns/events -> ... -> oldest -> prompt (wrap). `Shift+Tab` traverses the same ring in reverse: prompt -> oldest -> newer -> ... -> newest -> prompt.
**Rationale:** Gives fast access to newest from prompt while preserving bidirectional browsing.

### D9 — Enter on a turn block
**Decision:** Pressing `Enter` on a focused (collapsed) turn block **expands its trace and moves focus to the first event**.
**Rationale:** One-key entry into the details, matching the user's description.

### D10 — Tab inside an expanded trace
**Decision:** Inside an expanded trace, `Tab`/`Shift+Tab` move to the next/previous event. At the last/first event, `Tab`/`Shift+Tab` continue to the adjacent turn (newer/older respectively).
**Rationale:** Continuous Tab navigation across the whole transcript.

### D11 — Expanded turn block tab stop
**Decision:** When a trace is expanded, the turn block itself is **not** a tab stop; focus lives directly in the events.
**Rationale:** Matches D9 (Enter puts focus in events) and keeps Tab movement inside the details.

### D12 — Legacy shortcuts
**Decision:** Remove `Ctrl+Up/Down` and `Ctrl+O` from the redesigned keymap. `Tab`/`Shift+Tab` and `Enter` handle all turn and trace navigation.
**Rationale:** The user wants a clean Model D keymap without redundant legacy shortcuts.

### D13 — Slash menu Tab
**Decision:** When the slash autocomplete menu is open, `Tab` cycles the menu highlight; `Enter` accepts the selected item.
**Rationale:** Aligns with Model D's use of `Tab` for navigation and `Enter` for activation, while preserving autocomplete discoverability.

### D14 — Enter on a trace event
**Decision:** When a trace event (line) is focused, `Enter` toggles that event's own content/payload, preserving the existing per-event expand/collapse behavior.
**Rationale:** Keeps the ability to drill into individual events while using `Enter` consistently as the "activate" key.

### D15 — Escape in a trace
**Decision:** Pressing `Escape` while focus is inside a trace **collapses the trace and returns focus to the turn block** (which becomes the active tab stop when collapsed).
**Rationale:** Gives a clear exit path that lands on a focusable object, so `Enter` can immediately re-expand the same turn.

### D16 — Bottom hint design
**Decision:** The bottom hint line will be **context-sensitive** with a distinct hint for each mode: prompt, focused turn block, expanded trace event, slash menu, and `/keybindings` overlay.
**Rationale:** The user wants full discoverability at a glance; contextual hints prevent information overload and match Claude Code's footer behavior.

### D17 — PR strategy
**Decision:** Fold the keybinding redesign into the existing **PR #32** (`fix/trace-hints`). The PR title and description should be updated to reflect the full scope.
**Rationale:** The user wants a single review/merge unit and the existing branch is the natural home for keybinding and hint improvements.

## Open questions / assumptions

1. **`Ctrl+L` semantics.** (Resolved by D2.)
2. **Keybinding discovery format.** (Resolved by D4.)
3. **Should keybindings be user-configurable?** (Resolved by D5.)
4. **Turn/event navigation.** (Resolved by D6–D11.)
5. **`Ctrl+Up/Down` and `Ctrl+O` disposition.** (Resolved by D12.)
6. **Slash menu and Tab.** (Resolved by D13.)
7. **Event-level Enter behavior.** (Resolved by D14.)
8. **Trace exit/collapse behavior.** (Resolved by D15.)
9. **Hint/footer design.** (Resolved by D16.)
10. **What happens to PR #32?** (Resolved by D17.)

## Proposed directions (pre-decision)

- Adopt `Ctrl+L` as **redraw screen** (Claude Code convention) and move clear-transcript to `/clear` only.
- Add a `/keybindings` slash command for keybinding discovery instead of `?`, `Ctrl+R`, `Ctrl+G`, or `Ctrl+D`.
- Implement Model D Tab/Shift+Tab focus ring through turns and events.
- Remove `Ctrl+Up/Down` and `Ctrl+O`.
- Resolve slash menu, event Enter, and hint behavior.
- Decide whether to fold into PR #32 or a new PR.

## User journeys to cover

- J1: User wants to review an earlier turn without losing their place.
- J2: User wants to clear the screen but keep the conversation context.
- J3: User wants to edit a long prompt in `$EDITOR`.
- J4: User wants to find a previous prompt quickly.
- J5: User wants to see all available keys while in a trace.
- J6: User wants to quit safely without reaching for `Ctrl+C` twice.
