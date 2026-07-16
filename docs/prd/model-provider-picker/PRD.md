# PRD: Model/Provider Modal Picker

Status: **pending approval**  
Interview record: [DRAFT.md](DRAFT.md)

## 1. Problem Statement

Rickshaw's TUI currently forces users to type exact provider and model names in several places:

- The on-launch provider picker prints a list and asks the user to type a provider name.
- `/settings` is a text-based wizard that asks the user to type a provider name, then a model name.
- `/provider <name>` and `/model <name>` require typed arguments (with inline autocomplete only for `/model`).

This is error-prone, slow, and inconsistent with modern terminal UIs. The goal is to replace these typed-name interactions with a centered modal overlay that lists available options and lets the user select with standard keyboard navigation.

## 2. Proposed Solution

### 2.1 Core approach

Build a generic, reusable `SelectionModal` Textual `ModalScreen` in a new module `rickshaw/selection_modal.py` and use it for all provider, model, and effort selection in the TUI.

The modal is a single screen that can present one or more sequential steps. Each step shows a title, a scrollable list of options, and a footer hint. Navigation uses `Tab`/`Shift+Tab` and `Up`/`Down`; `Enter` selects the highlighted option; `Esc` goes back one step (or closes the modal on the first step).

### 2.2 `SelectionModal` behavior

- **No filter/search input** (D4). Users navigate and select entirely with the keyboard.
- **No typed arguments** for `/provider`, `/model`, or `/effort` (D3). Typed arguments are rejected with a help message.
- **Loading and error states** are rendered inside the modal (D5, D11, D12):
  - While fetching models, show a loading indicator and the text "Loading models…".
  - If `available_models()` returns an empty list, show "No models available" plus an actionable hint and a Back option.
  - If `available_models()` raises, show the error message plus Retry and Back options.
- **Pre-selection**: the currently active provider/model/effort is highlighted when the modal opens, if one exists (D17).
- **Responsive sizing**: the modal shrinks to fit narrow terminals; long option labels are truncated with ellipsis (D16).
- **Footer hint**: the bottom hint bar updates to a picker-specific hint while the modal is open (D20).

### 2.3 Row content

- **Provider rows** show the provider name and its protocol tag (e.g. `anthropic  anthropic`, `openai  openai`, `ollama  openai`).
- **Model rows** show the model name plus context window and price estimate when available (e.g. `gpt-4o  128k  ~$2.5/$10`).
- **Effort rows** show the effort value (`low`, `medium`, `high`). If a level is unsupported for the current provider/model, it is shown disabled with a note.
- Models are sorted alphabetically (D19).

### 2.4 Steps and flows

#### `/settings` (full wizard)

Opens the modal on the **provider** step. After a provider is selected:

1. If the provider requires OAuth and no credential is stored, run the existing OAuth flow, then continue.
2. Fetch the model list and advance to the **model** step.
3. After a model is selected, inspect `provider.capabilities().effort_levels`:
   - If it contains two or more values, advance to the **effort** step.
   - If it contains zero or one value, close the modal and apply the current/default effort.

#### `/provider`

Opens the modal on the **provider** step. On selection, the same provider→model→effort flow as `/settings` is followed.

#### `/model`

Opens the modal on the **model** step for the current provider. If no provider is active, opens the full `/settings` flow instead (D15).

#### `/effort`

Opens the modal on the **effort** step for the current provider/model. If no provider or model is active, opens the full `/settings` flow instead (D24). If only one effort level is available, the effort step still opens and shows that single level with a note (D23).

#### On-launch picker

If `rickshaw` starts with no persisted provider, open the modal on the **provider** step. If a provider is already active, the normal TUI loads without a picker.

### 2.5 Selection application

When the modal completes with a final provider/model/effort:

- Build the provider with `_rebuild_provider` or `build_provider_from_profile`.
- If the new provider/model does not support the current effort, reset effort to `medium` and write a note to the transcript (D14).
- Persist the new provider, model, and effort to `~/.rickshaw/settings.json`.
- Update `self.provider`, `self.orchestrator.provider`, `self.orchestrator.effort`, and `self.effort`.
- Update the status bar.
- Return focus to the prompt.

