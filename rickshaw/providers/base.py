"""Normalized types and abstract provider interface."""

from __future__ import annotations

import enum
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

_log = logging.getLogger(__name__)


class Effort(enum.Enum):
    """Reasoning effort level requested for a completion."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass
class Message:
    """A single message in a conversation."""

    role: str
    content: str


@dataclass
class TokenUsage:
    """Token counts for a completion."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


@dataclass
class ToolSpec:
    """Description of a tool the model may call.

    ``category`` classifies the tool (e.g. ``"memory"`` vs ``"general"``) so the
    orchestrator can apply category-specific handling. ``side_effect`` marks
    whether invoking the tool mutates state: read-only tools (``side_effect=
    False``) do not count against the orchestrator's bounded tool-round budget.
    """

    name: str
    description: str
    parameters: dict[str, Any]
    category: str = "general"
    side_effect: bool = True


@dataclass
class ToolCall:
    """A normalized tool/function call returned by the model.

    This is a pure, vendor-neutral data container. Provider-specific parsing
    (e.g. from OpenAI's wire format) lives on each provider via a
    ``_parse_tool_calls`` method, not on this dataclass.
    """

    id: str
    name: str
    arguments: dict[str, Any]
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Response:
    """Normalized response from any LLM provider."""

    text: str
    model: str
    usage: TokenUsage = field(default_factory=TokenUsage)
    effort: Effort = Effort.MEDIUM
    raw: dict[str, Any] = field(default_factory=dict)
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass
class Capabilities:
    """Structured description of what a provider supports."""

    streaming: bool = False
    function_calling: bool = False
    vision: bool = False
    embeddings: bool = False
    max_context_tokens: int = 0
    effort_levels: list[Effort] = field(default_factory=lambda: list(Effort))


class EmbeddingMixin:
    """Optional mixin for providers that support embeddings."""

    def embed(self, text: str) -> list[float]:
        """Return an embedding vector for *text*.

        Providers that support embeddings should override this method and
        report ``embeddings=True`` in :meth:`capabilities`.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not support embeddings"
        )


class LLMProvider(ABC):
    """Abstract base class every provider must implement."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier used by the factory / CLI (e.g. ``'openai'``)."""

    @abstractmethod
    def complete(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Response:
        """Send *messages* and return a normalized :class:`Response`.

        *tools* advertises the tool specifications available to the model.
        Providers that do not support function-calling should ignore it.

        *tool_choice* controls whether the model is encouraged, required, or
        forbidden from selecting a tool. Accepts ``"auto"`` (model decides),
        ``"none"`` (never call a tool), ``"required"`` (must call a tool), or
        ``None`` (provider default). It only has effect when *tools* is set.
        """

    def stream(
        self,
        messages: list[Message],
        effort: Effort = Effort.MEDIUM,
        tools: list[ToolSpec] | None = None,
        tool_choice: str | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Yield incremental text chunks.

        The default implementation falls back to :meth:`complete` and yields
        the full text as a single chunk, so providers without native streaming
        still satisfy the interface.
        """
        response = self.complete(
            messages, effort=effort, tools=tools, tool_choice=tool_choice, **kwargs
        )
        yield response.text

    @abstractmethod
    def available_models(self) -> list[str]:
        """Return a list of model identifiers this provider can serve."""

    # ------------------------------------------------------------------
    # Caching helper for available_models
    # ------------------------------------------------------------------

    _models_cache: list[str] | None = None

    def _cached_available_models(
        self,
        fetcher: Callable[[], list[str]],
        *,
        cache_key: str,
        is_local: bool,
    ) -> list[str]:
        """Shared caching logic for :meth:`available_models`.

        *fetcher* performs the actual (possibly network) retrieval.
        *cache_key* is ``"provider_name:base_url"`` for disk persistence.
        *is_local* controls error messaging on failure.

        Contract:
        - At most one *fetcher* call per provider instance lifetime.
        - On success the result is stored both in-memory and on disk.
        - On failure, fall back to the disk cache.
        - If there is no disk cache and the endpoint is remote, raise an
          instructive error asking the user to connect to the internet.
        - If the endpoint is local, re-raise the original error so the user
          knows their inference server isn't reachable.
        """
        if self._models_cache is not None:
            return list(self._models_cache)

        from rickshaw.settings import load_model_cache, save_model_cache

        try:
            models = fetcher()
        except Exception as fetch_err:
            # Network / connection failure — try the disk cache.
            disk = load_model_cache()
            cached = disk.get(cache_key)
            if cached is not None:
                _log.warning(
                    "Model fetch failed for %s; using cached list (%d models)",
                    cache_key,
                    len(cached),
                )
                self._models_cache = list(cached)
                return list(self._models_cache)

            if is_local:
                raise ConnectionError(
                    f"Could not reach local inference server at "
                    f"{cache_key.split(':', 1)[-1]}: {fetch_err}"
                ) from fetch_err

            raise ConnectionError(
                f"Could not fetch models from {cache_key.split(':', 1)[-1]} "
                f"and no cached model list is available. "
                f"Please connect to the internet and try again."
            ) from fetch_err

        self._models_cache = list(models)

        # Persist to disk (merge with existing cache entries).
        disk = load_model_cache()
        disk[cache_key] = list(models)
        save_model_cache(disk)

        return list(self._models_cache)

    @abstractmethod
    def validate(self) -> None:
        """Verify credentials and connectivity.

        Should raise a descriptive exception on failure so the CLI can
        surface a clear error early.
        """

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """Return a :class:`Capabilities` object describing this provider."""
