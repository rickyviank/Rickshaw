"""Embedder protocol and implementations — local default + provider adapter."""

from __future__ import annotations

import hashlib
import struct
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rickshaw.providers.base import EmbeddingMixin

_DEFAULT_DIM = 64


class Embedder(ABC):
    """Protocol for producing embedding vectors from text."""

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Return a fixed-dimension embedding vector for *text*."""

    @property
    def dimension(self) -> int:
        """Return the dimensionality of produced vectors."""
        return _DEFAULT_DIM


class LocalEmbedder(Embedder):
    """Deterministic hashing-based pseudo-embedder for fully offline use.

    Uses SHA-256 to produce a repeatable vector from the input text.
    Not semantically meaningful, but deterministic and zero-dependency.
    """

    def __init__(self, dim: int = _DEFAULT_DIM) -> None:
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        # Expand the hash to fill the requested dimension
        needed_bytes = self._dim * 4  # 4 bytes per float32
        expanded = digest
        while len(expanded) < needed_bytes:
            expanded += hashlib.sha256(expanded).digest()
        # Unpack as floats in [-1, 1]
        raw = struct.unpack(f">{self._dim}I", expanded[: needed_bytes])
        # Normalize unsigned ints to [-1, 1]
        vec = [(v / (2**32 - 1)) * 2 - 1 for v in raw]
        # L2-normalize
        norm = sum(x * x for x in vec) ** 0.5
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec


class ProviderEmbedder(Embedder):
    """Adapter wrapping an EmbeddingMixin-capable LLMProvider."""

    def __init__(self, provider: EmbeddingMixin, dim: int = _DEFAULT_DIM) -> None:
        self._provider = provider
        self._dim = dim

    @property
    def dimension(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        return self._provider.embed(text)
