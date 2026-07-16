# DRAFT: Model/Provider Modal Picker

Working document for the spec interview.

## Restated request

Replace the places in Rickshaw's TUI where users must type exact provider or model names with a centered modal overlay that lists the available choices and lets the user pick with arrow keys + Enter.

## Current behavior (as found in code)

- `rickshaw/tui.py` has an interactive `/settings` wizard that writes lines to the transcript and prompts the user to type a provider name, then a model name.
- `/provider <name>` and `/model <name>` require typed arguments (with slash-menu autocomplete for `/model` values).
- The on-launch provider picker (`_start_provider_picker`) also asks the user to type a provider name.
- There is an existing modal pattern: `rickshaw/keybindings_modal.py` uses `ModalScreen` with `Static`/`Markdown` content.
- Provider lists come from built-ins (`rickshaw_ai._builtins.default_providers`) plus configured profiles (`cfg.providers`).
- Model lists come from `provider.available_models()` (network call for OpenAI-compatible endpoints; static catalog for some).

## Proposed solution (post-interview)

Introduce a generic, reusable `SelectionModal` Textual `ModalScreen` in a new module `rickshaw/selection_modal.py`, and wire it into `rickshaw/tui.py`:

1. **Modal widget** (`SelectionModal`):
   - Centered overlay, similar styling to `KeybindingsModal`.
   - Title, scrollable list of options, footer hint.
   - No filter/search input; navigation is `Tab`/`Shift+Tab` and `Up`/`Down`, selection is `Enter`, cancel/back is `Esc`.
   - Each row shows the option value plus minimal metadata (provider protocol; model context window and price estimate). Long rows truncate with ellipsis in narrow terminals.
   - Supports loading states and inline error states with Retry/Back actions.

2. **Flows replaced**:
   - On-launch picker: open provider step if no provider is configured.
   - `/settings`: open the modal on the provider step; after provider selection advance to model; after model selection advance to effort only if multiple effort levels are supported.
   - `/provider`: open the provider step.
   - `/model`: open the model step for the current provider; if no provider is active, fall back to the full `/settings` flow.
   - `/effort`: open the effort step for the current provider/model; if none are active, fall back to the full `/settings` flow.
   - Typed arguments to `/provider`, `/model`, and `/effort` are rejected with a help message.

3. **Data source**:
   - Provider modal: built-in provider ids from `rickshaw_ai._builtins.default_providers()` only (custom `settings.json` providers are excluded from the modal but remain supported via `/provider add`).
   - Model modal: call `provider.available_models()`; show loading state and errors inline.
   - Effort modal: use `provider.capabilities().effort_levels` (or all three levels when empty/unreported, with unsupported ones noted).

4. **Affected modules**:
   - `rickshaw/tui.py` — replace `_start_provider_picker`, `_start_model_picker`, `/settings`, `/provider`, `/model`, `/effort` flows with modal calls.
   - `rickshaw/selection_modal.py` — new reusable modal.
   - `rickshaw/keybindings_modal.py` — minor CSS alignment for consistent modal chrome.
   - `tests/test_tui.py` — add tests for modal open/close/selection and command changes.
   - `README.md` — update command descriptions.

## Rough user journey

1. User runs `rickshaw` with no persisted provider.
2. A modal appears on the "Select provider" step listing built-in providers.
3. User navigates to `anthropic` and presses Enter.
4. If `anthropic` requires OAuth and has no credential, the existing OAuth flow runs, then the modal advances to the model step.
5. The modal advances to "Select model" and lists available models (loading state shown while fetching).
6. User selects `claude-sonnet-4-20250514` and presses Enter.
7. If the selected model supports multiple effort levels, the modal advances to "Select effort"; otherwise it closes.
8. The modal closes, provider/model/effort are applied, settings are persisted, the status bar updates, and the user returns to the prompt.

## Assumptions (resolved)

All assumptions have been resolved through the interview and are recorded in the Decisions Log below.

## Decisions log

