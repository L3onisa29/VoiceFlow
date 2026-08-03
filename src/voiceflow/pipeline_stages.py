from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from voiceflow.config import FormattingConfig
from voiceflow.normalization import (
    NormalizationRequest,
    NormalizationResult,
)
from voiceflow.processing import PipelineTrace
from voiceflow.transcription import TranscriptionResult, TranscriptSegment


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> object: ...


class Normalizer(Protocol):
    def normalize(self, request: NormalizationRequest) -> NormalizationResult: ...


@dataclass(frozen=True, slots=True)
class TranscriptionStageResult:
    transcription: TranscriptionResult
    raw_text: str


class TranscriptionStage:
    def __init__(
        self,
        transcriber: Transcriber,
        *,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._transcriber = transcriber
        self._clock_ns = clock_ns

    def run(
        self,
        audio: np.ndarray,
        *,
        sample_rate: int,
        trace: PipelineTrace,
    ) -> TranscriptionStageResult:
        trace.stt_started_ns = self._clock_ns()
        transcribed = self._transcriber.transcribe(audio, sample_rate=sample_rate)
        trace.stt_ended_ns = self._clock_ns()
        transcription = _coerce_transcription(
            transcribed,
            audio_duration_seconds=len(audio) / sample_rate,
            inference_seconds=trace.stt_seconds or 0,
        )
        return TranscriptionStageResult(
            transcription=transcription,
            raw_text=transcription.text,
        )


@dataclass(frozen=True, slots=True)
class NormalizationStageResult:
    normalization: NormalizationResult
    normalized_text: str


class NormalizationStage:
    def __init__(
        self,
        normalizer: Normalizer,
        formatting: FormattingConfig,
        *,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._normalizer = normalizer
        self._formatting = formatting
        self._clock_ns = clock_ns

    def run(
        self,
        raw_text: str,
        *,
        language: str | None,
        app_bundle_id: str | None,
        trace: PipelineTrace,
    ) -> NormalizationStageResult:
        trace.normalization_started_ns = self._clock_ns()
        normalization = self._normalizer.normalize(
            NormalizationRequest(
                raw_text,
                language=language,
                app_bundle_id=app_bundle_id,
                spoken_punctuation=self._formatting.spoken_punctuation,
                explicit_lists=self._formatting.explicit_lists,
                structured_tokens=self._formatting.structured_tokens,
                backtrack=self._formatting.backtrack,
            )
        )
        trace.normalization_ended_ns = self._clock_ns()
        return NormalizationStageResult(
            normalization=normalization,
            normalized_text=normalization.text,
        )


def _coerce_transcription(
    result: object,
    *,
    audio_duration_seconds: float,
    inference_seconds: float,
) -> TranscriptionResult:
    if isinstance(result, TranscriptionResult):
        return result
    text = "" if result is None else str(result).strip()
    segments = (
        TranscriptSegment(
            text=text,
            start_seconds=0,
            end_seconds=audio_duration_seconds,
        )
        if text
        else None
    )
    segments = (segments,) if segments is not None else ()
    return TranscriptionResult(
        text=text,
        segments=segments,
        audio_duration_seconds=audio_duration_seconds,
        inference_seconds=inference_seconds,
    )
