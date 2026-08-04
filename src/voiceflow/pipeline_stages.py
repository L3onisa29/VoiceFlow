from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

import numpy as np

from voiceflow.config import FormattingConfig
from voiceflow.diagnostics import log_event
from voiceflow.integrations import (
    BridgeOutcome,
    BridgeResult,
    DictationDestination,
    T3CodeDestination,
)
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
    DeliveryStatus,
    PipelineTrace,
    PostprocessRoute,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.system.text_injector import InjectionDestination
from voiceflow.transcription import TranscriptionResult, TranscriptSegment

DELIVERY_LOGGER = logging.getLogger("voiceflow.pipeline")


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


class TextDelivery(Protocol):
    def inject(self, text: str, *, destination: InjectionDestination) -> None: ...


class DeliveryStatusPresenter(Protocol):
    def transient(self, text: str, *, error: bool = False) -> None: ...

    def complete(self, *, fallback: bool, detail: str | None) -> None: ...

    def copied(self, *, detail: str | None = None) -> None: ...


class T3DeliveryBridge(Protocol):
    def deliver_transcription(
        self,
        recording_id: str,
        transcript: str,
    ) -> BridgeResult: ...


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


@dataclass(frozen=True, slots=True)
class DeliveryStageResult:
    delivery_status: DeliveryStatus
    error_code: str | None
    destination: DictationDestination


class DeliveryStage:
    def __init__(
        self,
        injector: TextDelivery,
        status: DeliveryStatusPresenter,
        *,
        injection_scheduler: Callable[[Callable[[], None]], None],
        injection_timeout_seconds: float,
        t3_bridge: T3DeliveryBridge | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        raise_if_cancelled: Callable[[], None] | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
    ) -> None:
        self._injector = injector
        self._status = status
        self._schedule_injection = injection_scheduler
        self._injection_timeout_seconds = injection_timeout_seconds
        self._t3_bridge = t3_bridge
        self._is_cancelled = is_cancelled or (lambda: False)
        self._raise_if_cancelled = raise_if_cancelled or (lambda: None)
        self._clock_ns = clock_ns

    def run(
        self,
        final_text: str,
        *,
        destination: DictationDestination,
        used_fallback: bool,
        fallback_provider: str,
        trace: PipelineTrace,
        log_context: dict[str, object] | None = None,
    ) -> DeliveryStageResult:
        trace.injection_started_ns = self._clock_ns()
        try:
            if isinstance(destination, T3CodeDestination):
                delivery, error_code = self._deliver_to_t3(
                    destination,
                    final_text,
                    log_context=log_context or {},
                )
            else:
                self._inject_on_main_thread(final_text, destination=destination)
                delivery = (
                    DeliveryStatus.CLIPBOARD_ONLY
                    if destination.uses_clipboard_only
                    else DeliveryStatus.INJECTED
                )
                error_code = None
        finally:
            trace.injection_ended_ns = self._clock_ns()
        self._raise_if_cancelled()
        self._complete(
            destination=destination,
            delivery=delivery,
            used_fallback=used_fallback,
            fallback_provider=fallback_provider,
        )
        return DeliveryStageResult(delivery, error_code, destination)

    def _deliver_to_t3(
        self,
        destination: T3CodeDestination,
        final_text: str,
        *,
        log_context: dict[str, object],
    ) -> tuple[DeliveryStatus, str | None]:
        if self._t3_bridge is None:
            self._status.transient("Bridge T3 Code non disponibile", error=True)
            return DeliveryStatus.T3_FAILED, "t3_validation_error"
        result = self._t3_bridge.deliver_transcription(
            recording_id=destination.recording_id,
            transcript=final_text,
        )
        log_event(
            DELIVERY_LOGGER,
            logging.DEBUG if result.outcome == BridgeOutcome.ACCEPTED else logging.WARNING,
            "integration.t3.delivery",
            "T3 Code delivery returned",
            context=log_context,
            integration_outcome=result.outcome,
            error_code=result.error_code,
            remote_code=result.remote_code,
        )
        if result.outcome == BridgeOutcome.ACCEPTED:
            return DeliveryStatus.T3_DELIVERED, None
        if result.outcome == BridgeOutcome.REJECTED:
            self._status.transient("T3 Code ha rifiutato la trascrizione", error=True)
            return DeliveryStatus.T3_REJECTED, result.error_code or "t3_rejected"
        if result.outcome == BridgeOutcome.AMBIGUOUS:
            self._status.transient(
                "Consegna a T3 Code non verificabile — nessun nuovo tentativo",
                error=True,
            )
            return DeliveryStatus.T3_AMBIGUOUS, result.error_code or "t3_ambiguous"
        self._status.transient(
            result.message or "Consegna a T3 Code non riuscita",
            error=True,
        )
        return DeliveryStatus.T3_FAILED, result.error_code or "t3_validation_error"

    def _inject_on_main_thread(
        self,
        text: str,
        *,
        destination: InjectionDestination,
    ) -> None:
        started = threading.Event()
        completed = threading.Event()
        callback_lock = threading.Lock()
        callback_state = "pending"
        error: list[BaseException] = []

        def inject() -> None:
            nonlocal callback_state
            with callback_lock:
                if callback_state != "pending" or self._is_cancelled():
                    callback_state = "cancelled"
                    completed.set()
                    return
                callback_state = "started"
                started.set()
            try:
                self._injector.inject(text, destination=destination)
            except BaseException as exc:
                error.append(exc)
            finally:
                completed.set()

        self._schedule_injection(inject)
        if not started.wait(timeout=self._injection_timeout_seconds):
            with callback_lock:
                if callback_state == "pending":
                    callback_state = "cancelled"
                    timed_out = True
                else:
                    timed_out = False
            if timed_out:
                self._raise_if_cancelled()
                raise TimeoutError(
                    "Text insertion did not start within "
                    f"{self._injection_timeout_seconds:g} seconds"
                )
        completed.wait()
        if error:
            raise error[0]

    def _complete(
        self,
        *,
        destination: DictationDestination,
        delivery: DeliveryStatus,
        used_fallback: bool,
        fallback_provider: str,
    ) -> None:
        completion_details: list[str] = []
        if isinstance(destination, InjectionDestination) and destination.uses_clipboard_only:
            completion_details.append("Nessuna destinazione — testo copiato negli appunti")
        if used_fallback:
            completion_details.append(
                f"{fallback_provider} non disponibile — usato testo normalizzato"
            )
        if delivery == DeliveryStatus.T3_DELIVERED:
            completion_details.insert(0, "Consegnato a T3 Code")
        detail = "; ".join(completion_details) or None
        if delivery == DeliveryStatus.CLIPBOARD_ONLY:
            self._status.copied(detail=detail)
        elif delivery in {DeliveryStatus.INJECTED, DeliveryStatus.T3_DELIVERED}:
            self._status.complete(
                fallback=used_fallback,
                detail=detail,
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
