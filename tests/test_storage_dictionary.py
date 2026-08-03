from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from voiceflow.config import HistoryConfig
from voiceflow.correction import CorrectionService
from voiceflow.dictionary import DictionaryKind, DictionaryService
from voiceflow.dictionary.app_scope import AppBundleResolver
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
from voiceflow.storage.contracts import TranscriptionRecord
from voiceflow.transcription import TranscriptionResult


def outcome(text: str = "test") -> PipelineOutcome:
    trace = PipelineTrace(
        queued_ns=0,
        dequeued_ns=10,
        stt_started_ns=20,
        stt_ended_ns=30,
        normalization_started_ns=30,
        normalization_ended_ns=40,
        postprocess_started_ns=40,
        postprocess_ended_ns=50,
        injection_started_ns=50,
        injection_ended_ns=60,
        completed_ns=70,
        route=PostprocessRoute.RULES_ONLY,
        reason=RouteReason.CLEAN_FAST_PATH,
        delivery_status=DeliveryStatus.INJECTED,
    )
    transcription = TranscriptionResult(
        text,
        (),
        audio_duration_seconds=1,
        inference_seconds=0.1,
        language="it",
        language_source="configured",
        confidence_proxy=0.7,
    )
    return PipelineOutcome(
        ProcessedDictation(
            1,
            text,
            text,
            text,
            transcription,
            trace,
            ProcessingIntent.AUTO,
            "com.example.Secret",
        ),
        DeliveryStatus.INJECTED,
    )


def make_storage(
    tmp_path: Path,
    *,
    enabled: bool = False,
    max_entries: int = 100,
    retention_hours: int = 24,
    clock=lambda: 1000.0,
) -> VoiceBudStorage:
    return VoiceBudStorage(
        tmp_path / "private" / "voicebud.sqlite3",
        history=HistoryConfig(
            enabled=enabled,
            max_entries=max_entries,
            retention_hours=retention_hours,
        ),
        clock=clock,
    )


def test_storage_migration_is_idempotent_private_and_rolls_back(tmp_path: Path) -> None:
    storage = make_storage(tmp_path)
    path = storage.path
    assert storage.query("PRAGMA user_version")[0][0] == 1
    assert os.stat(path.parent).st_mode & 0o777 == 0o700
    assert os.stat(path).st_mode & 0o777 == 0o600
    storage.close()
    make_storage(tmp_path).close()

    broken = tmp_path / "broken.sqlite3"
    with pytest.raises(sqlite3.OperationalError):
        VoiceBudStorage(
            broken,
            history=HistoryConfig(),
            migrations={1: ("CREATE TABLE partial (id INTEGER)", "INVALID SQL")},
        )
    connection = sqlite3.connect(broken)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 0
    assert (
        connection.execute("SELECT COUNT(*) FROM sqlite_master WHERE name = 'partial'").fetchone()[
            0
        ]
        == 0
    )
    connection.close()


def test_storage_preserves_preexisting_custom_directory_mode(tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)

    storage = VoiceBudStorage(directory / "voicebud.sqlite3", history=HistoryConfig())

    assert os.stat(directory).st_mode & 0o777 == 0o755
    storage.close()


def test_explicit_retention_applies_when_new_history_writes_are_disabled(
    tmp_path: Path,
) -> None:
    now = [1_000.0]
    storage = make_storage(
        tmp_path,
        enabled=False,
        max_entries=100,
        retention_hours=24,
        clock=lambda: now[0],
    )
    for index, created_at in enumerate((900.0, 950.0, 999.0)):
        storage.save_transcription(
            TranscriptionRecord(
                identifier=f"record-{index}",
                created_at=created_at,
                raw_text="raw",
                normalized_text="normalized",
                final_text="final",
                corrected_text=None,
                language=None,
                route=None,
                reason_code=None,
                fallback_code=None,
                confidence_proxy=None,
                audio_seconds=1,
                stt_seconds=None,
                normalization_seconds=None,
                postprocess_seconds=None,
                total_seconds=None,
                delivery_status=DeliveryStatus.INJECTED,
                error_code=None,
                app_bundle_id=None,
            )
        )
    storage.history = HistoryConfig(enabled=False, max_entries=2, retention_hours=1)
    storage.apply_retention_policy()
    assert [record.identifier for record in storage.transcription_records()] == [
        "record-2",
        "record-1",
    ]

    now[0] += 3_601
    storage.apply_retention_policy()
    assert storage.transcription_count() == 0

    disabled = HistoryResultSink(storage, HistoryConfig(enabled=False))
    disabled.consume(outcome("must not be written"))
    assert storage.transcription_count() == 0
    storage.close()


