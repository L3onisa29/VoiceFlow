from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import Any

import numpy as np
import pytest

from voiceflow.config import FormattingConfig
from voiceflow.integrations import BridgeOutcome, BridgeResult, T3CodeDestination
from voiceflow.normalization import (
    DeterministicNormalizer,
    NormalizationOperation,
    NormalizationRequest,
    NormalizationResult,
    ProtectedSpan,
)
from voiceflow.pipeline_stages import (
    DeliveryStage,
    DeliveryStageResult,
    NormalizationStage,
    NormalizationStageResult,
    PostprocessStage,
    PostprocessStageResult,
    TranscriptionStage,
    TranscriptionStageResult,
)
from voiceflow.postprocess.base import (
    PostprocessRequest,
    PostprocessResult,
    ProviderOperation,
)
from voiceflow.postprocess.router import RoutingDecision
from voiceflow.processing import (
    DeliveryStatus,
    PipelineTrace,
    PostprocessRoute,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.system.text_injector import (
    FocusChangedError,
    FocusTarget,
    InjectionDestination,
)
from voiceflow.transcription import TranscriptionResult, TranscriptSegment


class StepClock:
    def __init__(self, *values: int) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def transcription_result(
    text: str = "raw",
    *,
    language: str | None = None,
) -> TranscriptionResult:
    return TranscriptionResult(
        text=text,
        segments=(),
        audio_duration_seconds=0,
        inference_seconds=0,
        language=language,
        language_source="configured" if language else "automatic_unknown",
    )


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


class CapturingPostprocessor:
    name = "provider"

    def __init__(self, result: PostprocessResult) -> None:
        self.result = result
        self.requests: list[PostprocessRequest | str] = []

    def preflight(self) -> bool:
        return True

    def process(self, request: PostprocessRequest | str) -> PostprocessResult:
        self.requests.append(request)
        return self.result

    def retry(self) -> bool:
        return True

    def close(self) -> None:
        pass


class CapturingRouter:
    def __init__(
        self,
        decision: RoutingDecision,
        result: PostprocessResult,
    ) -> None:
        self.decision = decision
        self.result = result
        self.decide_requests: list[PostprocessRequest] = []
        self.process_calls: list[tuple[PostprocessRequest, RoutingDecision]] = []

    def decide(self, request: PostprocessRequest) -> RoutingDecision:
        self.decide_requests.append(request)
        return self.decision

    def process_decision(
        self,
        request: PostprocessRequest,
        decision: RoutingDecision,
    ) -> PostprocessResult:
        self.process_calls.append((request, decision))
        return self.result


@pytest.mark.parametrize(
    ("provider_name", "reason"),
    [
        ("provider", RouteReason.RULES_ONLY_MODE),
        ("none", RouteReason.PROVIDER_DISABLED),
    ],
)
def test_postprocess_stage_rules_routes_do_not_load_terms_or_publish_provider_state(
    provider_name: str,
    reason: RouteReason,
) -> None:
    provider = CapturingPostprocessor(PostprocessResult("must not run"))
    provider.name = provider_name
    decision = RoutingDecision(PostprocessRoute.RULES_ONLY, reason)
    routed_result = PostprocessResult(
        "normalized",
        route=PostprocessRoute.RULES_ONLY,
        reason=reason,
    )
    router = CapturingRouter(decision, routed_result)
    term_lookups: list[tuple[str | None, str | None]] = []
    provider_routes: list[str] = []
    trace = PipelineTrace(queued_ns=10)

    result = PostprocessStage(
        provider,
        postprocess_router=router,
        preferred_terms=lambda language, app: term_lookups.append((language, app)) or ("unused",),
        on_provider_route=lambda: provider_routes.append("provider"),
        clock_ns=StepClock(30, 40),
    ).run(
        NormalizationResult(
            "normalized",
            detected_signals=frozenset({"filler"}),
            protected_spans=(ProtectedSpan("VoiceFlow", "dictionary", 0, 9),),
        ),
        raw_text="raw",
        transcription=transcription_result(language="it"),
        processing_intent=ProcessingIntent.AUTO,
        app_bundle_id="com.example.editor",
        trace=trace,
    )

    assert result == PostprocessStageResult(
        postprocess=routed_result,
        final_text="normalized",
        route=PostprocessRoute.RULES_ONLY,
        reason=reason,
    )
    assert router.decide_requests == [
        PostprocessRequest(
            text="normalized",
            raw_text="raw",
            transcription=transcription_result(language="it"),
            language="it",
            detected_signals=frozenset({"filler"}),
            protected_spans=(ProtectedSpan("VoiceFlow", "dictionary", 0, 9),),
        )
    ]
    assert router.process_calls == [(router.decide_requests[0], decision)]
    assert provider.requests == []
    assert term_lookups == []
    assert provider_routes == []
    assert (trace.route, trace.reason) == (PostprocessRoute.RULES_ONLY, reason)
    assert (trace.postprocess_started_ns, trace.postprocess_ended_ns) == (30, 40)


def test_postprocess_stage_provider_route_uses_one_decision_and_one_term_lookup() -> None:
    transcription = transcription_result(language="it")
    operation = ProviderOperation("grammar", "normalized", "polished")
    provider_result = PostprocessResult(
        "polished",
        operations=(operation,),
        route=PostprocessRoute.PROVIDER,
        reason=RouteReason.SEMANTIC_CLEANUP,
    )
    decision = RoutingDecision(
        PostprocessRoute.PROVIDER,
        RouteReason.SEMANTIC_CLEANUP,
    )
    router = CapturingRouter(decision, provider_result)
    provider = CapturingPostprocessor(provider_result)
    term_lookups: list[tuple[str | None, str | None]] = []
    provider_routes: list[str] = []
    trace = PipelineTrace(queued_ns=10)

    result = PostprocessStage(
        provider,
        postprocess_router=router,
        preferred_terms=lambda language, app: (
            term_lookups.append((language, app)) or ("VoiceFlow",)
        ),
        on_provider_route=lambda: provider_routes.append("provider"),
        clock_ns=StepClock(30, 40),
    ).run(
        NormalizationResult("normalized", detected_signals=frozenset({"filler"})),
        raw_text="raw",
        transcription=transcription,
        processing_intent=ProcessingIntent.DEEP,
        app_bundle_id="com.example.editor",
        trace=trace,
    )

    assert result == PostprocessStageResult(
        postprocess=provider_result,
        final_text="polished",
        route=PostprocessRoute.PROVIDER,
        reason=RouteReason.SEMANTIC_CLEANUP,
    )
    assert result.postprocess is provider_result
    assert result.postprocess.operations == (operation,)
    assert len(router.decide_requests) == 1
    assert len(router.process_calls) == 1
    routed_request, routed_decision = router.process_calls[0]
    assert routed_decision is decision
    assert routed_request == PostprocessRequest(
        text="normalized",
        raw_text="raw",
        transcription=transcription,
        preferred_terms=("VoiceFlow",),
        language="it",
        processing_intent=ProcessingIntent.DEEP,
        detected_signals=frozenset({"filler"}),
    )
    assert term_lookups == [("it", "com.example.editor")]
    assert provider_routes == ["provider"]
    assert (trace.route, trace.reason) == (
        PostprocessRoute.PROVIDER,
        RouteReason.SEMANTIC_CLEANUP,
    )


def test_postprocess_stage_preserves_provider_fallback_metadata() -> None:
    fallback = PostprocessResult(
        "normalized",
        operations=(ProviderOperation("grammar", "bad", "good"),),
        route=PostprocessRoute.PROVIDER,
        reason=RouteReason.ALWAYS_MODE,
        used_fallback=True,
        fallback_code="provider_unavailable",
        error="provider offline",
    )
    router = CapturingRouter(
        RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.ALWAYS_MODE),
        fallback,
    )

    result = PostprocessStage(
        CapturingPostprocessor(fallback),
        postprocess_router=router,
        clock_ns=StepClock(30, 40),
    ).run(
        NormalizationResult("normalized"),
        raw_text="raw",
        transcription=transcription_result(),
        processing_intent=ProcessingIntent.AUTO,
        app_bundle_id=None,
        trace=PipelineTrace(queued_ns=10),
    )

    assert result.postprocess is fallback
    assert result.final_text == "normalized"
    assert result.postprocess.used_fallback
    assert result.postprocess.fallback_code == "provider_unavailable"
    assert result.postprocess.error == "provider offline"


