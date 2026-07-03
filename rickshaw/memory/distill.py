"""Memory distillation — turn raw assistant text into atomic, well-typed records.

Phase 4: replace the naive ``write_observations()`` (which dumped an entire
assistant response as one flat FACT) with a distiller that:

- Splits a turn into **atomic, well-typed** records (FACT / DECISION /
  PREFERENCE / ERROR) rather than one blob.
- Correctly classifies ``MemoryType`` and ``MemoryScope``.
- Sets ``sensitive`` properly (credentials, tokens, personal data).
- Makes dedup **scope-aware and update-on-duplicate**.
- **Personability:** actively extracts stable user-identity and preference
  memories (name, role, tone, recurring projects, likes/dislikes, working
  style) into GLOBAL-scoped PREFERENCE records, and supports **pinning** so a
  compact "about the user" set is always available to the assembler.

Distillation runs offline via a heuristic when no provider is reachable, and
uses the LLM when one is. Both paths return the same structure.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rickshaw.memory.record import MemoryRecord, MemoryScope, MemoryType

if TYPE_CHECKING:
    from rickshaw.providers.base import LLMProvider, Message, Effort, Response

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Preference / identity extraction (heuristic, offline)
# ---------------------------------------------------------------------------

# Patterns that signal a user preference or identity statement. The heuristic
# path scans the *user's* input (when available) for these, since preferences
# are things the user says about themselves.
_PREFERENCE_PATTERNS = [
    re.compile(r"\bI (?:prefer|like|love|enjoy|favor|want)\b", re.IGNORECASE),
    re.compile(r"\bI (?:don't like|dislike|hate|avoid|can't stand)\b", re.IGNORECASE),
    re.compile(r"\bmy (?:name|role|job|title|team|project|stack|setup)\b", re.IGNORECASE),
    re.compile(r"\bI (?:am|'m|work as|specialize in|focus on)\b", re.IGNORECASE),
    re.compile(r"\bI (?:always|usually|typically|tend to)\b", re.IGNORECASE),
    re.compile(r"\b(?:please|make sure|remember to|always) (?:use|keep|avoid)\b", re.IGNORECASE),
    re.compile(r"\bI use\b", re.IGNORECASE),
    re.compile(r"\bmy (?:favorite|preferred|go-to)\b", re.IGNORECASE),
]

# Patterns that signal sensitive content (should be marked sensitive=True).
_SENSITIVE_PATTERNS = [
    re.compile(r"\b(?:api[_ -]?key|secret|token|password|passwd|credential)\b", re.IGNORECASE),
    re.compile(r"\b(?:sk-|pk_|AKIA|ghp_|gho_)\b", re.IGNORECASE),  # common key prefixes
    re.compile(r"\b(?:private[_ -]?key|ssh[_ -]?key)\b", re.IGNORECASE),
    re.compile(r"\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b"),  # credit-card-like
    re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),  # emails
]

# Patterns that signal a decision was made.
_DECISION_PATTERNS = [
    re.compile(r"\b(?:let's|we should|we'll|I'll|decided to|going to|will use|chose)\b", re.IGNORECASE),
    re.compile(r"\b(?:decision|conclusion|resolution)\b", re.IGNORECASE),
]

# Patterns that signal an error occurred.
_ERROR_PATTERNS = [
    re.compile(r"\b(?:error|failed|failure|exception|traceback|crash|bug)\b", re.IGNORECASE),
]


def _is_sensitive(text: str) -> bool:
    return any(p.search(text) for p in _SENSITIVE_PATTERNS)


def _classify_type(text: str) -> MemoryType:
    if any(p.search(text) for p in _ERROR_PATTERNS):
        return MemoryType.ERROR
    if any(p.search(text) for p in _DECISION_PATTERNS):
        return MemoryType.DECISION
    if any(p.search(text) for p in _PREFERENCE_PATTERNS):
        return MemoryType.PREFERENCE
    return MemoryType.FACT


@dataclass
class DistilledRecord:
    """An intermediate representation before a MemoryRecord is materialized."""
    text: str
    type: MemoryType
    scope: MemoryScope
    sensitive: bool = False
    importance: float = 0.0
    pinned: bool = False


# ---------------------------------------------------------------------------
# Heuristic distillation (offline, no provider)
# ---------------------------------------------------------------------------

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _split_sentences(text: str) -> list[str]:
    """Split text into sentences, filtering out trivially short fragments."""
    text = text.strip()
    if not text:
        return []
    sentences = _SENTENCE_END.split(text)
    return [s.strip() for s in sentences if len(s.strip()) > 10]


def _extract_preferences(user_input: str) -> list[DistilledRecord]:
    """Extract preference/identity records from the user's input text."""
    records: list[DistilledRecord] = []
    for sentence in _split_sentences(user_input):
        for pattern in _PREFERENCE_PATTERNS:
            if pattern.search(sentence):
                records.append(DistilledRecord(
                    text=sentence,
                    type=MemoryType.PREFERENCE,
                    scope=MemoryScope.GLOBAL,
                    sensitive=_is_sensitive(sentence),
                    importance=0.7,
                    pinned=True,
                ))
                break
    return records


