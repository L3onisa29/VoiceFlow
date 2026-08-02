from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from contextlib import suppress
from typing import Protocol, TypeVar

from voiceflow.dictionary.models import DictionaryOperation

LOGGER = logging.getLogger(__name__)
DICTIONARY_FAILURE_LOG_MESSAGE = "Dictionary disabled after a local storage failure"
DICTIONARY_UNAVAILABLE_MESSAGE = "Dizionario non disponibile — dettatura ancora attiva"

_Result = TypeVar("_Result")


class _ReadContext(threading.local):
    def __init__(self) -> None:
        self.depth = 0


class DictionaryReader(Protocol):
    enabled: bool

    def replace(
        self,
        text: str,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, tuple[DictionaryOperation, ...]]: ...

    def preferred_terms(
        self,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, ...]: ...


class ResilientDictionary:
    """Serialize dictionary reads and permanently degrade after the first failure.

    Reader calls run under one reentrant lock: other threads serialize, while an
    underlying reader may safely re-enter on its owning thread. A nested failure
    disables reads before returning its fallback; the outer call already in flight may
    complete, but no later reader call begins. Failure callbacks observe that disabled
    state and may re-enter without reaching the reader.
    """

    def __init__(
        self,
        reader: DictionaryReader,
        *,
        notifier: Callable[[str], None],
        logger: logging.Logger = LOGGER,
    ) -> None:
        self._reader = reader
        self._notifier = notifier
        self._logger = logger
        self._lock = threading.RLock()
        self._read_context = _ReadContext()
        self._enabled = reader.enabled
        self._pending_failure: Exception | None = None
        self._failure_report_claimed = False

    @property
    def enabled(self) -> bool:
        with self._lock:
            return self._enabled

    def replace(
        self,
        text: str,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, tuple[DictionaryOperation, ...]]:
        fallback: tuple[str, tuple[DictionaryOperation, ...]] = (text, ())
        return self._read(
            lambda: self._reader.replace(
                text,
                language=language,
                app_bundle_id=app_bundle_id,
            ),
            fallback,
        )

    def preferred_terms(
        self,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> tuple[str, ...]:
        return self._read(
            lambda: self._reader.preferred_terms(
                language=language,
                app_bundle_id=app_bundle_id,
            ),
            (),
        )

    def _read(self, operation: Callable[[], _Result], fallback: _Result) -> _Result:
        failure_to_report: Exception | None = None
        self._lock.acquire()
        self._read_context.depth += 1
        try:
            if not self._enabled:
                return fallback
            try:
                return operation()
            except Exception as error:
                if self._enabled:
                    self._enabled = False
                    self._pending_failure = error
                    self._reader.enabled = False
                return fallback
        finally:
            self._read_context.depth -= 1
            if (
                self._read_context.depth == 0
                and self._pending_failure is not None
                and not self._failure_report_claimed
            ):
                failure_to_report = self._pending_failure
                self._pending_failure = None
                self._failure_report_claimed = True
            self._lock.release()
            if failure_to_report is not None:
                self._report_failure(failure_to_report)

    def _report_failure(self, error: Exception) -> None:
        with suppress(Exception):
            self._logger.error(
                DICTIONARY_FAILURE_LOG_MESSAGE,
                exc_info=(type(error), error, error.__traceback__),
            )
        with suppress(Exception):
            self._notifier(DICTIONARY_UNAVAILABLE_MESSAGE)