@pytest.mark.parametrize(
    ("provider_name", "expected_request", "route", "reason", "expected_lookups"),
    [
        (
            "none",
            "normalized",
            PostprocessRoute.RULES_ONLY,
            RouteReason.PROVIDER_DISABLED,
            [],
        ),
        (
            "provider",
            PostprocessRequest(
                text="normalized",
                raw_text="raw",
                transcription=transcription_result(language="en"),
                preferred_terms=("VoiceFlow",),
                language="en",
            ),
            PostprocessRoute.PROVIDER,
            RouteReason.ALWAYS_MODE,
            [("en", "com.example.editor")],
        ),
    ],
)
def test_postprocess_stage_preserves_legacy_routes(
    provider_name: str,
    expected_request: PostprocessRequest | str,
    route: PostprocessRoute,
    reason: RouteReason,
    expected_lookups: list[tuple[str | None, str | None]],
) -> None:
    provider_result = PostprocessResult("legacy-result")
    provider = CapturingPostprocessor(provider_result)
    provider.name = provider_name
    lookups: list[tuple[str | None, str | None]] = []
    trace = PipelineTrace(queued_ns=10)

    result = PostprocessStage(
        provider,
        preferred_terms=lambda language, app: lookups.append((language, app)) or ("VoiceFlow",),
        clock_ns=StepClock(30, 40),
    ).run(
        NormalizationResult("normalized"),
        raw_text="raw",
        transcription=transcription_result(language="en"),
        processing_intent=ProcessingIntent.AUTO,
        app_bundle_id="com.example.editor",
        trace=trace,
    )

    assert provider.requests == [expected_request]
    assert lookups == expected_lookups
    assert result == PostprocessStageResult(provider_result, "legacy-result", route, reason)
    assert (trace.route, trace.reason) == (route, reason)


