from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from typing import Any

import pytest

from voiceflow.app import VoiceBudApplication
from voiceflow.dictionary import (
    DictionaryKind,
    DictionaryOperation,
    DictionaryService,
    ResilientDictionary,
)
from voiceflow.dictionary.resilient import DICTIONARY_UNAVAILABLE_MESSAGE
from voiceflow.normalization import DeterministicNormalizer, NormalizationRequest
from voiceflow.storage.contracts import DictionaryEntryRecord


class Reader:
    def __init__(self) -> None:
        self.enabled = True
        self.replace_calls = 0
        self.preferred_terms_calls = 0
        self.replace_error: Exception | None = None
        self.preferred_terms_error: Exception | None = None
        self.operation = DictionaryOperation("entry", "voice bud", "VoiceBud", 0, 9)

    def replace(
        self,
        text: str,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, tuple[DictionaryOperation, ...]]:
        self.replace_calls += 1
        if self.replace_error is not None:
            raise self.replace_error
        return text.replace("voice bud", "VoiceBud"), (self.operation,)

    def preferred_terms(
        self,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, ...]:
        self.preferred_terms_calls += 1
        if self.preferred_terms_error is not None:
            raise self.preferred_terms_error
        return ("VoiceBud", "Parakeet")


def test_replace_failure_disables_once_and_short_circuits_all_future_reads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reader = Reader()
    reader.replace_error = OSError("database unavailable")
    notifications: list[str] = []
    dictionary = ResilientDictionary(reader, notifier=notifications.append)

    with caplog.at_level(logging.ERROR, logger="voiceflow.dictionary.resilient"):
        assert dictionary.replace("voice bud", language="it", app_bundle_id=None) == (
            "voice bud",
            (),
        )
        assert dictionary.replace("again", language=None, app_bundle_id=None) == ("again", ())
        assert dictionary.preferred_terms(language="it", app_bundle_id=None) == ()

    assert not dictionary.enabled
    assert not reader.enabled
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 0
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]
    assert [record.message for record in caplog.records] == [
        "Dictionary disabled after a local storage failure"
    ]


def test_preferred_terms_failure_disables_once_and_short_circuits_all_future_reads(
    caplog: pytest.LogCaptureFixture,
) -> None:
    reader = Reader()
    reader.preferred_terms_error = RuntimeError("read failed")
    notifications: list[str] = []
    dictionary = ResilientDictionary(reader, notifier=notifications.append)

    with caplog.at_level(logging.ERROR, logger="voiceflow.dictionary.resilient"):
        assert dictionary.preferred_terms(language=None, app_bundle_id="app") == ()
        assert dictionary.preferred_terms(language=None, app_bundle_id="app") == ()
        assert dictionary.replace("unchanged", language=None, app_bundle_id="app") == (
            "unchanged",
            (),
        )

    assert not dictionary.enabled
    assert reader.preferred_terms_calls == 1
    assert reader.replace_calls == 0
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]
    assert len(caplog.records) == 1


def test_successful_reads_preserve_results_and_operation_values() -> None:
    reader = Reader()
    notifications: list[str] = []
    dictionary = ResilientDictionary(reader, notifier=notifications.append)

    assert dictionary.replace("voice bud", language="it", app_bundle_id="app") == (
        "VoiceBud",
        (reader.operation,),
    )
    assert dictionary.preferred_terms(language="it", app_bundle_id="app") == (
        "VoiceBud",
        "Parakeet",
    )
    assert dictionary.enabled
    assert reader.enabled
    assert notifications == []


def test_same_thread_reader_reentry_preserves_inner_and_outer_results() -> None:
    class ReentrantReader(Reader):
        dictionary: ResilientDictionary

        def __init__(self) -> None:
            super().__init__()
            self.inner_result: tuple[str, ...] | None = None

        def replace(
            self,
            text: str,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, tuple[DictionaryOperation, ...]]:
            self.replace_calls += 1
            self.inner_result = self.dictionary.preferred_terms(
                language=language,
                app_bundle_id=app_bundle_id,
            )
            return text.replace("voice bud", "VoiceBud"), (self.operation,)

    reader = ReentrantReader()
    dictionary = ResilientDictionary(reader, notifier=lambda _message: None)
    reader.dictionary = dictionary
    results: list[tuple[str, tuple[DictionaryOperation, ...]]] = []
    read_thread = threading.Thread(
        target=lambda: results.append(
            dictionary.replace("voice bud", language="it", app_bundle_id="app")
        ),
        daemon=True,
    )

    read_thread.start()
    read_thread.join(timeout=1)

    assert not read_thread.is_alive(), "same-thread reader re-entry deadlocked"
    assert reader.inner_result == ("VoiceBud", "Parakeet")
    assert results == [("VoiceBud", (reader.operation,))]
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 1
    assert dictionary.enabled


