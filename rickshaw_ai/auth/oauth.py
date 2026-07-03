"""OAuth flows and token refresh.

Implements authorization-code + PKCE and device-code flows. The *app* injects
the interactive UX (open a browser, capture a redirect, or prompt the user to
paste a code); this module owns the protocol, token exchange, and — crucially —
token *refresh*, which runs inside ``CredentialStore.modify`` so a rotated token
is never double-refreshed.
"""

from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from typing import Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import httpx

from rickshaw_ai.credentials.types import OAuthCredential
from rickshaw_ai.errors import AuthError
from rickshaw_ai.registry import OAuthConfig

#: Called with the authorize URL; the app opens it (browser, print, etc.).
OpenBrowser = Callable[[str], object]
#: Awaitable that returns the authorization code (from redirect capture/paste).
PromptCode = Callable[[], Awaitable[str]]


def generate_pkce() -> tuple[str, str]:
    """Return ``(verifier, challenge)`` for a PKCE (S256) exchange."""
    verifier = base64.urlsafe_b64encode(os.urandom(48)).rstrip(b"=").decode()
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return verifier, challenge


def build_authorize_url(
    config: OAuthConfig, *, state: str, code_challenge: str | None
) -> str:
    params = {
        "response_type": "code",
        "client_id": config.client_id,
        "scope": " ".join(config.scopes),
        "state": state,
    }
    if config.redirect_uri:
        params["redirect_uri"] = config.redirect_uri
    if code_challenge:
        params["code_challenge"] = code_challenge
        params["code_challenge_method"] = "S256"
    query = "&".join(f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in params.items())
    sep = "&" if "?" in config.authorize_url else "?"
    return f"{config.authorize_url}{sep}{query}"


def _parse_callback(raw: str) -> tuple[str, str | None]:
    """Extract ``(authorization_code, state)`` from a callback value.

    Accepted formats:

    * Full redirect URL with ``?code=…&state=…`` query parameters.
    * ``CODE#STATE`` fragment.
    * Bare ``CODE``.
    """
    if "?" in raw:
        qs = parse_qs(urlparse(raw).query) if "://" in raw else parse_qs(raw.split("?", 1)[1])
        code = qs.get("code", [None])[0]
        returned_state = qs.get("state", [None])[0]
        if code:
            return code, returned_state

    if "#" in raw:
        parts = raw.split("#", 1)
        return parts[0].split("&", 1)[0], parts[1].split("&", 1)[0] or None

    return raw.split("&", 1)[0], None


def _credential_from_token(data: dict) -> OAuthCredential:
    expires_in = data.get("expires_in")
    expires = int((time.time() + float(expires_in)) * 1000) if expires_in else None
    return OAuthCredential(
        access=data["access_token"],
        refresh=data.get("refresh_token"),
        expires=expires,
    )


@dataclass
class OAuthClient:
    """Executes OAuth flows and refresh for one provider."""

    config: OAuthConfig
    http: httpx.AsyncClient

    async def login_auth_code(
        self, *, open_browser: OpenBrowser, prompt_code: PromptCode
    ) -> OAuthCredential:
        verifier = challenge = None
        if self.config.use_pkce:
            verifier, challenge = generate_pkce()
        state = base64.urlsafe_b64encode(os.urandom(16)).rstrip(b"=").decode()
        url = build_authorize_url(self.config, state=state, code_challenge=challenge)
        open_browser(url)
        raw = (await prompt_code()).strip()
        code, returned_state = _parse_callback(raw)
        if returned_state is not None and returned_state != state:
            raise AuthError(
                "OAuth state mismatch — possible CSRF; please retry login"
            )

        form = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": self.config.client_id,
        }
        if self.config.redirect_uri:
            form["redirect_uri"] = self.config.redirect_uri
        if verifier:
            form["code_verifier"] = verifier
        return await self._token_request(form)

    async def login_device_code(
        self, *, show_user_code: Callable[[str, str], object], poll_interval: float = 5.0
    ) -> OAuthCredential:
        if not self.config.device_code_url:
            raise AuthError("provider has no device_code_url configured")
        resp = await self.http.post(
            self.config.device_code_url,
            data={"client_id": self.config.client_id, "scope": " ".join(self.config.scopes)},
        )
        resp.raise_for_status()
        payload = resp.json()
        device_code = payload["device_code"]
        show_user_code(payload["user_code"], payload.get("verification_uri", ""))
        interval = float(payload.get("interval", poll_interval))
        deadline = time.time() + float(payload.get("expires_in", 900))
        while time.time() < deadline:
            import asyncio

            await asyncio.sleep(interval)
            token_resp = await self.http.post(
                self.config.token_url,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": self.config.client_id,
                },
            )
            data = token_resp.json()
            if token_resp.status_code == 200 and "access_token" in data:
                return _credential_from_token(data)
            if data.get("error") not in ("authorization_pending", "slow_down"):
                raise AuthError(f"device authorization failed: {data.get('error')}")
        raise AuthError("device authorization timed out")

    async def refresh(self, credential: OAuthCredential) -> OAuthCredential:
        """Exchange a refresh token for a fresh access token.

        On failure raises :class:`AuthError`. The caller must NOT fall back to an
        environment key.
        """
        if not credential.refresh:
            raise AuthError("no refresh token available; re-authentication required")
        form = {
            "grant_type": "refresh_token",
            "refresh_token": credential.refresh,
            "client_id": self.config.client_id,
        }
        new = await self._token_request(form)
        # Preserve the old refresh token if the provider didn't rotate it.
        if new.refresh is None:
            new = new.model_copy(update={"refresh": credential.refresh})
        new = new.model_copy(update={"env": credential.env})
        return new

    async def _token_request(self, form: dict[str, str]) -> OAuthCredential:
        try:
            resp = await self.http.post(self.config.token_url, data=form)
        except httpx.HTTPError as exc:  # pragma: no cover - network
            raise AuthError(f"token request failed: {exc}") from exc
        if resp.status_code != 200:
            raise AuthError(
                f"token request rejected (status {resp.status_code}): {resp.text}",
                status_code=resp.status_code,
            )
        return _credential_from_token(resp.json())
