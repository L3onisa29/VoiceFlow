from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizationOperation:
    type: str
    source: str
    replacement: str
    start: int | None = None
    end: int | None = None


@dataclass(frozen=True, slots=True)
class ProtectedSpan:
    text: str
    kind: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class NormalizationRequest:
    text: str
    language: str | None = None
    app_bundle_id: str | None = None
    spoken_punctuation: bool = True
    explicit_lists: bool = True
    structured_tokens: bool = True
    backtrack: bool = True


@dataclass(frozen=True, slots=True)
class NormalizationResult:
    text: str
    operations: tuple[NormalizationOperation, ...] = ()
    detected_signals: frozenset[str] = frozenset()
    protected_spans: tuple[ProtectedSpan, ...] = ()
