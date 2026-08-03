from __future__ import annotations

from typing import Any

import numpy as np
import pytest

from voiceflow.pipeline_stages import TranscriptionStage, TranscriptionStageResult
from voiceflow.processing import PipelineTrace
from voiceflow.transcription import TranscriptionResult, TranscriptSegment


class StepClock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


class CapturingTranscriber:
    def __init__(self, result: object) -> None:
        self.result = result
        self.calls: list[tuple[np.ndarray, int]] = []

    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> object:
        self.calls.append((audio, sample_rate))
        return self.result


def test_transcription_stage_coerces_text_with_exact_result_and_timing() -> None:
    audio = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float32)
    transcriber = CapturingTranscriber("  spoken text  ")
    trace = PipelineTrace(queued_ns=10)

    result = TranscriptionStage(transcriber, clock_ns=StepClock(30, 40)).run(
        audio,
        sample_rate=2,
        trace=trace,
    )

    assert result == TranscriptionStageResult(
        transcription=TranscriptionResult(
            text="spoken text",
            segments=(
                TranscriptSegment(
                    text="spoken text",
                    start_seconds=0,
                    end_seconds=2,
                ),
            ),
            audio_duration_seconds=2,
            inference_seconds=1e-8,
        ),
        raw_text="spoken text",
    )
    assert transcriber.calls == [(audio, 2)]
    assert (trace.stt_started_ns, trace.stt_ended_ns) == (30, 40)


def test_transcription_stage_preserves_structured_transcription_metadata() -> None:
    transcription = TranscriptionResult(
        text="structured",
        segments=(TranscriptSegment("structured", 0.25, 0.75),),
        audio_duration_seconds=1.5,
        inference_seconds=0.4,
        language="it",
        language_source="configured",
        confidence_proxy=0.93,
    )
    trace = PipelineTrace(queued_ns=10)

    result = TranscriptionStage(
        CapturingTranscriber(transcription),
        clock_ns=StepClock(30, 40),
    ).run(np.ones(8, dtype=np.float32), sample_rate=4, trace=trace)

    assert result == TranscriptionStageResult(transcription, "structured")
    assert result.transcription is transcription
    assert (trace.stt_started_ns, trace.stt_ended_ns) == (30, 40)


@pytest.mark.parametrize("transcribed", [None, "   "])
def test_transcription_stage_returns_exact_empty_result(transcribed: object) -> None:
    trace = PipelineTrace(queued_ns=10)

    result = TranscriptionStage(
        CapturingTranscriber(transcribed),
        clock_ns=StepClock(30, 40),
    ).run(np.ones(4, dtype=np.float32), sample_rate=2, trace=trace)

    assert result == TranscriptionStageResult(
        transcription=TranscriptionResult.empty(
            audio_duration_seconds=2,
            inference_seconds=1e-8,
        ),
        raw_text="",
    )
    assert (trace.stt_started_ns, trace.stt_ended_ns) == (30, 40)


def test_transcription_stage_preserves_transcriber_error_and_partial_timing() -> None:
    failure = RuntimeError("bad audio")

    class FailingTranscriber:
        def transcribe(self, _audio: np.ndarray, *, sample_rate: int) -> Any:
            assert sample_rate == 16000
            raise failure

    trace = PipelineTrace(queued_ns=10)
    stage = TranscriptionStage(FailingTranscriber(), clock_ns=StepClock(30, 40))

    with pytest.raises(RuntimeError) as raised:
        stage.run(np.ones(1, dtype=np.float32), sample_rate=16000, trace=trace)

    assert raised.value is failure
    assert (trace.stt_started_ns, trace.stt_ended_ns) == (30, None)
