from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from voiceflow.dictionary.models import DictionaryKind
    from voiceflow.processing import DeliveryStatus, PostprocessRoute, RouteReason


@dataclass(frozen=True, slots=True)
class DictionaryEntryRecord:
    identifier: str
    kind: DictionaryKind
    spoken_form: str
    written_form: str
    language: str | None
    app_bundle_id: str | None
    priority: int
    enabled: bool
    use_count: int
    created_at: float
    updated_at: float


@dataclass(frozen=True, slots=True)
class TranscriptionRecord:
    identifier: str
    created_at: float
    raw_text: str
    normalized_text: str
    final_text: str
    corrected_text: str | None
    language: str | None
    route: PostprocessRoute | None
    reason_code: RouteReason | None
    fallback_code: str | None
    confidence_proxy: float | None
    audio_seconds: float
    stt_seconds: float | None
    normalization_seconds: float | None
    postprocess_seconds: float | None
    total_seconds: float | None
    delivery_status: DeliveryStatus
    error_code: str | None
    app_bundle_id: str | None


class DictionaryRepository(Protocol):
    def add_dictionary_entry(self, record: DictionaryEntryRecord) -> None: ...

    def dictionary_entries(self) -> tuple[DictionaryEntryRecord, ...]: ...

    def increment_dictionary_usage(self, identifiers: Iterable[str]) -> None: ...


class HistoryRepository(Protocol):
    def save_transcription(self, record: TranscriptionRecord) -> None: ...

    def save_correction(self, identifier: str, corrected_text: str) -> bool: ...
