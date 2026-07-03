"""Unified error taxonomy for :mod:`rickshaw_ai`.

Every provider adapter maps its wire-level failures onto this single hierarchy
so callers can reason about errors without knowing which provider produced them.
Each error carries provider/model/request context and a ``retryable`` flag that
drives the shared :class:`~rickshaw_ai.registry.RetryPolicy`.
"""

from __future__ import annotations

from typing import Any


class RickshawAIError(Exception):
    """Base class for every error raised by :mod:`rickshaw_ai`."""

    #: Whether a shared retry policy may retry the operation that raised this.
    default_retryable: bool = False

    def __init__(
        self,
        message: str,
        *,
        provider_id: str | None = None,
        model_id: str | None = None,
        status_code: int | None = None,
        request_id: str | None = None,
        retryable: bool | None = None,
        retry_after: float | None = None,
        raw: Any = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.provider_id = provider_id
        self.model_id = model_id
        self.status_code = status_code
        self.request_id = request_id
        self.retryable = self.default_retryable if retryable is None else retryable
        self.retry_after = retry_after
        self.raw = raw

    def __str__(self) -> str:  # pragma: no cover - trivial
        bits = [self.message]
        if self.provider_id:
            bits.append(f"provider={self.provider_id}")
        if self.model_id:
            bits.append(f"model={self.model_id}")
        if self.status_code is not None:
            bits.append(f"status={self.status_code}")
        if self.request_id:
            bits.append(f"request_id={self.request_id}")
        return " ".join(bits) if len(bits) == 1 else f"{bits[0]} ({', '.join(bits[1:])})"


class AuthError(RickshawAIError):
    """Missing/invalid credentials, or a failed OAuth refresh.

    Never retried and never falls back to an environment key: a stored
    credential owns its provider (see :mod:`rickshaw_ai.auth.resolver`).
    """

    default_retryable = False


class RateLimitError(RickshawAIError):
    """HTTP 429 — too many requests. Retryable, honoring ``retry_after``."""

    default_retryable = True


class OverloadedError(RickshawAIError):
    """Provider temporarily overloaded (e.g. Anthropic 529, some 503s)."""

    default_retryable = True


class InvalidRequestError(RickshawAIError):
    """HTTP 400/422 — a malformed request. Not retryable."""

    default_retryable = False


class NotFoundError(RickshawAIError):
    """HTTP 404 — unknown model or route. Not retryable."""

    default_retryable = False


class ContextLengthExceededError(InvalidRequestError):
    """The request exceeded the model's context window. Not retryable."""

    default_retryable = False


class ContentFilterError(RickshawAIError):
    """The provider refused/blocked the content. Not retryable."""

    default_retryable = False


class TimeoutError(RickshawAIError):
    """The request timed out. Retryable."""

    default_retryable = True


class ConnectionError(RickshawAIError):
    """A transport-level connection failure. Retryable."""

    default_retryable = True


class ProviderError(RickshawAIError):
    """A 5xx or otherwise unclassified provider failure. Retryable."""

    default_retryable = True


class ToolInputError(RickshawAIError):
    """A tool call's arguments failed schema validation. Not retryable."""

    default_retryable = False


def classify_status(status_code: int) -> type[RickshawAIError]:
    """Map an HTTP *status_code* to the most specific error class."""
    if status_code in (401, 403):
        return AuthError
    if status_code == 404:
        return NotFoundError
    if status_code == 429:
        return RateLimitError
    if status_code == 529:
        return OverloadedError
    if status_code in (400, 422):
        return InvalidRequestError
    if status_code >= 500:
        return ProviderError
    return ProviderError