def test_concurrent_read_waiting_on_failure_never_reaches_reader_after_disable() -> None:
    replace_entered = threading.Event()
    release_replace = threading.Event()
    preferred_attempted = threading.Event()

    class BlockingReader(Reader):
        def replace(self, *args: Any, **kwargs: Any):
            self.replace_calls += 1
            replace_entered.set()
            assert release_replace.wait(timeout=1)
            raise OSError("replace failed")

        def preferred_terms(self, **kwargs: Any):
            self.preferred_terms_calls += 1
            pytest.fail("a read waiting behind disablement must not reach the reader")

    reader = BlockingReader()
    notifications: list[str] = []
    dictionary = ResilientDictionary(reader, notifier=notifications.append)
    results: list[object] = []

    replace_thread = threading.Thread(
        target=lambda: results.append(
            dictionary.replace("source", language=None, app_bundle_id=None)
        )
    )

    def preferred() -> None:
        preferred_attempted.set()
        results.append(dictionary.preferred_terms(language=None, app_bundle_id=None))

    preferred_thread = threading.Thread(target=preferred)
    replace_thread.start()
    assert replace_entered.wait(timeout=1)
    preferred_thread.start()
    assert preferred_attempted.wait(timeout=1)
    release_replace.set()
    replace_thread.join(timeout=1)
    preferred_thread.join(timeout=1)

    assert not replace_thread.is_alive()
    assert not preferred_thread.is_alive()
    assert ("source", ()) in results
    assert () in results
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 0
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]


def test_nested_reader_failure_disables_once_before_outer_read_completes(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class ReentrantFailingReader(Reader):
        dictionary: ResilientDictionary

        def __init__(self) -> None:
            super().__init__()
            self.inner_result: tuple[str, ...] | None = None

        def replace(
            self,
            text: str,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, tuple[DictionaryOperation, ...]]:
            self.replace_calls += 1
            self.inner_result = self.dictionary.preferred_terms(
                language=language,
                app_bundle_id=app_bundle_id,
            )
            return f"{text}-outer", (self.operation,)

        def preferred_terms(
            self,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, ...]:
            self.preferred_terms_calls += 1
            raise OSError("nested read failed")

    reader = ReentrantFailingReader()
    notifications: list[str] = []
    callback_results: list[tuple[str, ...]] = []

    def notify(message: str) -> None:
        notifications.append(message)
        callback_results.append(dictionary.preferred_terms(language=None, app_bundle_id=None))

    dictionary = ResilientDictionary(reader, notifier=notify)
    reader.dictionary = dictionary
    results: list[tuple[str, tuple[DictionaryOperation, ...]]] = []
    read_thread = threading.Thread(
        target=lambda: results.append(
            dictionary.replace("source", language=None, app_bundle_id=None)
        ),
        daemon=True,
    )

    with caplog.at_level(logging.ERROR, logger="voiceflow.dictionary.resilient"):
        read_thread.start()
        read_thread.join(timeout=1)

    assert not read_thread.is_alive(), "nested failure handling deadlocked"
    assert reader.inner_result == ()
    assert results == [("source-outer", (reader.operation,))]
    assert not dictionary.enabled
    assert not reader.enabled
    assert callback_results == [()]
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]
    assert [record.message for record in caplog.records] == [
        "Dictionary disabled after a local storage failure"
    ]

    assert dictionary.replace("later", language=None, app_bundle_id=None) == ("later", ())
    assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ()
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 1


