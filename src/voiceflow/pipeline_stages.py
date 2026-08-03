from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

from voiceflow.config import FormattingConfig
from voiceflow.normalization import (
    NormalizationRequest,
    NormalizationResult,
)
from voiceflow.postprocess.base import (
    PostProcessor,
    PostprocessRequest,
    PostprocessResult,
)
from voiceflow.postprocess.router import RoutingDecision
from voiceflow.processing import (
    PipelineTrace,
    PostprocessRoute,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.transcription import TranscriptionResult, TranscriptSegment


class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> object: ...


class Normalizer(Protocol):
    def normalize(self, request: NormalizationRequest) -> NormalizationResult: ...


class PostprocessRouting(Protocol):
    def decide(self, request: PostprocessRequest) -> RoutingDecision: ...

    def process_decision(
        self,
        request: PostprocessRequest,
        decision: RoutingDecision,
    ) -> PostprocessResult: ...


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


@dataclass(frozen=True, slots=True)
class PostprocessStageResult:
    postprocess: PostprocessResult
    final_text: str
    route: PostprocessRoute | None
    reason: RouteReason | None


class PostprocessStage:
    def __init__(
        self,
        postprocessor: PostProcessor,
        *,
        postprocess_router: PostprocessRouting | None = None,
        preferred_terms: Callable[[str | None, str | None], tuple[str, ...]] | None = None,
        on_provider_route: Callable[[], None] | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._postprocessor = postprocessor
        self._postprocess_router = postprocess_router
        self._preferred_terms = preferred_terms or (lambda _language, _app: ())
        self._on_provider_route = on_provider_route or (lambda: None)
        self._clock_ns = clock_ns

    def run(
        self,
        normalization: NormalizationResult,
        *,
        raw_text: str,
        transcription: TranscriptionResult,
        processing_intent: ProcessingIntent,
        app_bundle_id: str | None,
        trace: PipelineTrace,
    ) -> PostprocessStageResult:
        trace.postprocess_started_ns = self._clock_ns()
        request = PostprocessRequest(
            text=normalization.text,
            raw_text=raw_text,
            transcription=transcription,
            language=transcription.language,
            processing_intent=processing_intent,
            detected_signals=normalization.detected_signals,
            protected_spans=normalization.protected_spans,
        )
        try:
            if self._postprocess_router is not None:
                decision = self._postprocess_router.decide(request)
                if decision.route == PostprocessRoute.PROVIDER:
                    request = self._with_preferred_terms(
                        request,
                        language=transcription.language,
                        app_bundle_id=app_bundle_id,
                    )
                    self._on_provider_route()
                postprocess = self._postprocess_router.process_decision(request, decision)
                trace.route = postprocess.route
                trace.reason = postprocess.reason
            elif self._postprocessor.name == "none":
                trace.route = PostprocessRoute.RULES_ONLY
                trace.reason = RouteReason.PROVIDER_DISABLED
                postprocess = self._postprocessor.process(normalization.text)
            else:
                trace.route = PostprocessRoute.PROVIDER
                trace.reason = RouteReason.ALWAYS_MODE
                request = self._with_preferred_terms(
                    request,
                    language=transcription.language,
                    app_bundle_id=app_bundle_id,
                )
                self._on_provider_route()
                postprocess = self._postprocessor.process(request)
            return PostprocessStageResult(
                postprocess=postprocess,
                final_text=postprocess.text,
                route=trace.route,
                reason=trace.reason,
            )
        finally:
            trace.postprocess_ended_ns = self._clock_ns()

    def _with_preferred_terms(
        self,
        request: PostprocessRequest,
        *,
        language: str | None,
        app_bundle_id: str | None,
    ) -> PostprocessRequest:
        return replace(
            request,
            preferred_terms=self._preferred_terms(language, app_bundle_id),
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