If the user cancels (Esc on provider step), no changes are applied and the modal closes.

### 2.6 Affected modules

- `rickshaw/selection_modal.py` — new generic `SelectionModal`.
- `rickshaw/tui.py` — replace `_start_provider_picker`, `_start_model_picker`, `_cmd_settings`, `_cmd_provider`, `_cmd_model`, `_cmd_effort`, and the on-launch picker with modal calls.
- `rickshaw/keybindings_modal.py` — align modal CSS for consistent chrome (optional).
- `tests/test_tui.py` — add tests for modal open/close/selection, step transitions, error states, and command changes.
- `README.md` — update `/provider`, `/model`, `/effort`, and `/settings` descriptions.

## 3. User Journeys

### J1 — First launch with no persisted provider (happy path)

1. User runs `rickshaw`.
2. The TUI opens and immediately pushes the `SelectionModal` on the provider step.
3. The user navigates to `openai` and presses Enter.
4. The modal advances to the model step and shows "Loading models…".
5. The model list populates; the user selects `gpt-4o` and presses Enter.
6. `gpt-4o` supports multiple effort levels, so the modal advances to the effort step.
7. The user selects `medium` and presses Enter.
8. The modal closes, the status bar shows `openai · gpt-4o · medium`, and the user is at the prompt.

### J2 — Switch provider mid-session with `/provider`

1. User types `/provider` and presses Enter.
2. The modal opens on the provider step with the current provider pre-selected.
3. The user navigates to `anthropic` and presses Enter.
4. `anthropic` has no stored credential, so the existing OAuth flow runs.
5. After OAuth succeeds, the modal advances to the model step.
6. The user selects `claude-sonnet-4-20250514` and presses Enter.
7. The modal closes, the provider/model switch is applied, and a note appears if effort was reset.

### J3 — Switch model with `/model`

1. User types `/model` and presses Enter.
2. The modal opens on the model step for the current provider with the current model pre-selected.
3. The user selects a different model and presses Enter.
4. If the new model supports multiple effort levels and the current effort is unsupported, a note is shown and effort is reset to `medium`.
5. The modal closes and the status bar updates.

### J4 — Change effort with `/effort`

1. User types `/effort` and presses Enter.
2. The modal opens on the effort step with the current effort pre-selected.
3. The user selects `high` and presses Enter.
4. The modal closes, effort is persisted, and the status bar updates.

### J5 — `/model` before provider is selected

1. User types `/model` before any provider is active.
2. Because no provider is selected, the modal opens on the provider step instead.
3. The user picks a provider, then a model, then effort if applicable.
4. The modal closes and the full selection is applied.

### J6 — `/effort` before provider/model is selected

1. User types `/effort` before any provider is active.
2. The modal opens on the provider step.
3. The user completes provider → model → effort as in J1.

### J7 — `/settings` review and change

1. User types `/settings` and presses Enter.
2. The modal opens on the provider step with current values pre-selected.
3. The user can change provider, model, and effort (when applicable) in one flow.
4. On completion, all changes are persisted.

### J8 — Local provider with one model

1. User selects the `ollama` provider.
2. The modal fetches models and finds only one model.
3. Per D7, the model step is still shown with the single model highlighted.
4. The user presses Enter to confirm.

### J9 — Model fetch fails

1. User selects a provider.
2. The modal shows "Loading models…" for a short time, then an error: "Cannot list models: connection refused".
3. The modal shows Retry and Back options.
4. The user presses Back, returns to the provider list, and selects a different provider.

### J10 — Cancel mid-flow

1. User opens `/settings`, selects a provider, and reaches the model step.
2. The user presses Esc.
3. The modal returns to the provider step.
4. The user presses Esc again.
5. The modal closes and no changes are applied.

### J11 — Effort mismatch