@pytest.mark.parametrize("outer_operation", ["replace", "preferred_terms"])
def test_nested_failure_followed_by_outer_exception_reports_first_failure_once(
    outer_operation: str,
) -> None:
    class ReentrantFailingReader(Reader):
        dictionary: ResilientDictionary

        def replace(
            self,
            text: str,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, tuple[DictionaryOperation, ...]]:
            self.replace_calls += 1
            if outer_operation == "preferred_terms":
                raise OSError("inner replace failed")
            assert (
                self.dictionary.preferred_terms(
                    language=language,
                    app_bundle_id=app_bundle_id,
                )
                == ()
            )
            raise RuntimeError("outer replace failed")

        def preferred_terms(
            self,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, ...]:
            self.preferred_terms_calls += 1
            if outer_operation == "replace":
                raise OSError("inner preferred terms failed")
            assert self.dictionary.replace(
                "inner",
                language=language,
                app_bundle_id=app_bundle_id,
            ) == ("inner", ())
            raise RuntimeError("outer preferred terms failed")

    logged_errors: list[BaseException] = []
    notifications: list[str] = []

    class Logger:
        def error(self, _message: str, **kwargs: Any) -> None:
            logged_errors.append(kwargs["exc_info"][1])

    reader = ReentrantFailingReader()
    dictionary = ResilientDictionary(
        reader,
        logger=Logger(),
        notifier=notifications.append,
    )
    reader.dictionary = dictionary

    if outer_operation == "replace":
        assert dictionary.replace("outer", language=None, app_bundle_id=None) == (
            "outer",
            (),
        )
    else:
        assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ()

    first_counts = (reader.replace_calls, reader.preferred_terms_calls)
    assert len(logged_errors) == 1
    assert isinstance(logged_errors[0], OSError)
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]
    assert not dictionary.enabled
    assert not reader.enabled

    assert dictionary.replace("later", language=None, app_bundle_id=None) == ("later", ())
    assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ()
    assert (reader.replace_calls, reader.preferred_terms_calls) == first_counts
    assert len(logged_errors) == 1
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]


@pytest.mark.parametrize("outer_operation", ["replace", "preferred_terms"])
def test_nested_failure_reports_after_outermost_reader_lock_is_released(
    outer_operation: str,
) -> None:
    class ReentrantFailingReader(Reader):
        dictionary: ResilientDictionary

        def replace(
            self,
            text: str,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, tuple[DictionaryOperation, ...]]:
            self.replace_calls += 1
            if outer_operation == "preferred_terms":
                raise OSError("inner replace failed")
            assert (
                self.dictionary.preferred_terms(
                    language=language,
                    app_bundle_id=app_bundle_id,
                )
                == ()
            )
            return f"{text}-outer", (self.operation,)

        def preferred_terms(
            self,
            *,
            language: str | None,
            app_bundle_id: str | None,
        ) -> tuple[str, ...]:
            self.preferred_terms_calls += 1
            if outer_operation == "replace":
                raise OSError("inner preferred terms failed")
            assert self.dictionary.replace(
                "inner",
                language=language,
                app_bundle_id=app_bundle_id,
            ) == ("inner", ())
            return ("outer",)

    worker_results: list[object] = []
    workers: list[threading.Thread] = []
    worker_finished_before_notifier_returned: list[bool] = []

    def notify(_message: str) -> None:
        def worker_read() -> None:
            if outer_operation == "replace":
                worker_results.append(
                    dictionary.replace("worker", language=None, app_bundle_id=None)
                )
            else:
                worker_results.append(dictionary.preferred_terms(language=None, app_bundle_id=None))

        worker = threading.Thread(target=worker_read, daemon=True)
        workers.append(worker)
        worker.start()
        worker.join(timeout=0.5)
        worker_finished_before_notifier_returned.append(not worker.is_alive())

    reader = ReentrantFailingReader()
    dictionary = ResilientDictionary(reader, notifier=notify)
    reader.dictionary = dictionary

    if outer_operation == "replace":
        assert dictionary.replace("source", language=None, app_bundle_id=None) == (
            "source-outer",
            (reader.operation,),
        )
        expected_worker_result: object = ("worker", ())
    else:
        assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ("outer",)
        expected_worker_result = ()

    for worker in workers:
        worker.join(timeout=1)
        assert not worker.is_alive(), "callback worker leaked"

    assert worker_finished_before_notifier_returned == [True]
    assert worker_results == [expected_worker_result]
    assert (reader.replace_calls, reader.preferred_terms_calls) == (1, 1)
    assert not dictionary.enabled