@pytest.mark.parametrize("failure_point", ["decide", "process"])
def test_postprocess_stage_preserves_errors_and_records_completed_timing(
    failure_point: str,
) -> None:
    failure = RuntimeError(f"{failure_point} failed")

    class FailingRouter(CapturingRouter):
        def decide(self, request: PostprocessRequest) -> RoutingDecision:
            if failure_point == "decide":
                raise failure
            return super().decide(request)

        def process_decision(
            self,
            request: PostprocessRequest,
            decision: RoutingDecision,
        ) -> PostprocessResult:
            if failure_point == "process":
                raise failure
            return super().process_decision(request, decision)

    fallback = PostprocessResult(
        "unused",
        route=PostprocessRoute.PROVIDER,
        reason=RouteReason.ALWAYS_MODE,
    )
    router = FailingRouter(
        RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.ALWAYS_MODE),
        fallback,
    )
    trace = PipelineTrace(queued_ns=10)
    stage = PostprocessStage(
        CapturingPostprocessor(fallback),
        postprocess_router=router,
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(RuntimeError) as raised:
        stage.run(
            NormalizationResult("normalized"),
            raw_text="raw",
            transcription=transcription_result(),
            processing_intent=ProcessingIntent.AUTO,
            app_bundle_id=None,
            trace=trace,
        )

    assert raised.value is failure
    assert (trace.postprocess_started_ns, trace.postprocess_ended_ns) == (30, 40)
    assert (trace.route, trace.reason) == (None, None)


def test_postprocess_stage_result_is_frozen_and_slotted() -> None:
    result = PostprocessStageResult(
        PostprocessResult("text"),
        "text",
        PostprocessRoute.RULES_ONLY,
        RouteReason.CLEAN_FAST_PATH,
    )

    with pytest.raises(FrozenInstanceError):
        result.final_text = "changed"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")


class CapturingDeliveryStatus:
    def __init__(self) -> None:
        self.transients: list[tuple[str, bool]] = []
        self.completions: list[tuple[bool, str | None]] = []
        self.copies: list[str | None] = []

    def transient(self, text: str, *, error: bool = False) -> None:
        self.transients.append((text, error))

    def complete(self, *, fallback: bool, detail: str | None) -> None:
        self.completions.append((fallback, detail))

    def copied(self, *, detail: str | None = None) -> None:
        self.copies.append(detail)


class CapturingDeliveryInjector:
    def __init__(self, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.calls: list[tuple[str, InjectionDestination]] = []

    def inject(self, text: str, *, destination: InjectionDestination) -> None:
        self.calls.append((text, destination))
        if self.failure is not None:
            raise self.failure


class CapturingDeliveryBridge:
    def __init__(self, result: BridgeResult | BaseException) -> None:
        self.result = result
        self.calls: list[tuple[str, str]] = []

    def deliver_transcription(self, recording_id: str, transcript: str) -> BridgeResult:
        self.calls.append((recording_id, transcript))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


FOCUSED_DESTINATION = InjectionDestination.focused(FocusTarget(1, "field"))


@pytest.mark.parametrize(
    ("destination", "expected_delivery", "expected_detail"),
    [
        (FOCUSED_DESTINATION, DeliveryStatus.INJECTED, None),
        (
            InjectionDestination.clipboard_only(),
            DeliveryStatus.CLIPBOARD_ONLY,
            "Nessuna destinazione — testo copiato negli appunti; "
            "provider non disponibile — usato testo normalizzato",
        ),
    ],
)
def test_delivery_stage_injects_once_and_preserves_destination_timing_and_completion(
    destination: InjectionDestination,
    expected_delivery: DeliveryStatus,
    expected_detail: str | None,
) -> None:
    injector = CapturingDeliveryInjector()
    status = CapturingDeliveryStatus()
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        injector,
        status,
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        clock_ns=StepClock(30, 40),
    )

    result = stage.run(
        "final text",
        destination=destination,
        used_fallback=destination.uses_clipboard_only,
        fallback_provider="provider",
        trace=trace,
        log_context={"job_id": 7},
    )

    assert result == DeliveryStageResult(expected_delivery, None, destination)
    assert result.destination is destination
    assert injector.calls == [("final text", destination)]
    if destination.uses_clipboard_only:
        assert status.completions == []
        assert status.copies == [expected_detail]
    else:
        assert status.completions == [(False, expected_detail)]
        assert status.copies == []
    assert status.transients == []
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


@pytest.mark.parametrize(
    "failure",
    [FocusChangedError("focus moved"), PermissionError("permission denied")],
)
def test_delivery_stage_preserves_injector_exception_identity_and_completed_timing(
    failure: BaseException,
) -> None:
    injector = CapturingDeliveryInjector(failure)
    status = CapturingDeliveryStatus()
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        injector,
        status,
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(type(failure)) as raised:
        stage.run(
            "final text",
            destination=FOCUSED_DESTINATION,
            used_fallback=False,
            fallback_provider="provider",
            trace=trace,
        )

    assert raised.value is failure
    assert injector.calls == [("final text", FOCUSED_DESTINATION)]
    assert status.completions == []
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


def test_delivery_stage_dispatch_timeout_cancels_late_callback_without_injection() -> None:
    injector = CapturingDeliveryInjector()
    scheduled: list[Any] = []
    cancellation_checks: list[str] = []
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        injector,
        CapturingDeliveryStatus(),
        injection_scheduler=scheduled.append,
        injection_timeout_seconds=0,
        raise_if_cancelled=lambda: cancellation_checks.append("checked"),
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(TimeoutError, match="did not start within 0 seconds"):
        stage.run(
            "final text",
            destination=FOCUSED_DESTINATION,
            used_fallback=False,
            fallback_provider="provider",
            trace=trace,
        )

    assert cancellation_checks == ["checked"]
    assert len(scheduled) == 1
    scheduled[0]()
    assert injector.calls == []
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


def test_delivery_stage_checks_cancellation_after_delivery_before_completion() -> None:
    failure = RuntimeError("cancelled after delivery")

    def raise_cancelled() -> None:
        raise failure

    injector = CapturingDeliveryInjector()
    status = CapturingDeliveryStatus()
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        injector,
        status,
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        raise_if_cancelled=raise_cancelled,
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(RuntimeError) as raised:
        stage.run(
            "final text",
            destination=FOCUSED_DESTINATION,
            used_fallback=False,
            fallback_provider="provider",
            trace=trace,
        )

    assert raised.value is failure
    assert injector.calls == [("final text", FOCUSED_DESTINATION)]
    assert status.completions == []
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


@pytest.mark.parametrize(
    ("bridge_result", "expected_delivery", "expected_code", "expected_message"),
    [
        (BridgeResult.accepted_result(), DeliveryStatus.T3_DELIVERED, None, None),
        (
            BridgeResult.rejected("busy"),
            DeliveryStatus.T3_REJECTED,
            "t3_rejected",
            "T3 Code ha rifiutato la trascrizione",
        ),
        (
            BridgeResult.ambiguous(),
            DeliveryStatus.T3_AMBIGUOUS,
            "t3_ambiguous",
            "Consegna a T3 Code non verificabile — nessun nuovo tentativo",
        ),
        (
            BridgeResult(BridgeOutcome.ERROR, message="bridge offline"),
            DeliveryStatus.T3_FAILED,
            "t3_validation_error",
            "bridge offline",
        ),
    ],
)
def test_delivery_stage_maps_each_t3_outcome_once_without_injector_fallback(
    bridge_result: BridgeResult,
    expected_delivery: DeliveryStatus,
    expected_code: str | None,
    expected_message: str | None,
) -> None:
    injector = CapturingDeliveryInjector()
    status = CapturingDeliveryStatus()
    bridge = CapturingDeliveryBridge(bridge_result)
    destination = T3CodeDestination("recording-id")
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        injector,
        status,
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        t3_bridge=bridge,
        clock_ns=StepClock(30, 40),
    )

    result = stage.run(
        "final text",
        destination=destination,
        used_fallback=False,
        fallback_provider="provider",
        trace=trace,
        log_context={"job_id": 7},
    )

    assert result == DeliveryStageResult(expected_delivery, expected_code, destination)
    assert result.destination is destination
    assert bridge.calls == [("recording-id", "final text")]
    assert injector.calls == []
    assert status.transients == ([] if expected_message is None else [(expected_message, True)])
    assert status.completions == (
        [(False, "Consegnato a T3 Code")]
        if expected_delivery == DeliveryStatus.T3_DELIVERED
        else []
    )
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


