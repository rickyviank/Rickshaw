"""Type-tagged credentials, discriminated by ``type``.

The discriminator matches pi's ``auth.json`` so persisted credential files
interoperate. Each provider owns at most one credential.
"""

from __future__ import annotations

import time
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, Field


class ApiKeyCredential(BaseModel):
    """A static API key, optionally carrying provider-scoped env/config."""

    type: Literal["api_key"] = "api_key"
    key: str
    #: Provider-scoped env injected at request time (e.g. CLOUDFLARE_ACCOUNT_ID).
    env: dict[str, str] = Field(default_factory=dict)
    #: Provider-scoped config (e.g. gateway routing).
    config: dict[str, Any] = Field(default_factory=dict)


class OAuthCredential(BaseModel):
    """An OAuth token set with lazy refresh."""

    type: Literal["oauth"] = "oauth"
    access: str
    refresh: str | None = None
    #: Expiry as epoch milliseconds. ``None`` means "unknown / never expires".
    expires: int | None = None
    env: dict[str, str] = Field(default_factory=dict)

    def is_expired(self, *, leeway_seconds: int = 60) -> bool:
        if self.expires is None:
            return False
        return time.time() * 1000 >= (self.expires - leeway_seconds * 1000)


Credential = Annotated[
    Union[ApiKeyCredential, OAuthCredential],
    Field(discriminator="type"),
]
