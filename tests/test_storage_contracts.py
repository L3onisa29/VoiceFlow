from __future__ import annotations

import sqlite3
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from voiceflow.config import HistoryConfig
from voiceflow.dictionary import DictionaryEntry, DictionaryKind, DictionaryService
from voiceflow.processing import (
    DeliveryStatus,
    PipelineOutcome,
    PipelineTrace,
    PostprocessRoute,
    ProcessedDictation,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.storage import HistoryResultSink, VoiceBudStorage
from voiceflow.storage.contracts import (
    DictionaryEntryRecord,
    TranscriptionRecord,
)
from voiceflow.transcription import TranscriptionResult


def test_storage_package_imports_without_dictionary_preload() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from voiceflow.storage import VoiceBudStorage; print(VoiceBudStorage.__name__)",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.strip() == "VoiceBudStorage"


def _history_outcome() -> PipelineOutcome:
    trace = PipelineTrace(
        queued_ns=0,
        dequeued_ns=1_000_000_000,
        stt_started_ns=1_000_000_000,
        stt_ended_ns=3_000_000_000,
        normalization_started_ns=3_000_000_000,
        normalization_ended_ns=4_000_000_000,
        postprocess_started_ns=4_000_000_000,
        postprocess_ended_ns=7_000_000_000,
        completed_ns=8_000_000_000,
        route=PostprocessRoute.PROVIDER,
        reason=RouteReason.DEEP_REQUESTED,
        fallback_code="provider_timeout",
    )
    transcription = TranscriptionResult(
        "raw",
        (),
        audio_duration_seconds=1.25,
        inference_seconds=2,
        language="it",
        language_source="configured",
        confidence_proxy=0.75,
    )
    return PipelineOutcome(
        ProcessedDictation(
            sequence_id=1,
            raw_text="raw",
            normalized_text="normalized",
            final_text="final",
            transcription=transcription,
            trace=trace,
            processing_intent=ProcessingIntent.DEEP,
            app_bundle_id="com.example.App",
        ),
        DeliveryStatus.CLIPBOARD_ONLY,
        error_code="focus_changed",
    )


def test_dictionary_record_round_trips_every_field(tmp_path: Path) -> None:
    storage = VoiceBudStorage(tmp_path / "voicebud.sqlite3", history=HistoryConfig())
    record = DictionaryEntryRecord(
        identifier="dictionary-id",
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="voice bud",
        written_form="VoiceBud",
        language=None,
        app_bundle_id=None,
        priority=-2,
        enabled=False,
        use_count=7,
        created_at=123.125,
        updated_at=456.875,
    )

    storage.add_dictionary_entry(record)

    assert storage.dictionary_entries() == (record,)
    storage.close()


def test_transcription_record_round_trips_every_field(tmp_path: Path) -> None:
    storage = VoiceBudStorage(
        tmp_path / "voicebud.sqlite3",
        history=HistoryConfig(enabled=True, retention_hours=24, max_entries=10),
        clock=lambda: 1_000,
    )
    record = TranscriptionRecord(
        identifier="transcription-id",
        created_at=999.25,
        raw_text="raw",
        normalized_text="normalized",
        final_text="final",
        corrected_text=None,
        language=None,
        route=PostprocessRoute.RULES_ONLY,
        reason_code=RouteReason.CLEAN_FAST_PATH,
        fallback_code=None,
        confidence_proxy=None,
        audio_seconds=1.5,
        stt_seconds=None,
        normalization_seconds=0.25,
        postprocess_seconds=None,
        total_seconds=2.75,
        delivery_status=DeliveryStatus.INJECTED,
        error_code=None,
        app_bundle_id=None,
    )

    storage.save_transcription(record)

    assert storage.transcription_records() == (record,)
    storage.close()


def test_storage_reads_an_existing_version_one_database(tmp_path: Path) -> None:
    path = tmp_path / "voicebud.sqlite3"
    bootstrap = VoiceBudStorage(path, history=HistoryConfig())
    bootstrap.add_dictionary_entry(
        DictionaryEntryRecord(
            identifier="existing",
            kind=DictionaryKind.PREFERRED_TERM,
            spoken_form="codex",
            written_form="Codex",
            language="en",
            app_bundle_id="com.example.Editor",
            priority=3,
            enabled=True,
            use_count=4,
            created_at=10.5,
            updated_at=11.5,
        )
    )
    bootstrap.close()

    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
    connection.close()

    reopened = VoiceBudStorage(path, history=HistoryConfig())
    assert reopened.dictionary_entries()[0].identifier == "existing"
    assert reopened.dictionary_entries()[0].kind is DictionaryKind.PREFERRED_TERM
    assert reopened.dictionary_entries()[0].enabled is True
    assert reopened.query("PRAGMA user_version")[0][0] == 1
    reopened.close()


class FakeDictionaryRepository:
    def __init__(self) -> None:
        self.records: list[DictionaryEntryRecord] = []
        self.incremented: tuple[str, ...] = ()

    def add_dictionary_entry(self, record: DictionaryEntryRecord) -> None:
        self.records.append(record)

    def dictionary_entries(self) -> tuple[DictionaryEntryRecord, ...]:
        return tuple(self.records)

    def increment_dictionary_usage(self, identifiers: Iterable[str]) -> None:
        self.incremented = tuple(identifiers)
        selected = set(self.incremented)
        self.records = [
            replace(record, use_count=record.use_count + 1)
            if record.identifier in selected
            else record
            for record in self.records
        ]


def test_dictionary_service_uses_records_through_repository_boundary() -> None:
    repository = FakeDictionaryRepository()
    service = DictionaryService(repository, clock=lambda: 42.5)

    created = service.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form=" voice bud ",
        written_form=" VoiceBud ",
        language="it",
        enabled=False,
    )

    assert repository.records == [
        DictionaryEntryRecord(
            identifier=created.identifier,
            kind=DictionaryKind.REPLACEMENT,
            spoken_form="voice bud",
            written_form="VoiceBud",
            language="it",
            app_bundle_id=None,
            priority=0,
            enabled=False,
            use_count=0,
            created_at=42.5,
            updated_at=42.5,
        )
    ]
    assert service.entries() == (created,)


