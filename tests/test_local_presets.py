"""Tests for the local provider presets and per-profile timeout config."""

from __future__ import annotations

import pytest

from rickshaw.config import (
    LOCAL_PRESET_NAMES,
    ProviderProfile,
    _parse_timeout,
    load_config,
    local_no_models_hint,
    local_server_down_hint,
)


@pytest.fixture(autouse=True)
def _isolated_settings(tmp_path, monkeypatch):
    """Point settings at an empty temp dir so user files don't leak in."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.chdir(tmp_path)


EXPECTED_PRESETS = {
    "llamacpp": "http://localhost:8080/v1",
    "mlx": "http://localhost:8080/v1",
    "ollama": "http://localhost:11434/v1",
    "lmstudio": "http://localhost:1234/v1",
}


def test_local_presets_registered():
    cfg = load_config()
    for name, base_url in EXPECTED_PRESETS.items():
        profile = cfg.providers[name]
        assert profile.base_url == base_url
        assert profile.wire_format == "openai"
        assert profile.model == ""
        assert profile.is_local_endpoint() is True


def test_local_preset_names_constant_matches_profiles():
    assert set(LOCAL_PRESET_NAMES) == set(EXPECTED_PRESETS)


def test_local_preset_api_key_env_named_but_not_required():
    cfg = load_config()
    for name in LOCAL_PRESET_NAMES:
        profile = cfg.providers[name]
        assert profile.api_key_env == f"{name.upper()}_API_KEY"
        assert profile.has_api_key() is False


def test_local_preset_base_url_env_override(monkeypatch):
    monkeypatch.setenv("MLX_BASE_URL", "http://localhost:9090/v1")
    cfg = load_config()
    assert cfg.providers["mlx"].base_url == "http://localhost:9090/v1"
    assert cfg.providers["llamacpp"].base_url == "http://localhost:8080/v1"


def test_settings_override_preserves_builtin_defaults(tmp_path, monkeypatch):
    settings_dir = tmp_path / ".rickshaw"
    settings_dir.mkdir()
    (settings_dir / "settings.json").write_text(
        '{"version": 1, "providers": {"ollama": {"timeout": 300}}}'
    )
    cfg = load_config()
    profile = cfg.providers["ollama"]
    assert profile.timeout == 300.0
    assert profile.base_url == "http://localhost:11434/v1"
    assert profile.api_key_env == "OLLAMA_API_KEY"


def test_timeout_default_is_none():
    cfg = load_config()
    for name in ("openai", "anthropic", *LOCAL_PRESET_NAMES):
        assert cfg.providers[name].timeout is None


@pytest.mark.parametrize("raw,expected", [
    (None, None),
    (300, 300.0),
    ("45.5", 45.5),
    (0, None),
    (-1, None),
    ("nope", None),
])
def test_parse_timeout(raw, expected):
    assert _parse_timeout(raw, "test") == expected


def test_profile_timeout_field_defaults_to_none():
    profile = ProviderProfile(
        base_url="http://localhost:8080/v1", model="", api_key_env="X",
    )
    assert profile.timeout is None


def test_local_hints_known_and_fallback():
    assert "llama-server" in local_server_down_hint("llamacpp")
    assert "ollama pull" in local_no_models_hint("ollama")
    assert local_server_down_hint("custom") == "is the server running?"
    assert "model" in local_no_models_hint("custom")