1. User switches from `openai`/`o3` (supports high effort) to `openai`/`gpt-4o` (does not support high effort).
2. After the model step, the modal closes.
3. A note is written to the transcript: "note: gpt-4o does not support effort high. Reset to medium."
4. The status bar updates to `medium`.

### J12 — Narrow terminal

1. User resizes the terminal to a very narrow width while the modal is open.
2. The modal shrinks and long model names are truncated with ellipsis.
3. Navigation remains usable.

## 4. Constraints

- **Textual framework**: The implementation must use Textual's `ModalScreen`, `Static`, `Vertical`, `VerticalScroll`, and bindings.
- **No filter input**: The modal must not require typing to narrow the list (D4).
- **Built-in providers only in the modal**: Custom providers from `settings.json` are not shown in the modal but remain supported via `/provider add` (D8).
- **Preserve existing slash menu**: The inline `/` command menu and its filter-as-you-type behavior are not changed (D22).
- **No typed arguments**: `/provider`, `/model`, and `/effort` no longer accept typed arguments (D3).
- **OAuth flows stay unchanged**: The modal triggers the existing OAuth flow when needed; OAuth UI itself is not redesigned.
- **Backward-compatible persistence**: The same `~/.rickshaw/settings.json` keys (`provider`, `model`, `effort`) are written.

## 5. Decisions Log