def test_storage_bootstrap_applies_retention_when_history_writes_are_disabled(
    tmp_path: Path,
) -> None:
    storage = make_storage(
        tmp_path,
        enabled=True,
        max_entries=100,
        retention_hours=24,
        clock=lambda: 1_000,
    )
    for index, created_at in enumerate((900.0, 950.0, 999.0)):
        storage.execute(
            """
            INSERT INTO transcriptions (
                id, created_at, raw_text, normalized_text, final_text,
                audio_seconds, delivery_status
            ) VALUES (?, ?, 'raw', 'normalized', 'final', 1, 'injected')
            """,
            (f"record-{index}", created_at),
        )
    path = storage.path
    storage.close()

    retained = VoiceBudStorage(
        path,
        history=HistoryConfig(enabled=False, max_entries=2, retention_hours=1),
        clock=lambda: 1_000,
    )
    assert [record.identifier for record in retained.transcription_records()] == [
        "record-2",
        "record-1",
    ]
    retained.close()

    expired = VoiceBudStorage(
        path,
        history=HistoryConfig(enabled=False, max_entries=2, retention_hours=1),
        clock=lambda: 5_000,
    )
    assert expired.transcription_count() == 0
    expired.close()


def test_history_disabled_writes_no_transcript_and_enabled_obeys_limits(
    tmp_path: Path,
) -> None:
    storage = make_storage(tmp_path)
    disabled = HistoryResultSink(storage, HistoryConfig(enabled=False))
    disabled.consume(outcome("private words"))
    assert storage.transcription_count() == 0
    assert disabled.last_outcome is not None
    storage.close()

    now = [1000.0]
    storage = make_storage(
        tmp_path / "enabled",
        enabled=True,
        max_entries=2,
        retention_hours=1,
        clock=lambda: now[0],
    )
    sink = HistoryResultSink(
        storage,
        HistoryConfig(enabled=True, max_entries=2, retention_hours=1),
        clock=lambda: now[0],
    )
    for index in range(3):
        now[0] += 1
        sink.consume(outcome(f"text {index}"))
    assert storage.transcription_count() == 2
    assert storage.query("SELECT app_bundle_id FROM transcriptions")[0][0] is None
    now[0] += 3601
    storage.apply_retention_policy()
    assert storage.transcription_count() == 0
    storage.close()


def test_dictionary_scope_unicode_overlap_and_non_recursive_replacement(
    tmp_path: Path,
) -> None:
    storage = make_storage(tmp_path)
    dictionary = DictionaryService(storage, clock=lambda: 10)
    dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="voice",
        written_form="wrong",
    )
    longest = dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="voice bud",
        written_form="VoiceBud",
    )
    dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="voice bud",
        written_form="AppVoiceBud",
        app_bundle_id="com.example.App",
    )
    dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="caffè",
        written_form="CAFFÈ",
        language="it",
    )
    dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="alpha",
        written_form="beta",
    )
    dictionary.add(
        kind=DictionaryKind.REPLACEMENT,
        spoken_form="beta",
        written_form="gamma",
    )

    replaced, operations = dictionary.replace(
        "voice bud e caffè, alpha",
        language="it",
        app_bundle_id=None,
    )
    assert replaced == "VoiceBud e CAFFÈ, beta"
    assert operations[0].entry_id == longest.identifier
    assert "gamma" not in replaced
    scoped, _ = dictionary.replace(
        "voice bud",
        language="it",
        app_bundle_id="com.example.App",
    )
    assert scoped == "AppVoiceBud"
    bounded, _ = dictionary.replace("decaffèinato", language="it", app_bundle_id=None)
    assert bounded == "decaffèinato"
    assert any(entry.use_count for entry in dictionary.entries())
    storage.close()


def test_correction_never_creates_rule_without_confirmation(tmp_path: Path) -> None:
    storage = make_storage(tmp_path, enabled=True)
    sink = HistoryResultSink(storage, HistoryConfig(enabled=True))
    sink.consume(outcome("voice bad"))
    dictionary = DictionaryService(storage)
    service = CorrectionService(sink, dictionary)

    proposal = service.save("VoiceBud", confirm_replacement=False)
    assert proposal is not None
    assert sink.last_corrected_text == "VoiceBud"
    assert dictionary.entries() == ()
    service.save("VoiceBud", confirm_replacement=True)
    assert len(dictionary.entries()) == 1
    storage.close()


def test_app_bundle_resolver_reads_only_bundle_from_pid() -> None:
    calls: list[int] = []

    class Application:
        def bundleIdentifier(self) -> str:
            return "com.example.App"

    resolver = AppBundleResolver(lambda pid: calls.append(pid) or Application())
    assert resolver.resolve(42) == "com.example.App"
    assert calls == [42]


def test_app_bundle_resolver_uses_macos_running_application_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[int] = []

    class Application:
        def bundleIdentifier(self) -> str:
            return "com.example.Native"

    class RunningApplication:
        @staticmethod
        def runningApplicationWithProcessIdentifier_(pid: int) -> Application:
            calls.append(pid)
            return Application()

    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(NSRunningApplication=RunningApplication),
    )

    assert AppBundleResolver().resolve(73) == "com.example.Native"
    assert calls == [73]