def test_delivery_stage_missing_t3_bridge_fails_without_retry_or_fallback() -> None:
    injector = CapturingDeliveryInjector()
    status = CapturingDeliveryStatus()
    destination = T3CodeDestination("recording-id")

    result = DeliveryStage(
        injector,
        status,
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        clock_ns=StepClock(30, 40),
    ).run(
        "final text",
        destination=destination,
        used_fallback=False,
        fallback_provider="provider",
        trace=PipelineTrace(queued_ns=10),
    )

    assert result == DeliveryStageResult(
        DeliveryStatus.T3_FAILED,
        "t3_validation_error",
        destination,
    )
    assert injector.calls == []
    assert status.transients == [("Bridge T3 Code non disponibile", True)]
    assert status.completions == []


def test_delivery_stage_preserves_t3_exception_identity_and_completed_timing() -> None:
    failure = RuntimeError("bridge failed")
    bridge = CapturingDeliveryBridge(failure)
    trace = PipelineTrace(queued_ns=10)
    stage = DeliveryStage(
        CapturingDeliveryInjector(),
        CapturingDeliveryStatus(),
        injection_scheduler=lambda callback: callback(),
        injection_timeout_seconds=2,
        t3_bridge=bridge,
        clock_ns=StepClock(30, 40),
    )

    with pytest.raises(RuntimeError) as raised:
        stage.run(
            "final text",
            destination=T3CodeDestination("recording-id"),
            used_fallback=False,
            fallback_provider="provider",
            trace=trace,
        )

    assert raised.value is failure
    assert bridge.calls == [("recording-id", "final text")]
    assert (trace.injection_started_ns, trace.injection_ended_ns) == (30, 40)


def test_delivery_stage_result_is_frozen_and_slotted() -> None:
    result = DeliveryStageResult(
        DeliveryStatus.INJECTED,
        None,
        FOCUSED_DESTINATION,
    )

    with pytest.raises(FrozenInstanceError):
        result.error_code = "changed"  # type: ignore[misc]
    assert not hasattr(result, "__dict__")
