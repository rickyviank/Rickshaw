"""Persistent input history stored in ``~/.rickshaw/history``.

Every submitted entry — plain messages *and* slash commands — is appended,
newline-delimited, so the TUI can recall previous inputs with ``↑``/``↓``
across launches.  A rolling cap of :data:`HISTORY_CAP` entries keeps the file
bounded; the oldest lines are dropped once the cap is exceeded.

Reuses the ``~/.rickshaw`` directory convention from :mod:`rickshaw.settings`.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

#: Rolling cap on the number of stored entries (PRD D23.4).
HISTORY_CAP = 1000


def default_history_path() -> Path:
    """Return ``~/.rickshaw/history``."""
    return Path.home() / ".rickshaw" / "history"


def load_history(path: Path | None = None) -> list[str]:
    """Load stored entries oldest-first.

    Returns an empty list when the file does not exist or cannot be read.
    Blank lines are ignored.
    """
    path = path or default_history_path()
    if not path.is_file():
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
    except OSError:
        return []
    return [line for line in lines if line]


def append_history(entry: str, path: Path | None = None) -> None:
    """Append *entry* to the history file, enforcing :data:`HISTORY_CAP`.

    Empty/whitespace-only entries are ignored.  The whole file is rewritten
    atomically (temp file + :func:`os.replace`) with at most the most recent
    :data:`HISTORY_CAP` entries so a crash mid-write never corrupts it.
    """
    entry = entry.strip()
    if not entry:
        return
    path = path or default_history_path()
    entries = load_history(path)
    entries.append(entry)
    if len(entries) > HISTORY_CAP:
        entries = entries[-HISTORY_CAP:]
    _atomic_write(path, entries)


def _atomic_write(path: Path, entries: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent), prefix=".history-", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            for entry in entries:
                fh.write(entry)
                fh.write("\n")
        os.replace(tmp, str(path))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
