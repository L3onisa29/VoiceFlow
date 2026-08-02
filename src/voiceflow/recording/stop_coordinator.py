from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Protocol

import numpy as np

from voiceflow.audio.quality import capture_rejection_reason
from voiceflow.config import AudioConfig
from voiceflow.diagnostics import exception_fields, log_event
from voiceflow.integrations import (
    T3_CODE_BUNDLE_IDENTIFIER,
    DictationDestination,
    T3CodeDestination,
)
from voiceflow.recording.contracts import (
    AccessibilityAtStop,
    CapturedSubmission,
    StopDisposition,
    StopResult,
)
from voiceflow.system.text_injector import InjectionDestination

LOGGER = logging.getLogger(__name__)


class _SubmissionLease(Protocol):
    def abort(self) -> None: ...

    def commit_submission(
        self,
        audio: np.ndarray,
        *,
        destination: DictationDestination,
        sample_rate: int,
        app_bundle_id: str | None,
    ) -> int: ...


class RecordingStopCoordinator:
    def __init__(
        self,
        *,
        audio_config: AudioConfig,
        device_name: Callable[[], str],
        capture_destination: Callable[[], InjectionDestination],
        resolve_bundle_id: Callable[[int], str | None] | None = None,
        store_recent_audio: Callable[[str, np.ndarray, int], None] | None = None,
        submission_allowed: Callable[[], bool] = lambda: True,
    ) -> None:
        self._audio_config = audio_config
        self._device_name = device_name
        self._capture_destination = capture_destination
        self._resolve_bundle_id = resolve_bundle_id
        self._store_recent_audio = store_recent_audio
        self._submission_allowed = submission_allowed

    def submit(
        self,
        captured: CapturedSubmission,
        lease: _SubmissionLease,
    ) -> StopResult:
        rejection = capture_rejection_reason(
            duration_seconds=captured.duration_seconds,
            rms=captured.rms,
            device_name=self._device_name(),
            config=self._audio_config,
        )
        if rejection is not None:
            self._abort_rejected(lease)
            destination = (
                captured.recording_destination
                if isinstance(captured.recording_destination, T3CodeDestination)
                else None
            )
            return StopResult(
                StopDisposition.REJECTED,
                None,
                destination,
                T3_CODE_BUNDLE_IDENTIFIER if destination is not None else None,
                rejection,
            )

        destination, clipboard_fallback = self._resolve_destination(captured)
        app_bundle_id = self._resolve_app_bundle_id(destination)
        if not self._submission_allowed():
            lease.abort()
            return StopResult(
                StopDisposition.DISCARDED,
                None,
                destination,
                app_bundle_id,
                "Registrazione scartata durante l'arresto",
                clipboard_fallback,
            )
        try:
            sequence_id = lease.commit_submission(
                captured.audio,
                destination=destination,
                sample_rate=captured.sample_rate,
                app_bundle_id=app_bundle_id,
            )
        except RuntimeError:
            LOGGER.debug("Discarded recording during shutdown")
            return StopResult(
                StopDisposition.DISCARDED,
                None,
                destination,
                app_bundle_id,
                "Registrazione scartata durante l'arresto",
                clipboard_fallback,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "recording.submission_failed",
                "Captured recording could not be enqueued",
                context=exception_fields(exc),
                exc_info=True,
            )
            return StopResult(
                StopDisposition.FAILED,
                None,
                destination,
                app_bundle_id,
                "Registrazione non accodata",
                clipboard_fallback,
            )

        self._store_feedback(sequence_id, captured)
        return StopResult(
            StopDisposition.SUBMITTED,
            sequence_id,
            destination,
            app_bundle_id,
            None,
            clipboard_fallback,
        )

    def _abort_rejected(self, lease: _SubmissionLease) -> None:
        try:
            lease.abort()
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "recording.rejection_cleanup_failed",
                "Rejected recording cleanup failed",
                context=exception_fields(exc),
                exc_info=True,
            )

    def _resolve_destination(
        self,
        captured: CapturedSubmission,
    ) -> tuple[DictationDestination, bool]:
        destination = captured.recording_destination
        if not isinstance(destination, AccessibilityAtStop):
            return destination, False
        try:
            return self._capture_destination(), False
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                "destination.clipboard_fallback",
                "Focused input unavailable; clipboard fallback selected",
                context=exception_fields(exc),
                host_platform="macos",
                target_platform="clipboard",
                operation="clipboard_write",
            )
            return InjectionDestination.clipboard_only(), True

    def _resolve_app_bundle_id(self, destination: DictationDestination) -> str | None:
        if isinstance(destination, T3CodeDestination):
            return T3_CODE_BUNDLE_IDENTIFIER
        if destination.focus_target is None or self._resolve_bundle_id is None:
            return None
        try:
            return self._resolve_bundle_id(destination.focus_target.pid)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                "destination.bundle_resolution_failed",
                "Unable to resolve destination bundle identifier",
                context=exception_fields(exc),
                host_platform="macos",
                operation="destination_detection",
            )
            return None

    def _store_feedback(self, sequence_id: int, captured: CapturedSubmission) -> None:
        if self._store_recent_audio is None:
            return
        try:
            self._store_recent_audio(
                str(sequence_id),
                captured.audio,
                captured.sample_rate,
            )
        except Exception as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "feedback.recent_audio_store_failed",
                "Recent audio could not be retained after submission",
                context=exception_fields(exc),
                job_id=sequence_id,
                exc_info=True,
            )
