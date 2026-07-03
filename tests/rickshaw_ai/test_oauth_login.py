"""OAuth interactive login: auth-code + PKCE and device-code flows."""

from __future__ import annotations

import httpx
import pytest
import respx

from rickshaw_ai import InMemoryCredentialStore, OAuthCredential, create_models
from rickshaw_ai.auth.oauth import generate_pkce
from rickshaw_ai.registry import ModelInfo, OAuthConfig, ProviderInfo

AUTHORIZE = "https://oauth.example/authorize"
TOKEN = "https://oauth.example/token"
DEVICE = "https://oauth.example/device"


def _provider(mode="auth_code"):
    return ProviderInfo(
        id="acme", base_url="https://api.acme.test", protocol="openai_compatible",
        auth_methods=["oauth"],
        oauth=OAuthConfig(
            authorize_url=AUTHORIZE, token_url=TOKEN, client_id="cid",
            scopes=["a", "b"], mode=mode, device_code_url=DEVICE,
        ),
        models=[ModelInfo(id="acme/m", provider_id="acme", model="m", supports_tools=True)],
    )


def test_pkce_is_deterministic_shape():
    verifier, challenge = generate_pkce()
    assert verifier and challenge and verifier != challenge


@respx.mock
async def test_auth_code_login_persists_credential():
    respx.post(TOKEN).mock(return_value=httpx.Response(
        200, json={"access_token": "acc", "refresh_token": "ref", "expires_in": 3600}))
    store = InMemoryCredentialStore()
    models = create_models(providers=[_provider()], credentials=store)

    seen = {}

    def _open(url):
        seen["url"] = url

    async def _code():
        return "the-code#state123"

    await models.login("acme", open_browser=_open, prompt_code=_code)

    assert seen["url"].startswith(AUTHORIZE)
    assert "code_challenge" in seen["url"]
    cred = await store.read("acme")
    assert isinstance(cred, OAuthCredential)
    assert cred.access == "acc"
    assert cred.refresh == "ref"


@respx.mock
async def test_device_code_login_polls_until_token():
    respx.post(DEVICE).mock(return_value=httpx.Response(200, json={
        "device_code": "dev", "user_code": "WXYZ", "verification_uri": "https://ex/verify",
        "interval": 0, "expires_in": 60}))
    respx.post(TOKEN).mock(side_effect=[
        httpx.Response(400, json={"error": "authorization_pending"}),
        httpx.Response(200, json={"access_token": "acc2", "expires_in": 3600}),
    ])
    store = InMemoryCredentialStore()
    models = create_models(providers=[_provider(mode="device_code")], credentials=store)

    shown = {}
    await models.login("acme", show_user_code=lambda code, uri: shown.update(code=code, uri=uri))

    assert shown["code"] == "WXYZ"
    cred = await store.read("acme")
    assert isinstance(cred, OAuthCredential)
    assert cred.access == "acc2"