class FakeHistoryRepository:
    def __init__(self) -> None:
        self.record: TranscriptionRecord | None = None
        self.correction: tuple[str, str] | None = None

    def save_transcription(self, record: TranscriptionRecord) -> None:
        self.record = record

    def save_correction(self, identifier: str, corrected_text: str) -> bool:
        self.correction = (identifier, corrected_text)
        return True


def test_history_sink_uses_record_through_repository_boundary() -> None:
    repository = FakeHistoryRepository()
    sink = HistoryResultSink(
        repository,
        HistoryConfig(enabled=True, store_app_bundle_id=True),
        clock=lambda: 123.5,
    )

    sink.consume(_history_outcome())

    assert repository.record is not None
    assert repository.record.created_at == 123.5
    assert repository.record.corrected_text is None
    assert repository.record.route is PostprocessRoute.PROVIDER
    assert repository.record.reason_code is RouteReason.DEEP_REQUESTED
    assert repository.record.delivery_status is DeliveryStatus.CLIPBOARD_ONLY
    assert repository.record.app_bundle_id == "com.example.App"
    assert repository.record.stt_seconds == 2
    assert repository.record.normalization_seconds == 1
    assert repository.record.postprocess_seconds == 3
    assert repository.record.total_seconds == 8
    assert sink.save_last_correction("corrected") is True
    assert repository.correction == (repository.record.identifier, "corrected")


def test_dictionary_service_reads_typed_records_without_row_or_tuple_behavior() -> None:
    class AttributeOnlyRepository(FakeDictionaryRepository):
        def dictionary_entries(self) -> tuple[DictionaryEntryRecord, ...]:
            return (
                DictionaryEntryRecord(
                    identifier="attribute-only",
                    kind=DictionaryKind.PREFERRED_TERM,
                    spoken_form="codex",
                    written_form="Codex",
                    language=None,
                    app_bundle_id=None,
                    priority=1,
                    enabled=True,
                    use_count=2,
                    created_at=1,
                    updated_at=2,
                ),
            )

    entries = DictionaryService(AttributeOnlyRepository()).entries()

    assert entries == (
        DictionaryEntry(
            identifier="attribute-only",
            kind=DictionaryKind.PREFERRED_TERM,
            spoken_form="codex",
            written_form="Codex",
            priority=1,
            use_count=2,
            created_at=1,
            updated_at=2,
        ),
    )
