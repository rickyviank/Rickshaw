"""Reusable multi-step selection modal for Rickshaw.

The modal is a Textual ``ModalScreen`` that presents a scrollable list of
options. It supports multiple sequential steps (provider → model → effort),
loading states for options that must be fetched, and inline error/empty states
with retry and back actions.

Example::

    from rickshaw.selection_modal import SelectionModal, SelectionStep

    def on_advance(step_id: str, value: str, selections: dict[str, str], modal):
        if step_id == "provider":
            return SelectionStep(
                step_id="model",
                title="Select model",
                loader=lambda: [(m, m) for m in fetch_models(value)],
            )
        return None

    app.push_screen(
        SelectionModal(
            SelectionStep(step_id="provider", title="Select provider", options=[...]),
            on_advance=on_advance,
        ),
        on_result,
    )
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from textual import work
from rich.markup import escape


@dataclass
class SelectionStep:
    """Configuration for one step of the selection modal.

    ``options`` are rendered immediately. If ``options`` is empty and a
    ``loader`` is provided, the modal shows a loading state and calls the
    loader in a worker thread.
    """

    step_id: str
    title: str
    options: list[tuple[str, str]] = field(default_factory=list)
    loader: Callable[[], list[tuple[str, str]]] | None = None
    current: str | None = None
    hint: str = ""
    error_message: str = ""
    empty_message: str = "No options available."


class SelectionModal(ModalScreen[dict[str, str] | None]):
    """A centered, multi-step selection modal.

    Returns a mapping ``step_id -> selected_value`` when the user completes all
    steps, or ``None`` if the user cancels from the first step.

    Navigation: ``Up``/``Down``/``Tab``/``Shift+Tab`` move the highlight,
    ``Enter`` accepts, ``Escape`` goes back one step (or closes on the first
    step).
    """

    DEFAULT_CSS = """
    SelectionModal {
        align: center middle;
        background: $background 60%;
    }
    SelectionModal > Vertical {
        width: 80;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $background 80%;
        background: $surface;
    }
    SelectionModal .title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }
    SelectionModal .list {
        height: auto;
        max-height: 70%;
    }
    SelectionModal .row {
        padding: 0 1;
    }
    SelectionModal .row-selected {
        background: $primary-darken-2;
        color: $text;
        text-style: bold;
    }
    SelectionModal .row-unselected {
        color: $text-muted;
    }
    SelectionModal .message {
        text-align: center;
        color: $warning;
        margin: 1 0;
    }
    SelectionModal .footer {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }
    """

    _BACK_VALUE = "__back__"
    _RETRY_VALUE = "__retry__"

    def __init__(
        self,
        initial_step: SelectionStep,
        on_advance: Callable[
            [str, str, dict[str, str], "SelectionModal"],
            SelectionStep | None,
        ]
        | None = None,
    ) -> None:
        super().__init__()
        self._on_advance = on_advance
        self._step_history: list[SelectionStep] = [initial_step]
        self._selections: dict[str, str] = {}
        self._index = 0
        self._loading = False

    @property
    def _current_step(self) -> SelectionStep:
        return self._step_history[-1]

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("", classes="title")
            with VerticalScroll(classes="list"):
                yield Static("", classes="rows")
            yield Static("", classes="footer")

    def on_mount(self) -> None:
        self._maybe_load()

    def on_key(self, event) -> None:
        if self._loading:
            if event.key == "escape":
                self._back()
                event.stop()
            return

        if event.key in ("up", "shift+tab"):
            self._move(-1)
            event.stop()
        elif event.key in ("down", "tab"):
            self._move(1)
            event.stop()
        elif event.key == "enter":
            self._select()
            event.stop()
        elif event.key == "escape":
            self._back()
            event.stop()

    def _move(self, direction: int) -> None:
        options = self._visible_options()
        if not options:
            return
        self._index = max(0, min(len(options) - 1, self._index + direction))
        self._update_view()

    def _visible_options(self) -> list[tuple[str, str]]:
        step = self._current_step
        if step.error_message:
            opts = [
                (self._RETRY_VALUE, "Retry"),
                (self._BACK_VALUE, "Back"),
            ]
        elif step.options:
            opts = list(step.options)
        else:
            opts = [(self._BACK_VALUE, "Back")]
        return opts

    def _select(self) -> None:
        options = self._visible_options()
        if not options:
            return
        value, _ = options[self._index]

        if value == self._BACK_VALUE:
            self._back()
            return
        if value == self._RETRY_VALUE:
            self._retry()
            return

        step = self._current_step
        self._selections[step.step_id] = value

        if self._on_advance is None:
            self.dismiss(dict(self._selections))
            return

        next_step = self._on_advance(step.step_id, value, dict(self._selections), self)
        if next_step is None:
            self.dismiss(dict(self._selections))
            return

        self._step_history.append(next_step)
        self._index = 0
        self._maybe_load()

    def _back(self) -> None:
        if len(self._step_history) > 1:
            self._step_history.pop()
            step = self._current_step
            self._selections.pop(step.step_id, None)
            self._index = 0
            self._maybe_load()
        else:
            self.dismiss(None)

    def _retry(self) -> None:
        step = self._current_step
        step.error_message = ""
        step.options = []
        self._index = 0
        self._maybe_load()

    def _maybe_load(self) -> None:
        step = self._current_step
        if not step.options and step.loader is not None:
            self._loading = True
            self._update_view()
            self._load_step(step)
        else:
            self._loading = False
            self._set_initial_index()
            self._update_view()

    def _set_initial_index(self) -> None:
        step = self._current_step
        current = step.current
        options = self._visible_options()
        if current and options:
            for i, (value, _) in enumerate(options):
                if value == current:
                    self._index = i
                    return
        self._index = 0

    @work(thread=True, exclusive=True, group="selection")
    def _load_step(self, step: SelectionStep) -> None:
        try:
            options = step.loader()
        except Exception as exc:
            self.app.call_from_thread(self._set_error, str(exc))
            return
        self.app.call_from_thread(self._set_loaded, options)

    def _set_error(self, message: str) -> None:
        self._loading = False
        self._current_step.error_message = message
        self._current_step.options = []
        self._index = 0
        self._update_view()

    def _set_loaded(self, options: list[tuple[str, str]]) -> None:
        self._loading = False
        self._current_step.options = options
        self._set_initial_index()
        self._update_view()

    def set_step_options(self, options: list[tuple[str, str]]) -> None:
        """Update the current step's options from an external loader.

        This can be used by callers who want to manage their own worker and
        call back into the modal.
        """
        self._set_loaded(options)

    def set_step_error(self, message: str) -> None:
        """Set an error message on the current step."""
        self._set_error(message)

    def _update_view(self) -> None:
        title = self.query_one(".title", Static)
        rows = self.query_one(".rows", Static)
        footer = self.query_one(".footer", Static)

        step = self._current_step
        title.update(escape(step.title))

        if self._loading:
            rows.update("Loading…")
            footer.update(escape(step.hint or "esc cancel"))
            return

        if step.error_message:
            lines = [f"[b]Error:[/b] {escape(step.error_message)}"]
            options = self._visible_options()
        elif not step.options:
            lines = [escape(step.empty_message)]
            options = self._visible_options()
        else:
            lines = []
            options = step.options

        for i, (value, label) in enumerate(options):
            classes = "row-selected" if i == self._index else "row-unselected"
            lines.append(f"[{classes}]{escape(label)}[/]")

        rows.update("\n".join(lines))
        footer.update(escape(step.hint or "tab/up/down navigate · enter select · esc back/close"))

    def update_hint(self, hint: str) -> None:
        """Update the footer hint text."""
        self._current_step.hint = hint
        self.query_one(".footer", Static).update(escape(hint))
