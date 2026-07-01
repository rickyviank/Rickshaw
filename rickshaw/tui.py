"""Full-screen terminal UI for Rickshaw, built on Textual.

A Claude-Code / Codex-style TUI: a scrollable transcript, a pinned input at the
bottom, a status bar (provider · model · effort · tokens), streaming replies, a
"thinking" indicator, Esc to interrupt an in-flight turn, and slash-command
autocomplete. Every turn is routed through :meth:`Orchestrator.run_turn`, so the
semantic memory layer (remember / recall / forget) and graceful-degradation info
are active and surfaced.

Textual is an optional dependency. Install the extra to use the UI::

    pip install -e ".[tui]"

then launch::

    rickshaw-tui --provider openai --effort high

The module itself (and the branding constants below) import fine without Textual
installed — the framework is imported lazily, only when the app is built.
"""

from __future__ import annotations

import argparse
import sys

from rickshaw.cli import _EFFORT_NAMES, _build_provider, load_config
from rickshaw.config import RickshawConfig
from rickshaw.memory.service import MemoryService
from rickshaw.orchestrator import Orchestrator
from rickshaw.providers.base import Effort, LLMProvider
from rickshaw.providers.factory import get_provider

# Branding — module-level so cli.py can import and reuse them.
RICKSHAW_LOGO = "o--o  rickshaw"
RICKSHAW_SLOGAN = "your driver, your memory"
RICKSHAW_BANNER = f"{RICKSHAW_LOGO} \u00b7 {RICKSHAW_SLOGAN}"

# Where the memory layer persists across sessions (vs. the default ":memory:").
_DEFAULT_DB_PATH = "rickshaw_memory.db"

# Slash-commands, used for help text and inline autocomplete.
_COMMANDS = {
    "/help": "Show this help.",
    "/clear": "Clear the transcript.",
    "/effort": "/effort <low|medium|high> — set reasoning effort.",
    "/model": "/model [name] — show or switch the chat model.",
    "/memory": "List recently stored memories.",
    "/quit": "Exit.",
    "/exit": "Exit.",
}

_TEXTUAL_MISSING_MSG = (
    "The Rickshaw terminal UI requires Textual, which is not installed.\n"
    "Install the optional extra with:\n\n"
    '    pip install "rickshaw[tui]"\n'
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="rickshaw-tui",
        description="Full-screen terminal UI for the Rickshaw provider harness.",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Provider name (e.g. openai, devin). Overrides config/env.",
    )
    parser.add_argument(
        "--effort",
        choices=["low", "medium", "high"],
        default=None,
        help="Default reasoning effort level for the session.",
    )
    parser.add_argument(
        "--db-path",
        default=_DEFAULT_DB_PATH,
        help=(
            "SQLite path for the persistent memory layer "
            f"(default: {_DEFAULT_DB_PATH})."
        ),
    )
    return parser.parse_args(argv)


def _rebuild_provider(name: str, cfg: RickshawConfig, model: str) -> LLMProvider:
    """Build a provider with a model override (used by /model)."""
    if name == "openai":
        return get_provider(
            "openai",
            api_key=cfg.openai_api_key,
            base_url=cfg.openai_base_url,
            model=model,
            embedding_model=cfg.openai_embedding_model,
        )
    raise ValueError(f"switching models is not supported for provider {name!r}")


