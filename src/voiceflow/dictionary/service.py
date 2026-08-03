from __future__ import annotations

import re
import time
from collections.abc import Callable
from uuid import uuid4

from voiceflow.dictionary.models import (
    DictionaryEntry,
    DictionaryKind,
    DictionaryOperation,
)
from voiceflow.storage.contracts import DictionaryRepository


class DictionaryService:
    def __init__(
        self,
        storage: DictionaryRepository,
        *,
        enabled: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.storage = storage
        self.enabled = enabled
        self._clock = clock

    def entries(self) -> tuple[DictionaryEntry, ...]:
        if not self.enabled:
            return ()
        return self.storage.dictionary_entries()

    def add(
        self,
        *,
        kind: DictionaryKind,
        spoken_form: str,
        written_form: str,
        language: str | None = None,
        app_bundle_id: str | None = None,
        priority: int = 0,
        enabled: bool = True,
    ) -> DictionaryEntry:
        spoken = spoken_form.strip()
        written = written_form.strip()
        if not spoken or not written:
            raise ValueError("Dictionary forms cannot be empty")
        now = self._clock()
        entry = DictionaryEntry(
            identifier=str(uuid4()),
            kind=kind,
            spoken_form=spoken,
            written_form=written,
            language=language or None,
            app_bundle_id=app_bundle_id or None,
            priority=priority,
            enabled=enabled,
            created_at=now,
            updated_at=now,
        )
        self.storage.add_dictionary_entry(entry)
        return entry

    @staticmethod
    def _scope(entry: DictionaryEntry, language: str | None, app: str | None) -> int:
        language_match = entry.language is not None and entry.language == language
        app_match = entry.app_bundle_id is not None and entry.app_bundle_id == app
        if entry.language is not None and not language_match:
            return -1
        if entry.app_bundle_id is not None and not app_match:
            return -1
        return int(language_match) * 2 + int(app_match)

    def scoped_entries(
        self,
        *,
        kind: DictionaryKind,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[DictionaryEntry, ...]:
        ranked = (
            (entry, self._scope(entry, language, app_bundle_id))
            for entry in self.entries()
            if entry.enabled and entry.kind == kind
        )
        return tuple(
            entry
            for entry, scope in sorted(
                ((entry, scope) for entry, scope in ranked if scope >= 0),
                key=lambda pair: (
                    -pair[1],
                    -pair[0].priority,
                    -len(pair[0].spoken_form),
                    pair[0].created_at,
                    pair[0].identifier,
                ),
            )
        )

    def preferred_terms(
        self, *, language: str | None, app_bundle_id: str | None
    ) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                entry.written_form
                for entry in self.scoped_entries(
                    kind=DictionaryKind.PREFERRED_TERM,
                    language=language,
                    app_bundle_id=app_bundle_id,
                )
            )
        )

    def replace(
        self,
        text: str,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, tuple[DictionaryOperation, ...]]:
        candidates: list[tuple[int, int, int, DictionaryEntry, str]] = []
        entries = self.scoped_entries(
            kind=DictionaryKind.REPLACEMENT,
            language=language,
            app_bundle_id=app_bundle_id,
        )
        ranking = {entry.identifier: index for index, entry in enumerate(entries)}
        for entry in entries:
            pattern = re.compile(
                rf"(?<!\w){re.escape(entry.spoken_form)}(?!\w)",
                re.IGNORECASE | re.UNICODE,
            )
            for match in pattern.finditer(text):
                candidates.append(
                    (
                        match.start(),
                        match.end(),
                        ranking[entry.identifier],
                        entry,
                        match.group(),
                    )
                )
        selected: list[tuple[int, int, DictionaryEntry, str]] = []
        cursor = 0
        for start, end, _rank, entry, source in sorted(
            candidates, key=lambda item: (item[0], item[2], -(item[1] - item[0]))
        ):
            if start < cursor:
                continue
            selected.append((start, end, entry, source))
            cursor = end
        if not selected:
            return text, ()
        parts: list[str] = []
        operations: list[DictionaryOperation] = []
        cursor = 0
        for start, end, entry, source in selected:
            parts.extend((text[cursor:start], entry.written_form))
            operations.append(
                DictionaryOperation(
                    entry.identifier,
                    source,
                    entry.written_form,
                    start,
                    end,
                )
            )
            cursor = end
        parts.append(text[cursor:])
        self.storage.increment_dictionary_usage(op.entry_id for op in operations)
        return "".join(parts), tuple(operations)
