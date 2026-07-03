"""Shared test fixtures.

Isolates each test's ``$HOME`` to a fresh temporary directory so nothing writes
the developer's real ``~/.rickshaw`` (settings + model cache). Without this,
tests that fetch and cache model lists leak state into later tests — e.g. the
offline ``/settings`` test would read a previously-cached model list instead of
hitting the intended "cannot list models" path.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))  # Windows parity
    yield
