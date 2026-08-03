from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import numpy as np

from voiceflow.processing import PipelineTrace
from voiceflow.transcription import TranscriptionResult, TranscriptSegment


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> object: ...


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