@pytest.mark.parametrize("failing_callback", ["logger", "notifier"])
def test_callback_failure_does_not_corrupt_disablement_or_retry_reads(
    failing_callback: str,
) -> None:
    reader = Reader()
    reader.replace_error = OSError("database unavailable")
    logger_calls: list[str] = []
    notification_calls: list[str] = []

    class Logger:
        def error(self, message: str, **_kwargs: Any) -> None:
            logger_calls.append(message)
            if failing_callback == "logger":
                raise RuntimeError("logger callback failed")

    def notify(message: str) -> None:
        notification_calls.append(message)
        if failing_callback == "notifier":
            raise RuntimeError("notification callback failed")

    dictionary = ResilientDictionary(reader, logger=Logger(), notifier=notify)

    assert dictionary.replace("source", language=None, app_bundle_id=None) == ("source", ())
    assert dictionary.replace("second", language=None, app_bundle_id=None) == ("second", ())
    assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ()
    assert not dictionary.enabled
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 0
    assert logger_calls == ["Dictionary disabled after a local storage failure"]
    assert notification_calls == [DICTIONARY_UNAVAILABLE_MESSAGE]


@pytest.mark.parametrize("failing_callback", ["logger", "notifier"])
def test_reentrant_callback_failure_is_one_shot_and_does_not_strand_read_state(
    failing_callback: str,
) -> None:
    reader = Reader()
    reader.replace_error = OSError("database unavailable")
    logger_calls: list[tuple[str, ...]] = []
    notification_calls: list[tuple[str, tuple[DictionaryOperation, ...]]] = []

    class Logger:
        def error(self, _message: str, **_kwargs: Any) -> None:
            logger_calls.append(dictionary.preferred_terms(language=None, app_bundle_id=None))
            if failing_callback == "logger":
                raise RuntimeError("logger callback failed")

    def notify(_message: str) -> None:
        notification_calls.append(dictionary.replace("callback", language=None, app_bundle_id=None))
        if failing_callback == "notifier":
            raise RuntimeError("notification callback failed")

    dictionary = ResilientDictionary(reader, logger=Logger(), notifier=notify)

    assert dictionary.replace("source", language=None, app_bundle_id=None) == ("source", ())
    assert logger_calls == [()]
    assert notification_calls == [("callback", ())]
    assert dictionary.preferred_terms(language=None, app_bundle_id=None) == ()
    assert dictionary.replace("later", language=None, app_bundle_id=None) == ("later", ())
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 0
    assert logger_calls == [()]
    assert notification_calls == [("callback", ())]


def test_normalizer_and_app_preferred_terms_share_one_resilient_boundary() -> None:
    reader = Reader()
    reader.replace_error = OSError("database unavailable")
    notifications: list[str] = []
    dictionary = ResilientDictionary(reader, notifier=notifications.append)
    normalizer = DeterministicNormalizer(dictionary)
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.resilient_dictionary = dictionary

    result = normalizer.normalize(NormalizationRequest("ciao punto"))

    assert result.text == "ciao."
    assert app._preferred_terms("it", "app") == ()
    assert reader.replace_calls == 1
    assert reader.preferred_terms_calls == 0
    assert notifications == [DICTIONARY_UNAVAILABLE_MESSAGE]


def test_dictionary_writes_remain_available_after_resilient_reads_disable() -> None:
    class Repository:
        def __init__(self) -> None:
            self.added: list[DictionaryEntryRecord] = []
            self.read_calls = 0

        def add_dictionary_entry(self, record: DictionaryEntryRecord) -> None:
            self.added.append(record)

        def dictionary_entries(self) -> tuple[DictionaryEntryRecord, ...]:
            self.read_calls += 1
            raise OSError("database read failed")

        def increment_dictionary_usage(self, identifiers: Iterable[str]) -> None:
            pass

    repository = Repository()
    service = DictionaryService(repository, clock=lambda: 42.0)
    resilient = ResilientDictionary(service, notifier=lambda _message: None)

    assert resilient.preferred_terms(language=None, app_bundle_id=None) == ()
    added = service.add(
        kind=DictionaryKind.PREFERRED_TERM,
        spoken_form="voice bud",
        written_form="VoiceBud",
    )

    assert not resilient.enabled
    assert repository.read_calls == 1
    assert repository.added == [
        DictionaryEntryRecord(
            identifier=added.identifier,
            kind=DictionaryKind.PREFERRED_TERM,
            spoken_form="voice bud",
            written_form="VoiceBud",
            language=None,
            app_bundle_id=None,
            priority=0,
            enabled=True,
            use_count=0,
            created_at=42.0,
            updated_at=42.0,
        )
    ]
