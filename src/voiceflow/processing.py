from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from voiceflow.transcription import TranscriptionResult


class ProcessingIntent(StrEnum):
    AUTO = "auto"
    DEEP = "deep"


class PostprocessRoute(StrEnum):
    RULES_ONLY = "rules_only"
    PROVIDER = "provider"


class RouteReason(StrEnum):
    PROVIDER_DISABLED = "provider_disabled"
    RULES_ONLY_MODE = "rules_only_mode"
    CLEAN_FAST_PATH = "clean_fast_path"
    DEEP_REQUESTED = "deep_requested"
    BACKTRACK_DETECTED = "backtrack_detected"
    SEMANTIC_CLEANUP = "semantic_cleanup"
    ALWAYS_MODE = "always_mode"


class DeliveryStatus(StrEnum):
    NOT_ATTEMPTED = "not_attempted"
    INJECTED = "injected"
    CLIPBOARD_ONLY = "clipboard_only"
    FOCUS_CHANGED = "focus_changed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EMPTY = "empty"
    T3_DELIVERED = "t3_delivered"
    T3_REJECTED = "t3_rejected"
    T3_AMBIGUOUS = "t3_ambiguous"
    T3_FAILED = "t3_failed"


@dataclass(slots=True)
class PipelineTrace:
    queued_ns: int
    dequeued_ns: int | None = None
    stt_started_ns: int | None = None
    stt_ended_ns: int | None = None
    normalization_started_ns: int | None = None
    normalization_ended_ns: int | None = None
    postprocess_started_ns: int | None = None
    postprocess_ended_ns: int | None = None
    injection_started_ns: int | None = None
    injection_ended_ns: int | None = None
    completed_ns: int | None = None
    route: PostprocessRoute | None = None
    reason: RouteReason | None = None
    fallback_code: str | None = None
    failed_stage: str | None = None
    delivery_status: DeliveryStatus = DeliveryStatus.NOT_ATTEMPTED

    @staticmethod
    def _elapsed_seconds(start: int | None, end: int | None) -> float | None:
        if start is None or end is None:
            return None
        return max(0, end - start) / 1_000_000_000

    @property
    def queue_seconds(self) -> float | None:
        return self._elapsed_seconds(self.queued_ns, self.dequeued_ns)

    @property
    def stt_seconds(self) -> float | None:
        return self._elapsed_seconds(self.stt_started_ns, self.stt_ended_ns)

    @property
    def normalization_seconds(self) -> float | None:
        return self._elapsed_seconds(self.normalization_started_ns, self.normalization_ended_ns)

    @property
    def postprocess_seconds(self) -> float | None:
        return self._elapsed_seconds(self.postprocess_started_ns, self.postprocess_ended_ns)

    @property
    def delivery_seconds(self) -> float | None:
        return self._elapsed_seconds(self.injection_started_ns, self.injection_ended_ns)

    @property
    def total_seconds(self) -> float | None:
        return self._elapsed_seconds(self.queued_ns, self.completed_ns)


@dataclass(frozen=True, slots=True)
class ProcessedDictation:
    sequence_id: int
    raw_text: str
    normalized_text: str
    final_text: str
    transcription: TranscriptionResult
    trace: PipelineTrace
    processing_intent: ProcessingIntent
    app_bundle_id: str | None = None


@dataclass(frozen=True, slots=True)
class PipelineOutcome:
    dictation: ProcessedDictation
    delivery_status: DeliveryStatus
    error_code: str | None = None


class PipelineResultSink(Protocol):
    def consume(self, outcome: PipelineOutcome) -> None: ...


class NullPipelineResultSink:
    def consume(self, outcome: PipelineOutcome) -> None:
        del outcome