def _heuristic_distill(
    user_input: str,
    assistant_text: str,
    scope: MemoryScope = MemoryScope.SESSION,
) -> list[DistilledRecord]:
    """Offline heuristic distillation of a turn into atomic records."""
    records: list[DistilledRecord] = []

    # 1. Extract preferences/identity from the USER's input (personability).
    if user_input:
        records.extend(_extract_preferences(user_input))

    # 2. Distill the ASSISTANT's text into atomic typed facts/decisions.
    for sentence in _split_sentences(assistant_text):
        rtype = _classify_type(sentence)
        # Skip preference extraction from assistant text — preferences come
        # from the user, not the assistant's responses.
        if rtype == MemoryType.PREFERENCE:
            rtype = MemoryType.FACT
        records.append(DistilledRecord(
            text=sentence,
            type=rtype,
            scope=scope,
            sensitive=_is_sensitive(sentence),
            importance=0.3 if rtype == MemoryType.FACT else 0.5,
        ))

    return records


# ---------------------------------------------------------------------------
# LLM-driven distillation (uses a provider when reachable)
# ---------------------------------------------------------------------------

_LLM_DISTILL_PROMPT = """\
You are a memory distillation assistant. Extract atomic, well-typed memory \
records from the conversation turn below. Each record must be a single \
self-contained statement.

Classify each record as one of: fact, decision, preference, error.
- fact: a piece of information or observation.
- decision: a choice or plan that was made.
- preference: a stable user preference, working style, or identity detail \
  (name, role, tone, tools they like/dislikes).
- error: something that went wrong.

Mark sensitive=true for credentials, tokens, personal data, or anything that \
should not leave the user's machine.

Set scope to "global" for preferences/identity (they persist across sessions) \
or "session" for task-specific facts.

Set pinned=true ONLY for preferences/identity that describe the user durably \
(name, role, working style, tool preferences).

Respond as a JSON array of objects with keys: text, type, scope, sensitive, \
pinned. Respond with ONLY the JSON array, no commentary.

User said: {user_input}
Assistant replied: {assistant_text}"""


def _llm_distill(
    user_input: str,
    assistant_text: str,
    provider: LLMProvider,
) -> list[DistilledRecord] | None:
    """LLM-driven distillation. Returns None on failure (caller falls back)."""
    import json
    try:
        from rickshaw.providers.base import Effort, Message
    except Exception:
        return None
    prompt = _LLM_DISTILL_PROMPT.format(
        user_input=user_input[:500],
        assistant_text=assistant_text[:2000],
    )
    try:
        response = provider.complete(
            [Message(role="user", content=prompt)],
            effort=Effort.LOW,
        )
        text = response.text.strip()
        # Strip markdown code fences if present.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        items = json.loads(text)
        records: list[DistilledRecord] = []
        for item in items:
            rtype_str = item.get("type", "fact").lower()
            try:
                rtype = MemoryType(rtype_str)
            except ValueError:
                rtype = MemoryType.FACT
            scope_str = item.get("scope", "session").lower()
            try:
                scope = MemoryScope(scope_str)
            except ValueError:
                scope = MemoryScope.SESSION
            records.append(DistilledRecord(
                text=item.get("text", "").strip(),
                type=rtype,
                scope=scope,
                sensitive=bool(item.get("sensitive", False)),
                pinned=bool(item.get("pinned", False)),
                importance=0.7 if rtype == MemoryType.PREFERENCE else 0.4,
            ))
        return [r for r in records if r.text]
    except Exception as exc:
        logger.debug("LLM distillation failed (%s); falling back to heuristic.", exc)
        return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def distill_turn(
    user_input: str,
    assistant_text: str,
    scope: MemoryScope = MemoryScope.SESSION,
    provider: LLMProvider | None = None,
) -> list[DistilledRecord]:
    """Distill a conversation turn into atomic, well-typed records.

    Uses the LLM when a provider is reachable; falls back to a heuristic when
    not (offline default). Both paths return the same structure.
    """
    if provider is not None:
        llm_records = _llm_distill(user_input, assistant_text, provider)
        if llm_records is not None:
            return llm_records
    return _heuristic_distill(user_input, assistant_text, scope)


def is_pinworthy(record: MemoryRecord) -> bool:
    """Return True if a record should be pinned (always in the "about the user" set)."""
    return (
        record.type == MemoryType.PREFERENCE
        and record.scope == MemoryScope.GLOBAL
        and not record.sensitive
    )
