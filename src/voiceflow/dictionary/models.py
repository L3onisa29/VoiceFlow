from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DictionaryKind(StrEnum):
    PREFERRED_TERM = "preferred_term"
    REPLACEMENT = "replacement"


@dataclass(frozen=True, slots=True)
class DictionaryEntry:
    identifier: str
    kind: DictionaryKind
    spoken_form: str
    written_form: str
    language: str | None = None
    app_bundle_id: str | None = None
    priority: int = 0
    enabled: bool = True
    use_count: int = 0
    created_at: float = 0
    updated_at: float = 0


@dataclass(frozen=True, slots=True)
class DictionaryOperation:
    entry_id: str
    source: str
    replacement: str
    start: int
    end: int
