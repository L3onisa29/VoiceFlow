from __future__ import annotations

import threading
import time
from collections.abc import Callable
from uuid import uuid4

from voiceflow.config import HistoryConfig
from voiceflow.processing import PipelineOutcome
from voiceflow.storage.contracts import HistoryRepository, TranscriptionRecord


class HistoryResultSink:
    """Record new history only when enabled; retention remains a storage policy."""

    def __init__(
        self,
        storage: HistoryRepository | None,
        config: HistoryConfig,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.storage = storage
        self.config = config
        self._clock = clock
        self._lock = threading.RLock()
        self.last_outcome: PipelineOutcome | None = None
        self.last_identifier: str | None = None
        self.last_corrected_text: str | None = None

    def consume(self, outcome: PipelineOutcome) -> None:
        with self._lock:
            self.last_outcome = outcome
            self.last_identifier = None
            self.last_corrected_text = None
        if not self.config.enabled or self.storage is None:
            return
        item = outcome.dictation
        trace = item.trace
        identifier = str(uuid4())
        self.storage.save_transcription(
            TranscriptionRecord(
                identifier=identifier,
                created_at=self._clock(),
                raw_text=item.raw_text,
                normalized_text=item.normalized_text,
                final_text=item.final_text,
                corrected_text=None,
                language=item.transcription.language,
                route=trace.route,
                reason_code=trace.reason,
                fallback_code=trace.fallback_code,
                confidence_proxy=item.transcription.confidence_proxy,
                audio_seconds=item.transcription.audio_duration_seconds,
                stt_seconds=trace.stt_seconds,
                normalization_seconds=trace.normalization_seconds,
                postprocess_seconds=trace.postprocess_seconds,
                total_seconds=trace.total_seconds,
                delivery_status=outcome.delivery_status,
                error_code=outcome.error_code,
                app_bundle_id=(item.app_bundle_id if self.config.store_app_bundle_id else None),
            )
        )
        with self._lock:
            self.last_identifier = identifier

    def save_last_correction(self, corrected_text: str) -> bool:
        with self._lock:
            identifier = self.last_identifier
            self.last_corrected_text = corrected_text
        if not identifier or self.storage is None or not self.config.enabled:
            return True
        return self.storage.save_correction(identifier, corrected_text)
