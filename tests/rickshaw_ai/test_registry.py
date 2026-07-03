"""Registry invariants: only tool-calling models may be registered."""

from __future__ import annotations

import pytest

from rickshaw_ai import ModelInfo, ProviderInfo, builtin_models, create_models


def test_all_builtin_models_support_tools():
    models = builtin_models()
    assert models.list()
    assert all(m.supports_tools for m in models.list())


def test_builtin_ids_are_namespaced():
    models = builtin_models()
    for m in models.list():
        assert m.id == f"{m.provider_id}/{m.model}"


def test_registering_non_tool_model_raises():
    bad = ProviderInfo(
        id="bad",
        base_url="https://example.com",
        protocol="openai_compatible",
        models=[
            ModelInfo(
                id="bad/no-tools",
                provider_id="bad",
                model="no-tools",
                supports_tools=False,
            )
        ],
    )
    with pytest.raises(ValueError, match="does not support tools"):
        create_models(providers=[bad])


def test_get_unknown_model_raises():
    models = builtin_models()
    with pytest.raises(Exception, match="unknown model"):
        models.get("nope/nope")


def test_oauth_first_providers_present():
    models = builtin_models()
    for pid in ("anthropic", "openai", "copilot"):
        info = models.provider_info(pid)
        assert "oauth" in info.auth_methods
        assert info.oauth is not None
