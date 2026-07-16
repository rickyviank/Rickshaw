"""Reusable Textual modal/screen for displaying Rickshaw keybindings.

The overlay is opened by the ``/keybindings`` slash command (PRD
``docs/prd/tui-keybindings/PRD.md``) and can be dismissed with ``Esc``,
``q``, or ``?``.
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Markdown, Static


DEFAULT_BINDINGS: dict[str, list[tuple[str, str]]] = {
    "Prompt": [
        ("Enter", "Submit message (accept selected item if slash menu is open)"),
        ("Shift+Enter / Ctrl+J", "Insert a newline"),
        ("Tab", "Move focus to the newest turn block"),
        ("Up / Down", "Navigate command history"),
        (
            "/",
            (
                "Open slash-command autocomplete: "
                "/help · /status · /settings · /models · /clear · "
                "/provider · /effort · /model · /login · /memory · "
                "/quit · /exit · /keybindings"
            ),
        ),
        ("Escape", "Interrupt running turn, close slash menu, or clear prompt text"),
        ("Ctrl+C", "Cancel running turn, or double-tap to quit"),
        ("Ctrl+L", "Redraw the screen"),
    ],
    "Turn block": [
        ("Tab / Shift+Tab", "Move focus forward/backward in the transcript ring"),
        ("Enter", "Expand trace and focus the first event"),
        ("Escape", "Return to prompt"),
    ],
    "Trace event": [
        ("Tab / Shift+Tab", "Move to next/previous event (or adjacent turn at boundaries)"),
        ("Enter", "Toggle the event's content"),
        ("Escape", "Collapse trace and return to the turn block"),
        ("R", "Toggle raw JSON for the whole trace block"),
    ],
    "Slash menu": [
        ("Tab", "Cycle the menu highlight"),
        ("Enter", "Accept the selected command"),
        ("Escape", "Close the menu"),
    ],
    "Overlay": [
        ("Escape / q / ?", "Close this overlay"),
    ],
}


def _format_bindings(bindings: str | dict[str, Any]) -> str:
    """Render bindings as Markdown.

    * If ``bindings`` is already a string, it is returned unchanged.
    * If it is a mapping, each key is a group heading and each value is an
      iterable of ``(key, action)`` pairs.
    """
    if isinstance(bindings, str):
        return bindings

    lines: list[str] = ["# Keybindings", ""]
    for group, entries in bindings.items():
        lines.append(f"## {group}")
        lines.append("")
        for entry in entries:
            if isinstance(entry, (list, tuple)) and len(entry) == 2:
                key, action = entry
                lines.append(f"- `{key}` — {action}")
            else:
                lines.append(f"- {entry}")
        lines.append("")
    return "\n".join(lines)


class KeybindingsModal(ModalScreen[None]):
    """A centered modal overlay that displays Rickshaw keybindings by group.

    Parameters
    ----------
    bindings:
        The keybindings to display. If ``None``, the default PRD keymap is
        used. Pass a Markdown string for full control, or a mapping of group
        names to lists of ``(key, action)`` tuples.

    Example
    -------
    Show the modal from an app::

        from rickshaw.keybindings_modal import KeybindingsModal

        def action_keybindings(self) -> None:
            self.push_screen(KeybindingsModal())
    """

    DEFAULT_CSS = """
    KeybindingsModal {
        align: center middle;
        background: $background 60%;
    }

    KeybindingsModal > Vertical {
        width: 80;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: thick $background 80%;
        background: $surface;
    }

    KeybindingsModal .title {
        text-align: center;
        text-style: bold;
        margin-bottom: 1;
    }

    KeybindingsModal .footer {
        text-align: center;
        color: $text-muted;
        margin-top: 1;
    }

    KeybindingsModal Markdown {
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape,q,question", "dismiss", "Close"),
    ]

    def __init__(
        self,
        bindings: str | dict[str, Any] | None = None,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._bindings_data = bindings if bindings is not None else DEFAULT_BINDINGS
        self._markdown = _format_bindings(self._bindings_data)

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static("Keybindings", classes="title")
            yield Markdown(self._markdown)
            yield Static("esc · q · ? close", classes="footer")

    def action_dismiss(self) -> None:
        """Close the modal and return ``None`` to the caller."""
        self.dismiss()