def make_app(
    orchestrator: Orchestrator,
    provider: LLMProvider,
    effort: Effort,
    cfg: RickshawConfig | None = None,
):
    """Build the Textual app instance. Imports Textual lazily.

    Kept as a factory (rather than a module-level class) so importing this
    module does not require Textual to be installed.
    """
    try:
        from textual import work
        from textual.app import App, ComposeResult
        from textual.binding import Binding
        from textual.containers import VerticalScroll
        from textual.suggester import SuggestFromList
        from textual.widgets import Footer, Input, Markdown, Static
    except ImportError as exc:  # pragma: no cover - exercised via message text
        raise SystemExit(_TEXTUAL_MISSING_MSG) from exc

    cfg = cfg or RickshawConfig()

    class RickshawTUI(App):
        """Textual application driving turns through the Orchestrator."""

        TITLE = "rickshaw"
        SUB_TITLE = RICKSHAW_SLOGAN
        CSS = """
        Screen { layout: vertical; }
        #banner {
            height: auto;
            padding: 1 2 0 2;
            color: $accent;
            text-style: bold;
        }
        #transcript { height: 1fr; padding: 0 2; }
        #transcript > Static { margin: 1 0 0 0; }
        #transcript > Markdown { margin: 0 0 0 0; }
        .user { color: $success; text-style: bold; }
        .meta { color: $text-muted; }
        .warn { color: $warning; }
        #status {
            height: 1;
            padding: 0 2;
            background: $panel;
            color: $text-muted;
        }
        #prompt { dock: bottom; margin: 0 0 1 0; }
        """

        BINDINGS = [
            Binding("escape", "interrupt", "Interrupt", show=True),
            Binding("ctrl+l", "clear", "Clear", show=True),
            Binding("ctrl+c", "quit", "Quit", show=True),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.orchestrator = orchestrator
            self.provider = provider
            self.effort = effort
            self.cfg = cfg
            self.orchestrator.effort = effort
            self._buffer = ""
            self._current_md: Markdown | None = None
            self._turn_active = False

        # ---- layout -----------------------------------------------------

        def compose(self) -> ComposeResult:
            yield Static(RICKSHAW_BANNER, id="banner")
            yield VerticalScroll(id="transcript")
            yield Static("", id="status")
            yield Input(
                placeholder="Message rickshaw…  (/help for commands)",
                id="prompt",
                suggester=SuggestFromList(sorted(_COMMANDS), case_sensitive=False),
            )
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_status()
            caps = self.provider.capabilities()
            levels = (
                ", ".join(e.value for e in caps.effort_levels)
                if caps.effort_levels
                else "(provider advertises no effort levels)"
            )
            self._write(
                f"Connected to [b]{self.provider.name}[/b]. "
                f"Supported effort: {levels}.",
                cls="meta",
            )
            self._write("Type a message, or /help for commands.", cls="meta")
            self.query_one("#prompt", Input).focus()

        # ---- transcript helpers ----------------------------------------

        def _write(self, text: str, cls: str = "") -> Static:
            """Append a plain (Rich-markup) line to the transcript."""
            widget = Static(text, classes=cls)
            self.query_one("#transcript", VerticalScroll).mount(widget)
            self._scroll_end()
            return widget

        def _begin_assistant(self) -> None:
            self._buffer = ""
            md = Markdown("")
            self.query_one("#transcript", VerticalScroll).mount(md)
            self._current_md = md
            self._scroll_end()

        def _scroll_end(self) -> None:
            self.query_one("#transcript", VerticalScroll).scroll_end(animate=False)

        def _refresh_status(self, note: str = "") -> None:
            model = getattr(self.provider, "_model", "") or self.provider.name
            bar = (
                f"provider: {self.provider.name}   model: {model}   "
                f"effort: {self.orchestrator.effort.value}"
            )
            if note:
                bar = f"{bar}   |   {note}"
            self.query_one("#status", Static).update(bar)

        # ---- input handling --------------------------------------------

        def on_input_submitted(self, event: Input.Submitted) -> None:
            value = event.value.strip()
            event.input.value = ""
            if not value:
                return
            if value.startswith("/"):
                self._handle_command(value)
                return
            if self._turn_active:
                self._write("A turn is already running; press Esc to interrupt.", "warn")
                return
            self._start_turn(value)

        def _handle_command(self, value: str) -> None:
            parts = value.split(maxsplit=1)
            cmd = parts[0].lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            if cmd in ("/quit", "/exit"):
                self.exit()
            elif cmd == "/help":
                self._cmd_help()
            elif cmd == "/clear":
                self.action_clear()
            elif cmd == "/effort":
                self._cmd_effort(arg)
            elif cmd == "/model":
                self._cmd_model(arg)
            elif cmd == "/memory":
                self._cmd_memory()
            else:
                self._write(f"Unknown command {cmd!r}. Try /help.", "warn")

        def _cmd_help(self) -> None:
            self._write("Commands:", "meta")
            for name, desc in _COMMANDS.items():
                self._write(f"  [b]{name}[/b] — {desc}", "meta")
            self._write("  Esc interrupts a running turn; Ctrl+C quits.", "meta")

        def _cmd_effort(self, arg: str) -> None:
            level = arg.lower()
            if level not in _EFFORT_NAMES:
                self._write(f"Invalid effort {arg!r}. Use: low, medium, high.", "warn")
                return
            new_effort = _EFFORT_NAMES[level]
            self.orchestrator.effort = new_effort
            caps = self.provider.capabilities()
            if caps.effort_levels and new_effort not in caps.effort_levels:
                self._write(
                    f"Warning: {self.provider.name} does not honor "
                    f"effort={new_effort.value}; it may be ignored.",
                    "warn",
                )
            self._refresh_status()
            self._write(f"Effort set to {new_effort.value}.", "meta")

        def _cmd_model(self, arg: str) -> None:
            if not arg:
                model = getattr(self.provider, "_model", "") or "(unknown)"
                self._write(f"Current model: {model}", "meta")
                return
            try:
                new_provider = _rebuild_provider(self.provider.name, self.cfg, arg)
            except Exception as exc:
                self._write(f"Cannot switch model: {exc}", "warn")
                return
            self.provider = new_provider
            self.orchestrator.provider = new_provider
            self._refresh_status()
            self._write(f"Model switched to {arg}.", "meta")

        def _cmd_memory(self) -> None:
            try:
                records = self.orchestrator.memory.store.all_records()
            except Exception as exc:  # pragma: no cover - defensive
                self._write(f"Could not read memory: {exc}", "warn")
                return
            if not records:
                self._write("No memories stored yet.", "meta")
                return
            self._write(f"Stored memories ({len(records)}):", "meta")
            for rec in records[-10:]:
                snippet = rec.text if len(rec.text) <= 100 else rec.text[:97] + "…"
                self._write(f"  · {snippet}", "meta")

        # ---- turn execution --------------------------------------------

        def _start_turn(self, text: str) -> None:
            self._turn_active = True
            self._write(f"you> {text}", "user")
            self._begin_assistant()
            self._refresh_status(note="thinking…")
            self.query_one("#prompt", Input).disabled = True
            self._run_turn(text)

        @work(thread=True, exclusive=True, group="turn")
        def _run_turn(self, text: str) -> None:
            def on_delta(chunk: str) -> None:
                if not self._turn_active:
                    return
                self.call_from_thread(self._append_delta, chunk)

            try:
                result = self.orchestrator.run_turn(text, on_delta=on_delta)
            except Exception as exc:  # keep the app alive on unexpected errors
                self.call_from_thread(self._turn_error, exc)
                return
            self.call_from_thread(self._turn_done, result)

        def _append_delta(self, chunk: str) -> None:
            self._buffer += chunk
            if self._current_md is not None:
                self._current_md.update(self._buffer)
            self._scroll_end()

        def _turn_done(self, result) -> None:
            if self._current_md is not None and self._buffer != result.text:
                # Non-streaming providers deliver everything in one delta; make
                # sure the final rendered text matches the result exactly.
                self._current_md.update(result.text)
            parts = [f"tool calls: {result.tool_calls_made}"]
            if result.usage is not None and result.usage.total_tokens:
                parts.append(f"{result.usage.total_tokens} tok")
            if result.degraded:
                parts.append("degraded (local memory)")
            for warning in result.warnings:
                parts.append(warning)
            self._write(f"[dim]{'  '.join(parts)}[/dim]")
            self._finish_turn()

        def _turn_error(self, exc: Exception) -> None:
            self._write(f"Error: {exc}", "warn")
            self._finish_turn()

        def _finish_turn(self) -> None:
            self._turn_active = False
            self._current_md = None
            self._refresh_status()
            prompt = self.query_one("#prompt", Input)
            prompt.disabled = False
            prompt.focus()

        # ---- actions ----------------------------------------------------

        def action_interrupt(self) -> None:
            if not self._turn_active:
                return
            self.workers.cancel_group(self, "turn")
            self._write("(interrupted)", "warn")
            self._finish_turn()

        def action_clear(self) -> None:
            self.query_one("#transcript", VerticalScroll).remove_children()
            self._write("Transcript cleared.", "meta")

    return RickshawTUI()


def _run_app(
    orchestrator: Orchestrator,
    provider: LLMProvider,
    effort: Effort,
    cfg: RickshawConfig,
) -> None:
    """Build and run the Textual app (separated out for testability)."""
    make_app(orchestrator, provider, effort, cfg).run()


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    cfg = load_config()

    provider_name = args.provider or cfg.provider
    effort = _EFFORT_NAMES.get(args.effort, cfg.effort) if args.effort else cfg.effort

    provider = _build_provider(provider_name, cfg)

    try:
        provider.validate()
    except Exception as exc:
        print(f"Provider validation failed ({provider_name}): {exc}", file=sys.stderr)
        print("Continuing anyway; calls may fail.\n", file=sys.stderr)

    memory = MemoryService(db_path=args.db_path)
    orchestrator = Orchestrator(provider=provider, memory=memory, effort=effort)

    _run_app(orchestrator, provider, effort, cfg)


if __name__ == "__main__":
    main()