| ID | Decision | Alternatives | Reasoning |
|----|----------|--------------|-----------|
| D1 | All provider/model/effort selection flows use the modal overlay: on-launch picker, `/settings`, `/provider`, `/model`, `/effort`. | Settings + on-launch only; settings only. | User wants a consistent, no-typing experience across all selection surfaces. |
| D2 | `/settings` uses one modal that starts on provider list and advances to model/effort inside the same screen. | Two separate modals; one two-pane modal. | User wants the sequential wizard preserved without separate-modal flash. |
| D3 | `/provider`, `/model`, and `/effort` become no-argument commands that open the corresponding modal step; typed arguments are rejected with a help message. | Always open modal but ignore args; use arg as preselection; direct switch on exact match. | User wants a clean, no-typing command surface. |
| D4 | The modal has no filter/search input; users navigate only with `Tab`/`Shift+Tab`/`Up`/`Down` and select with `Enter`. | Always-visible filter; `/` toggle; type-to-jump. | User wants a simple, no-typing picker. |
| D5 | The modal shows a loading state while fetching models and displays errors inline with a way to retry or go back. | Fetch before opening; pre-fetch and cache; skip step on failure. | User wants transparent feedback and the ability to recover without leaving the modal. |
| D6 | OAuth-capable built-ins always appear in the provider picker; selecting one without a credential triggers the existing OAuth flow before continuing to model selection. | Skip model step for OAuth; disable if not authenticated; show tag and require `/login`. | User wants all providers discoverable and OAuth handled automatically. |
| D7 | Local providers always show the model step, even when only one model is available. | Auto-select single model; auto-select if previously persisted. | User wants consistent, transparent selection for all providers. |
| D8 | User-defined custom providers from `settings.json` are not shown in the modal picker; the underlying custom-provider feature remains available via `/provider add` and manual config. | Show all configured providers; remove custom-provider feature entirely. | User wants the modal picker limited to built-ins for now; removing the feature would be a breaking, out-of-scope change. |
| D9 | The modal supports `Tab`/`Shift+Tab` and `Up`/`Down` for navigation, `Enter` to select, and `Esc` to cancel/go back. | Up/Down/Enter/Esc only; add j/k and q; all common bindings. | User wants consistency with existing slash-menu Tab cycling plus standard arrow-key list navigation. |
| D10 | `/settings` is a two- or three-step modal: provider → model → effort (only if the selected model/provider reports multiple supported effort levels; otherwise effort stays at the current/default value). | Provider+model only; show effort but not editable. | User wants effort configurable in `/settings` when meaningful, but not forced into an extra step for providers with a single effort level. |
| D11 | When a provider lists zero models, the modal shows an empty state with an actionable hint and a way to go back to the provider list. | Close and write error; auto-retry with hint. | User wants to recover without leaving the modal and pick another provider. |
| D12 | When `available_models()` raises an exception, the modal shows the error inline with Retry and Back options. | Close and show error; mark provider as failed. | User wants to recover from transient failures without leaving the modal. |
| D13 | `Esc` navigates back one step in the modal (model → provider, effort → model); `Esc` on the provider step closes the modal with no changes applied. | Esc always closes; Esc confirms previous choices. | User wants to correct mistakes without starting over, while preserving a safe cancel path. |
| D14 | When the selected provider/model does not support the current effort, effort is auto-reset to `medium` and a note is shown in the transcript. | Include effort step on mismatch only; keep current effort. | User wants to avoid provider errors while keeping the existing reconciliation behavior. |
| D15 | `/model` with no active provider opens the full `/settings` modal (provider → model, and effort if applicable). | Show warning; open empty model modal. | User wants a forgiving path that doesn't require remembering `/settings`. |
| D16 | The modal shrinks to fit narrow terminals, truncating long names with ellipsis rather than requiring a minimum size. | Full-screen below threshold; show 'terminal too narrow'. | User wants the picker to remain usable in constrained terminal sizes. |
| D17 | The modal pre-selects the currently active provider/model/effort when one exists. | Start at top; pre-select only for `/model` and `/provider`. | User wants the current state reflected so switching is fast and predictable. |
| D18 | Provider rows show the protocol tag; model rows show context window and price estimate. Long rows are truncated with ellipsis in narrow terminals. | Names only; full metadata with all fields. | User wants useful context without modal clutter. |
| D19 | Models are sorted alphabetically within the picker. | Curated/builtin order; capability grouping. | User wants a predictable, scannable list. |
| D20 | The bottom hint bar shows a context-aware picker hint (e.g. `tab/up/down navigate · enter select · esc back/close`) that updates per step. | Reuse overlay hint; no hint change. | User wants discoverable controls for the modal. |
| D21 | Implement a generic, reusable `SelectionModal` that accepts a list of options and a callback. | Dedicated provider/model modal. | User wants a reusable component and consistency across selection surfaces. |
| D22 | The generic `SelectionModal` is used for provider, model, and effort pickers. The existing inline slash-command menu remains unchanged; `/effort` typed-value autocomplete is removed because `/effort` opens the modal. | Replace slash menu too; keep `/effort` autocomplete. | User wants a single picker component for provider/model/effort while preserving the fast slash-command workflow. |
| D23 | When the current provider/model supports only one effort level, `/effort` still opens the effort modal showing that single level with an explanatory note. | Skip modal; show all levels disabled. | User wants transparency even when no real choice exists. |
| D24 | `/effort` with no active provider/model opens the full `/settings` modal (provider → model → effort). | Show warning. | User wants a forgiving path consistent with `/model`. |

## 6. Out of Scope

- Redesigning the inline slash-command menu (`/` autocomplete). It stays as-is (D22).
- Changes to the OAuth login UI itself; only the trigger point from the picker is in scope.
- Adding user-defined custom providers to the modal picker (D8).
- Adding a search/filter input inside the modal (D4).
- Configurable keybindings for the picker (the keymap is fixed per D9).
- Changes to provider/model validation, pricing, or context-window metadata beyond displaying existing fields.
- Removing or redesigning `/provider add`; the custom-provider feature remains untouched.

## 7. Test Plan

- Add unit tests for `SelectionModal` state transitions (step changes, selection, cancel, back).
- Add TUI tests for:
  - Opening the modal on launch with no provider.
  - `/provider`, `/model`, `/effort`, and `/settings` opening the correct step.
  - Pre-selection of the active provider/model/effort.
  - Advancing through provider → model → effort when applicable.
  - Loading and error states during model fetch.
  - Empty model list state.
  - `Esc` going back one step and closing from the provider step.
  - Effort mismatch reset note.
  - Narrow-terminal truncation.
- Update any existing tests that rely on `/provider <name>`, `/model <name>`, or `/effort <level>` typed arguments.
- Run `uv run pytest` before handoff.
