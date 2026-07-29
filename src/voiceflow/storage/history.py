from __future__ import annotations

import threading
import time
from collections.abc import Callable
from uuid import uuid4

from voiceflow.config import HistoryConfig
from voiceflow.processing import PipelineOutcome
from voiceflow.storage.database import VoiceBudStorage


class HistoryResultSink:
    def __init__(
        self,
        storage: VoiceBudStorage | None,
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
            (
                identifier,
                self._clock(),
                item.raw_text,
                item.normalized_text,
                item.final_text,
                None,
                item.transcription.language,
                trace.route.value if trace.route else None,
                trace.reason.value if trace.reason else None,
                trace.fallback_code,
                item.transcription.confidence_proxy,
                item.transcription.audio_duration_seconds,
                trace.stt_seconds,
                trace.normalization_seconds,
                trace.postprocess_seconds,
                trace.total_seconds,
                outcome.delivery_status.value,
                outcome.error_code,
                item.app_bundle_id if self.config.store_app_bundle_id else None,
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
