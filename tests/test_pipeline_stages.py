from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pytest

from voiceflow.config import FormattingConfig
from voiceflow.normalization import (
    DeterministicNormalizer,
    NormalizationOperation,
    NormalizationRequest,
    NormalizationResult,
    ProtectedSpan,
)
from voiceflow.pipeline_stages import (
    NormalizationStage,
    NormalizationStageResult,
    TranscriptionStage,
    TranscriptionStageResult,
)
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


class CapturingNormalizer:
    def __init__(self, result: NormalizationResult) -> None:
        self.result = result
        self.requests: list[NormalizationRequest] = []

    def normalize(self, request: NormalizationRequest) -> NormalizationResult:
        self.requests.append(request)
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


def test_normalization_stage_preserves_result_metadata_operations_and_timing() -> None:
    normalization = NormalizationResult(
        text="VoiceFlow.",
        operations=(
            NormalizationOperation(
                "dictionary_replacement",
                "voice flow",
                "VoiceFlow",
                0,
                10,
            ),
            NormalizationOperation("spoken_punctuation", "punto", ".", 11, 16),
        ),
        detected_signals=frozenset({"backtrack"}),
        protected_spans=(ProtectedSpan("VoiceFlow", "dictionary", 0, 9),),
    )
    normalizer = CapturingNormalizer(normalization)
    trace = PipelineTrace(queued_ns=10)

    result = NormalizationStage(
        normalizer,
        FormattingConfig(),
        clock_ns=StepClock(30, 40),
    ).run(
        "voice flow punto",
        language="it",
        app_bundle_id="com.example.editor",
        trace=trace,
    )

    assert result == NormalizationStageResult(normalization, "VoiceFlow.")
    assert result.normalization is normalization
    assert normalizer.requests == [
        NormalizationRequest(
            "voice flow punto",
            language="it",
            app_bundle_id="com.example.editor",
            spoken_punctuation=True,
            explicit_lists=True,
            structured_tokens=True,
            backtrack=True,
        )
    ]
    assert (trace.normalization_started_ns, trace.normalization_ended_ns) == (30, 40)


def test_normalization_stage_no_op_preserves_exact_result() -> None:
    trace = PipelineTrace(queued_ns=10)

    result = NormalizationStage(
        DeterministicNormalizer(),
        FormattingConfig(),
        clock_ns=StepClock(30, 40),
    ).run(
        "frase breve pulita",
        language=None,
        app_bundle_id=None,
        trace=trace,
    )

    assert result == NormalizationStageResult(
        NormalizationResult(text="frase breve pulita"),
        "frase breve pulita",
    )
    assert (trace.normalization_started_ns, trace.normalization_ended_ns) == (30, 40)


def test_normalization_stage_forwards_disabled_formatting_flags() -> None:
    normalizer = CapturingNormalizer(NormalizationResult("unchanged"))

    NormalizationStage(
        normalizer,
        FormattingConfig(
            spoken_punctuation=False,
            explicit_lists=False,
            structured_tokens=False,
            backtrack=False,
        ),
        clock_ns=StepClock(30, 40),
    ).run(
        "unchanged",
        language="en",
        app_bundle_id="com.example.notes",
        trace=PipelineTrace(queued_ns=10),
    )

    assert normalizer.requests == [
        NormalizationRequest(
            "unchanged",
            language="en",
            app_bundle_id="com.example.notes",
            spoken_punctuation=False,
            explicit_lists=False,
            structured_tokens=False,
            backtrack=False,
        )
    ]


def test_normalization_stage_preserves_normalizer_error_and_partial_timing() -> None:
    failure = RuntimeError("dictionary unavailable")

    class FailingNormalizer:
        def normalize(self, request: NormalizationRequest) -> NormalizationResult:
            assert request.text == "raw text"
            raise failure

    trace = PipelineTrace(queued_ns=10)
    stage = NormalizationStage(
        FailingNormalizer(),
        FormattingConfig(),
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(RuntimeError) as raised:
        stage.run(
            "raw text",
            language="it",
            app_bundle_id=None,
            trace=trace,
        )

    assert raised.value is failure
    assert (trace.normalization_started_ns, trace.normalization_ended_ns) == (30, None)


def test_normalization_stage_result_is_frozen_and_slotted() -> None:
    result = NormalizationStageResult(NormalizationResult("text"), "text")

    with pytest.raises(FrozenInstanceError):
        result.normalized_text = "changed"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