| ID | Decision | Alternatives | Reasoning |
|----|----------|--------------|-----------|
| D1 | All provider/model selection flows use the modal overlay: on-launch picker, `/settings`, `/provider`, and `/model`. | Settings + on-launch only; settings only; replace typed args everywhere. | User wants a consistent, no-typing experience across all selection surfaces. |
| D2 | `/settings` uses one modal that starts on provider list and advances to model list after a provider is selected. | Two separate modals; one two-pane modal. | User wants the current sequential wizard preserved but without the separate-modal flash. |
| D3 | `/provider`, `/model`, and `/effort` become no-argument commands that open the corresponding step of the modal picker: `/provider` opens the provider step, `/model` opens the model step for the current provider, and `/effort` opens the effort step for the current provider/model. Typed arguments are rejected with a help message. | Always open modal but ignore args; use arg as preselection; direct switch on exact match. | User wants a clean, no-typing command surface. |
| D4 | The modal has no filter/search input; users navigate only with Up/Down and select with Enter. | Always-visible filter; / toggle; type-to-jump. | User wants a simple, no-typing picker. |
| D5 | The modal shows a loading state while fetching models and displays errors inline with a way to retry or go back. | Fetch before opening; pre-fetch and cache; skip step on failure. | User wants transparent feedback and the ability to recover without leaving the modal. |
| D6 | OAuth-capable built-ins always appear in the provider picker; selecting one without a credential triggers the existing OAuth flow before continuing to model selection. | Skip model step for OAuth; disable if not authenticated; show tag and require /login. | User wants all providers discoverable and OAuth handled automatically. |
| D7 | Local providers always show the model step, even when only one model is available. | Auto-select single model; auto-select if previously persisted. | User wants consistent, transparent selection for all providers. |
| D8 | User-defined custom providers from `settings.json` are not shown in the modal picker; the underlying custom-provider feature remains available via `/provider add` and manual config. | Show all configured providers; remove custom-provider feature entirely. | User wants the modal picker limited to built-ins for now; removing the feature would be a breaking, out-of-scope change. |
| D9 | The modal supports `Tab`/`Shift+Tab` and `Up`/`Down` for navigation, `Enter` to select, and `Esc` to cancel. | Up/Down/Enter/Esc only; add j/k and q; all common bindings. | User wants consistency with the existing slash-menu Tab cycling plus standard arrow-key list navigation. |
| D10 | `/settings` is a two- or three-step modal: provider → model → effort (only if the selected model/provider reports multiple supported effort levels; otherwise effort stays at the current/default value). | Provider+model only; show effort but not editable. | User wants effort configurable in /settings when meaningful, but not forced into an extra step for providers with a single effort level. |
| D11 | When a provider lists zero models, the modal shows an empty state with an actionable hint and a way to go back to the provider list. | Close and write error; auto-retry with hint. | User wants to recover without leaving the modal and pick another provider. |
| D12 | When `available_models()` raises an exception, the modal shows the error inline with Retry and Back options. | Close and show error; mark provider as failed. | User wants to recover from transient failures without leaving the modal. |
| D13 | `Esc` navigates back one step in the modal (model → provider, effort → model); `Esc` on the provider step closes the modal with no changes applied. | Esc always closes; Esc confirms previous choices. | User wants to correct mistakes without starting over, while preserving a safe cancel path. |
| D14 | When the selected provider/model does not support the current effort, effort is auto-reset to `medium` and a note is shown in the transcript. | Include effort step on mismatch only; keep current effort. | User wants to avoid provider errors while keeping the existing reconciliation behavior. |
| D15 | `/model` with no active provider opens the full `/settings` modal (provider → model, and effort if applicable). | Show warning; open empty model modal. | User wants a forgiving path that doesn't require remembering /settings. |
| D16 | The modal shrinks to fit narrow terminals, truncating long names with ellipsis rather than requiring a minimum size. | Full-screen below threshold; show 'terminal too narrow'. | User wants the picker to remain usable in constrained terminal sizes. |
| D17 | The modal pre-selects the currently active provider/model when one exists (including for /settings, /provider, /model, and on-launch if a provider is already set). | Start at top; pre-select only for /model and /provider. | User wants the current state reflected so switching is fast and predictable. |
| D18 | Provider rows show the protocol tag; model rows show context window and price estimate. Long rows are truncated with ellipsis in narrow terminals. | Names only; full metadata with all fields. | User wants useful context without modal clutter. |
| D19 | Models are sorted alphabetically within the picker. | Curated/builtin order; capability grouping. | User wants a predictable, scannable list. |
| D20 | The bottom hint bar shows a context-aware picker hint (e.g. 'tab/up/down navigate · enter select · esc back/close') that updates per step. | Reuse overlay hint; no hint change. | User wants discoverable controls for the modal. |
| D21 | Implement a generic, reusable `SelectionModal` that accepts a list of options and a callback. | Dedicated provider/model modal. | User wants a reusable component and consistency across selection surfaces. |
| D22 | The generic `SelectionModal` is used for provider, model, and effort pickers. The existing inline slash-command menu remains unchanged; `/effort` typed-value autocomplete is removed because `/effort` opens the modal. | Replace slash menu too; keep /effort autocomplete. | User wants a single picker component for provider/model/effort while preserving the fast slash-command workflow. |
| D23 | When the current provider/model supports only one effort level, `/effort` still opens the effort modal showing that single level with an explanatory note. | Skip modal; show all levels disabled. | User wants transparency even when no real choice exists. |
| D24 | `/effort` with no active provider/model opens the full `/settings` modal (provider → model → effort). | Show warning. | User wants a forgiving path consistent with `/model`. |

## Edge cases (resolved)

- **Provider with zero available models**: Show empty state with actionable hint and a way to go back to the provider list (D11).
- **Provider whose `available_models()` raises**: Show error inline with Retry and Back options (D12).
- **User cancels mid-flow**: `Esc` goes back one step; on the provider step it closes with no changes (D13).
- **Switching provider/model with effort mismatch**: Auto-reset effort to `medium` and show a note (D14).
- **Running `/model` with no provider**: Open the full `/settings` modal (D15).
- **Running `/effort` with no provider/model**: Open the full `/settings` modal (D24).
- **Terminal too narrow**: Shrink modal width and truncate long rows with ellipsis (D16).
- **Current provider/model pre-selection**: Modal pre-selects the active item when one exists (D17).
