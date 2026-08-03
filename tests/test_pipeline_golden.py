from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np
import pytest

from voiceflow.config import RoutingConfig
from voiceflow.integrations import BridgeResult, T3CodeDestination
from voiceflow.normalization import (
    DeterministicNormalizer,
    NormalizationRequest,
    NormalizationResult,
)
from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess.base import PostprocessRequest, PostprocessResult
from voiceflow.postprocess.router import PostprocessRouter, RoutingDecision
from voiceflow.processing import (
    DeliveryStatus,
    PipelineOutcome,
    PostprocessRoute,
    RouteReason,
)
from voiceflow.state import ActivityState, SystemState
from voiceflow.system.text_injector import (
    FocusChangedError,
    FocusTarget,
    InjectionDestination,
)

FOCUSED_DESTINATION = InjectionDestination.focused(FocusTarget(7, "golden-field"))
CLIPBOARD_DESTINATION = InjectionDestination.clipboard_only()
T3_RECORDING_ID = "golden-recording"


class StepClock:
    def __init__(self) -> None:
        self.value = 0

    def __call__(self) -> int:
        self.value += 10
        return self.value


class Hook:
    def __init__(self, events: list[str], cancel_at: str | None) -> None:
        self.events = events
        self.cancel_at = cancel_at
        self.pipeline: DictationPipeline | None = None

    def reach(self, point: str) -> None:
        if self.cancel_at == point:
            assert self.pipeline is not None
            self.pipeline._cancelled.set()


class GoldenTranscriber:
    def __init__(self, text: str, hook: Hook) -> None:
        self.text = text
        self.hook = hook

    def transcribe(self, _audio: np.ndarray, *, sample_rate: int) -> str:
        assert sample_rate == 16000
        self.hook.events.append("transcription")
        self.hook.reach("after_transcription")
        return self.text


class GoldenNormalizer(DeterministicNormalizer):
    def __init__(self, hook: Hook) -> None:
        super().__init__()
        self.hook = hook

    def normalize(self, request: NormalizationRequest) -> NormalizationResult:
        self.hook.events.append("normalization")
        result = super().normalize(request)
        self.hook.reach("after_normalization")
        return result


class GoldenProvider:
    name = "golden-provider"

    def __init__(self, hook: Hook, behavior: str) -> None:
        self.hook = hook
        self.behavior = behavior

    def preflight(self) -> bool:
        return True

    def process(self, request: PostprocessRequest | str) -> PostprocessResult:
        self.hook.events.append("provider")
        text = request.text if isinstance(request, PostprocessRequest) else request
        if self.behavior == "fallback":
            result = PostprocessResult(
                text,
                used_fallback=True,
                fallback_code="provider_unavailable",
                error="provider unavailable",
            )
        else:
            result = PostprocessResult(f"clean-{text}")
        self.hook.reach("after_postprocess")
        return result

    def retry(self) -> bool:
        return True

    def close(self) -> None:
        pass


class GoldenRouter(PostprocessRouter):
    def __init__(self, provider: GoldenProvider, mode: str, events: list[str]) -> None:
        super().__init__(provider, RoutingConfig(mode=mode))
        self.events = events

    def process_decision(
        self,
        request: PostprocessRequest,
        decision: RoutingDecision,
    ) -> PostprocessResult:
        self.events.append("routing")
        return super().process_decision(request, decision)


class GoldenInjector:
    method = "paste"

    def __init__(self, hook: Hook, failure: str | None) -> None:
        self.hook = hook
        self.failure = failure
        self.calls: list[tuple[str, InjectionDestination]] = []
        self.injected: list[str] = []
        self.clipboard_fallbacks: list[str] = []

    def inject(self, text: str, *, destination: InjectionDestination) -> None:
        self.hook.events.append("injection")
        self.calls.append((text, destination))
        if self.failure == "focus_changed":
            self.clipboard_fallbacks.append(text)
            raise FocusChangedError("focus changed")
        if self.failure == "permission":
            raise PermissionError("accessibility denied")
        self.injected.append(text)
        self.hook.reach("after_delivery")


class GoldenBridge:
    def __init__(self, events: list[str], result: BridgeResult) -> None:
        self.events = events
        self.result = result
        self.deliveries: list[tuple[str, str]] = []
        self.abandoned: list[str] = []

    def deliver_transcription(self, recording_id: str, transcript: str) -> BridgeResult:
        self.events.append("t3_delivery")
        self.deliveries.append((recording_id, transcript))
        return self.result

    def abandon_recording(self, recording_id: str) -> None:
        self.abandoned.append(recording_id)


class GoldenStatus:
    def __init__(self, hook: Hook) -> None:
        self.hook = hook
        self.updates: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.completions: list[dict[str, Any]] = []

    def update(self, **changes: Any) -> None:
        self.updates.append(changes)
        if changes.get("activity") == ActivityState.INJECTING:
            self.hook.reach("before_delivery")

    def transient(self, text: str, **_kwargs: Any) -> None:
        self.messages.append(text)

    def complete(self, **kwargs: Any) -> None:
        self.completions.append(kwargs)


class GoldenSink:
    def __init__(self, events: list[str], *, fail: bool = False) -> None:
        self.events = events
        self.fail = fail
        self.outcomes: list[PipelineOutcome] = []

    def consume(self, outcome: PipelineOutcome) -> None:
        self.events.append("result_sink")
        self.outcomes.append(outcome)
        if self.fail:
            raise OSError("history unavailable")


@dataclass(frozen=True, slots=True)
class GoldenCase:
    name: str
    transcript: str = "raw text"
    routing_mode: str = "always"
    provider_behavior: str = "success"
    destination: str = "focused"
    cancellation: str | None = None
    injection_failure: str | None = None
    bridge_result: BridgeResult | None = None
    sink_failure: bool = False
    drop_dispatch: bool = False


@dataclass(frozen=True, slots=True)
class GoldenExpectation:
    delivery: DeliveryStatus
    error_code: str | None
    raw_text: str
    normalized_text: str
    final_text: str
    trace: dict[str, object]
    order: tuple[str, ...]
    injected: tuple[str, ...] = ()
    clipboard_fallbacks: tuple[str, ...] = ()
    bridge_deliveries: tuple[tuple[str, str], ...] = ()
    messages: tuple[str, ...] = ()
    completions: tuple[tuple[bool, str | None], ...] = ()


def trace(
    *,
    completed: int,
    delivery: DeliveryStatus,
    stt_end: int | None = 40,
    normalization: tuple[int | None, int | None] = (50, 60),
    postprocess: tuple[int | None, int | None] = (70, 80),
    injection: tuple[int | None, int | None] = (90, 100),
    route: PostprocessRoute | None = PostprocessRoute.PROVIDER,
    reason: RouteReason | None = RouteReason.ALWAYS_MODE,
    fallback_code: str | None = None,
    failed_stage: str | None = None,
) -> dict[str, object]:
    return {
        "queued_ns": 10,
        "dequeued_ns": 20,
        "stt_started_ns": 30,
        "stt_ended_ns": stt_end,
        "normalization_started_ns": normalization[0],
        "normalization_ended_ns": normalization[1],
        "postprocess_started_ns": postprocess[0],
        "postprocess_ended_ns": postprocess[1],
        "injection_started_ns": injection[0],
        "injection_ended_ns": injection[1],
        "completed_ns": completed,
        "route": route,
        "reason": reason,
        "fallback_code": fallback_code,
        "failed_stage": failed_stage,
        "delivery_status": delivery,
    }


FULL_PROVIDER_TRACE = trace(completed=110, delivery=DeliveryStatus.INJECTED)
FULL_RULES_TRACE = trace(
    completed=110,
    delivery=DeliveryStatus.INJECTED,
    route=PostprocessRoute.RULES_ONLY,
    reason=RouteReason.RULES_ONLY_MODE,
)

GOLDEN_MATRIX: tuple[tuple[GoldenCase, GoldenExpectation], ...] = (
    (
        GoldenCase("empty_transcript", transcript=""),
        GoldenExpectation(
            DeliveryStatus.EMPTY,
            "empty_transcript",
            "",
            "",
            "",
            trace(
                completed=50,
                delivery=DeliveryStatus.EMPTY,
                normalization=(None, None),
                postprocess=(None, None),
                injection=(None, None),
                route=None,
                reason=None,
            ),
            ("transcription", "result_sink"),
            messages=("Nessuna voce riconosciuta",),
        ),
    ),
    (
        GoldenCase("rules_only", routing_mode="rules_only"),
        GoldenExpectation(
            DeliveryStatus.INJECTED,
            None,
            "raw text",
            "raw text",
            "raw text",
            FULL_RULES_TRACE,
            ("transcription", "normalization", "routing", "injection", "result_sink"),
            injected=("raw text",),
            completions=((False, None),),
        ),
    ),
    (
        GoldenCase("provider_success"),
        GoldenExpectation(
            DeliveryStatus.INJECTED,
            None,
            "raw text",
            "raw text",
            "clean-raw text",
            FULL_PROVIDER_TRACE,
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            injected=("clean-raw text",),
            completions=((False, None),),
        ),
    ),
    (
        GoldenCase("provider_failure_fallback", provider_behavior="fallback"),
        GoldenExpectation(
            DeliveryStatus.INJECTED,
            None,
            "raw text",
            "raw text",
            "raw text",
            trace(
                completed=110,
                delivery=DeliveryStatus.INJECTED,
                fallback_code="provider_unavailable",
            ),
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            injected=("raw text",),
            completions=((True, "golden-provider non disponibile — usato testo normalizzato"),),
        ),
    ),
    (
        GoldenCase("cancel_after_transcription", cancellation="after_transcription"),
        GoldenExpectation(
            DeliveryStatus.CANCELLED,
            "cancelled",
            "raw text",
            "",
            "",
            trace(
                completed=50,
                delivery=DeliveryStatus.CANCELLED,
                normalization=(None, None),
                postprocess=(None, None),
                injection=(None, None),
                route=None,
                reason=None,
                failed_stage="transcription",
            ),
            ("transcription", "result_sink"),
        ),
    ),
    (
        GoldenCase("cancel_after_normalization", cancellation="after_normalization"),
        GoldenExpectation(
            DeliveryStatus.CANCELLED,
            "cancelled",
            "raw text",
            "raw text",
            "",
            trace(
                completed=90,
                delivery=DeliveryStatus.CANCELLED,
                injection=(None, None),
                failed_stage="postprocess",
            ),
            ("transcription", "normalization", "routing", "provider", "result_sink"),
        ),
    ),
    (
        GoldenCase("cancel_after_postprocess", cancellation="after_postprocess"),
        GoldenExpectation(
            DeliveryStatus.CANCELLED,
            "cancelled",
            "raw text",
            "raw text",
            "",
            trace(
                completed=90,
                delivery=DeliveryStatus.CANCELLED,
                injection=(None, None),
                failed_stage="postprocess",
            ),
            ("transcription", "normalization", "routing", "provider", "result_sink"),
        ),
    ),
    (
        GoldenCase("cancel_before_delivery", cancellation="before_delivery"),
        GoldenExpectation(
            DeliveryStatus.CANCELLED,
            "cancelled",
            "raw text",
            "raw text",
            "clean-raw text",
            trace(
                completed=90,
                delivery=DeliveryStatus.CANCELLED,
                injection=(None, None),
                failed_stage="postprocess",
            ),
            ("transcription", "normalization", "routing", "provider", "result_sink"),
        ),
    ),
    (
        GoldenCase("cancel_after_delivery", cancellation="after_delivery"),
        GoldenExpectation(
            DeliveryStatus.CANCELLED,
            "cancelled",
            "raw text",
            "raw text",
            "clean-raw text",
            trace(
                completed=110,
                delivery=DeliveryStatus.CANCELLED,
                failed_stage="delivery",
            ),
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            injected=("clean-raw text",),
        ),
    ),
    (
        GoldenCase(
            "normal_injection",
            transcript="hello virgola world punto",
            routing_mode="rules_only",
        ),
        GoldenExpectation(
            DeliveryStatus.INJECTED,
            None,
            "hello virgola world punto",
            "hello, world.",
            "hello, world.",
            FULL_RULES_TRACE,
            ("transcription", "normalization", "routing", "injection", "result_sink"),
            injected=("hello, world.",),
            completions=((False, None),),
        ),
    ),
    (
        GoldenCase("clipboard_only", destination="clipboard"),
        GoldenExpectation(
            DeliveryStatus.CLIPBOARD_ONLY,
            None,
            "raw text",
            "raw text",
            "clean-raw text",
            {**FULL_PROVIDER_TRACE, "delivery_status": DeliveryStatus.CLIPBOARD_ONLY},
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            injected=("clean-raw text",),
            completions=((False, "Nessuna destinazione — testo lasciato negli appunti"),),
        ),
    ),
    (
        GoldenCase("focus_changed", injection_failure="focus_changed"),
        GoldenExpectation(
            DeliveryStatus.FOCUS_CHANGED,
            "focus_changed",
            "raw text",
            "raw text",
            "clean-raw text",
            trace(
                completed=110,
                delivery=DeliveryStatus.FOCUS_CHANGED,
                failed_stage="delivery",
            ),
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            clipboard_fallbacks=("clean-raw text",),
            messages=("Destinazione cambiata — testo lasciato negli appunti",),
        ),
    ),
    (
        GoldenCase("permission_failure", injection_failure="permission"),
        GoldenExpectation(
            DeliveryStatus.FAILED,
            "permission_denied",
            "raw text",
            "raw text",
            "clean-raw text",
            trace(
                completed=110,
                delivery=DeliveryStatus.FAILED,
                failed_stage="delivery",
            ),
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            messages=("accessibility denied",),
        ),
    ),
    (
        GoldenCase("dispatch_timeout", drop_dispatch=True),
        GoldenExpectation(
            DeliveryStatus.FAILED,
            "injection_timeout",
            "raw text",
            "raw text",
            "clean-raw text",
            trace(
                completed=110,
                delivery=DeliveryStatus.FAILED,
                failed_stage="delivery",
            ),
            ("transcription", "normalization", "routing", "provider", "result_sink"),
            messages=(
                "Scrittura del testo non riuscita — TimeoutError: "
                "Text insertion did not start within 0 seconds",
            ),
        ),
    ),
    (
        GoldenCase(
            "t3_accepted",
            destination="t3",
            bridge_result=BridgeResult.accepted_result(),
        ),
        GoldenExpectation(
            DeliveryStatus.T3_DELIVERED,
            None,
            "raw text",
            "raw text",
            "clean-raw text",
            {**FULL_PROVIDER_TRACE, "delivery_status": DeliveryStatus.T3_DELIVERED},
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "t3_delivery",
                "result_sink",
            ),
            bridge_deliveries=((T3_RECORDING_ID, "clean-raw text"),),
            completions=((False, "Consegnato a T3 Code"),),
        ),
    ),
    (
        GoldenCase(
            "t3_rejected",
            destination="t3",
            bridge_result=BridgeResult.rejected("busy"),
        ),
        GoldenExpectation(
            DeliveryStatus.T3_REJECTED,
            "t3_rejected",
            "raw text",
            "raw text",
            "clean-raw text",
            {**FULL_PROVIDER_TRACE, "delivery_status": DeliveryStatus.T3_REJECTED},
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "t3_delivery",
                "result_sink",
            ),
            bridge_deliveries=((T3_RECORDING_ID, "clean-raw text"),),
            messages=("T3 Code ha rifiutato la trascrizione",),
        ),
    ),
    (
        GoldenCase(
            "t3_ambiguous_timeout",
            destination="t3",
            bridge_result=BridgeResult.ambiguous(),
        ),
        GoldenExpectation(
            DeliveryStatus.T3_AMBIGUOUS,
            "t3_ambiguous",
            "raw text",
            "raw text",
            "clean-raw text",
            {**FULL_PROVIDER_TRACE, "delivery_status": DeliveryStatus.T3_AMBIGUOUS},
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "t3_delivery",
                "result_sink",
            ),
            bridge_deliveries=((T3_RECORDING_ID, "clean-raw text"),),
            messages=("Consegna a T3 Code non verificabile — nessun nuovo tentativo",),
        ),
    ),
    (
        GoldenCase(
            "t3_failed",
            destination="t3",
            bridge_result=BridgeResult.validation_error(),
        ),
        GoldenExpectation(
            DeliveryStatus.T3_FAILED,
            "t3_validation_error",
            "raw text",
            "raw text",
            "clean-raw text",
            {**FULL_PROVIDER_TRACE, "delivery_status": DeliveryStatus.T3_FAILED},
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "t3_delivery",
                "result_sink",
            ),
            bridge_deliveries=((T3_RECORDING_ID, "clean-raw text"),),
            messages=("Integrazione locale T3 Code non valida",),
        ),
    ),
    (
        GoldenCase("result_sink_failure", sink_failure=True),
        GoldenExpectation(
            DeliveryStatus.INJECTED,
            None,
            "raw text",
            "raw text",
            "clean-raw text",
            FULL_PROVIDER_TRACE,
            (
                "transcription",
                "normalization",
                "routing",
                "provider",
                "injection",
                "result_sink",
            ),
            injected=("clean-raw text",),
            completions=((False, None),),
        ),
    ),
)

REQUIRED_GOLDEN_CASES = {
    "empty_transcript",
    "rules_only",
    "provider_success",
    "provider_failure_fallback",
    "cancel_after_transcription",
    "cancel_after_normalization",
    "cancel_after_postprocess",
    "cancel_before_delivery",
    "cancel_after_delivery",
    "normal_injection",
    "clipboard_only",
    "focus_changed",
    "permission_failure",
    "dispatch_timeout",
    "t3_accepted",
    "t3_rejected",
    "t3_ambiguous_timeout",
    "t3_failed",
    "result_sink_failure",
}


def wait_empty(pipeline: DictationPipeline, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while pipeline.pending and time.monotonic() < deadline:
        time.sleep(0.001)
    assert pipeline.pending == 0


def trace_snapshot(outcome: PipelineOutcome) -> dict[str, object]:
    pipeline_trace = outcome.dictation.trace
    return {
        field: getattr(pipeline_trace, field)
        for field in (
            "queued_ns",
            "dequeued_ns",
            "stt_started_ns",
            "stt_ended_ns",
            "normalization_started_ns",
            "normalization_ended_ns",
            "postprocess_started_ns",
            "postprocess_ended_ns",
            "injection_started_ns",
            "injection_ended_ns",
            "completed_ns",
            "route",
            "reason",
            "fallback_code",
            "failed_stage",
            "delivery_status",
        )
    }


def test_golden_matrix_case_lock() -> None:
    names = [case.name for case, _expected in GOLDEN_MATRIX]
    assert len(names) == len(set(names))
    assert set(names) == REQUIRED_GOLDEN_CASES


@pytest.mark.parametrize(
    ("case", "expected"),
    GOLDEN_MATRIX,
    ids=[case.name for case, _expected in GOLDEN_MATRIX],
)
def test_dictation_pipeline_golden_outcome_matrix(
    case: GoldenCase,
    expected: GoldenExpectation,
) -> None:
    events: list[str] = []
    hook = Hook(events, case.cancellation)
    provider = GoldenProvider(hook, case.provider_behavior)
    injector = GoldenInjector(hook, case.injection_failure)
    status = GoldenStatus(hook)
    sink = GoldenSink(events, fail=case.sink_failure)
    bridge = GoldenBridge(events, case.bridge_result) if case.bridge_result is not None else None
    callbacks: list[Callable[[], None]] = []
    scheduler = callbacks.append if case.drop_dispatch else lambda callback: callback()
    destination = {
        "focused": FOCUSED_DESTINATION,
        "clipboard": CLIPBOARD_DESTINATION,
        "t3": T3CodeDestination(T3_RECORDING_ID),
    }[case.destination]
    pipeline = DictationPipeline(
        transcriber=GoldenTranscriber(case.transcript, hook),
        postprocessor=provider,
        postprocess_router=GoldenRouter(provider, case.routing_mode, events),
        normalizer=GoldenNormalizer(hook),
        injector=injector,
        status=status,
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_timeout_seconds=0,
        injection_scheduler=scheduler,
        result_sink=sink,
        clock_ns=StepClock(),
        t3_bridge=bridge,
    )
    hook.pipeline = pipeline
    try:
        assert pipeline.try_reserve()
        sequence_id = pipeline.submit_reserved(
            np.ones(160, dtype=np.float32),
            destination=destination,
            app_bundle_id="com.example.golden",
        )
        wait_empty(pipeline)
    finally:
        pipeline.shutdown()

    assert sequence_id == 1
    assert len(sink.outcomes) == 1
    outcome = sink.outcomes[0]
    assert (outcome.delivery_status, outcome.error_code) == (
        expected.delivery,
        expected.error_code,
    )
    assert (
        outcome.dictation.sequence_id,
        outcome.dictation.raw_text,
        outcome.dictation.normalized_text,
        outcome.dictation.final_text,
        outcome.dictation.app_bundle_id,
    ) == (
        1,
        expected.raw_text,
        expected.normalized_text,
        expected.final_text,
        "com.example.golden",
    )
    assert outcome.dictation.transcription.text == expected.raw_text
    assert outcome.dictation.transcription.audio_duration_seconds == 0.01
    assert outcome.dictation.transcription.inference_seconds == (
        1e-8 if expected.trace["stt_ended_ns"] is not None else 0
    )
    assert [segment.text for segment in outcome.dictation.transcription.segments] == (
        [expected.raw_text] if expected.raw_text else []
    )
    assert trace_snapshot(outcome) == expected.trace
    assert tuple(events) == expected.order
    assert tuple(injector.injected) == expected.injected
    assert tuple(injector.clipboard_fallbacks) == expected.clipboard_fallbacks
    assert tuple(bridge.deliveries if bridge is not None else ()) == expected.bridge_deliveries
    assert tuple(status.messages) == expected.messages
    assert (
        tuple((completion["fallback"], completion["detail"]) for completion in status.completions)
        == expected.completions
    )
    if case.destination == "clipboard":
        assert injector.calls == [(expected.final_text, CLIPBOARD_DESTINATION)]
    if case.injection_failure == "permission":
        assert {"system": SystemState.ERROR, "system_message": "accessibility denied"} in (
            status.updates
        )
    if case.drop_dispatch:
        assert len(callbacks) == 1
        callbacks[0]()
        assert injector.calls == []


class BatchTranscriber:
    def __init__(self, first_result: str | BaseException) -> None:
        self.first_result = first_result
        self.entered = threading.Event()
        self.release = threading.Event()
        self.calls = 0

    def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
        assert sample_rate == 16000
        self.calls += 1
        if self.calls == 1:
            self.entered.set()
            assert self.release.wait(timeout=1)
            if isinstance(self.first_result, BaseException):
                raise self.first_result
            return self.first_result
        return f"second-{int(audio[0])}"


def run_two_job_batch(first_result: str | BaseException) -> tuple[list[PipelineOutcome], list[str]]:
    transcriber = BatchTranscriber(first_result)
    events: list[str] = []
    hook = Hook(events, None)
    provider = GoldenProvider(hook, "success")
    injector = GoldenInjector(hook, None)
    sink = GoldenSink(events)
    pipeline = DictationPipeline(
        transcriber=transcriber,
        postprocessor=provider,
        postprocess_router=GoldenRouter(provider, "rules_only", events),
        normalizer=GoldenNormalizer(hook),
        injector=injector,
        status=GoldenStatus(hook),
        max_pending=2,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
        clock_ns=StepClock(),
    )
    hook.pipeline = pipeline
    try:
        assert pipeline.try_reserve()
        assert pipeline.submit_reserved(np.ones(1), destination=FOCUSED_DESTINATION) == 1
        assert transcriber.entered.wait(timeout=1)
        assert pipeline.try_reserve()
        assert pipeline.submit_reserved(np.full(1, 2), destination=FOCUSED_DESTINATION) == 2
        transcriber.release.set()
        wait_empty(pipeline)
    finally:
        transcriber.release.set()
        pipeline.shutdown()
    return sink.outcomes, injector.injected


def test_golden_fifo_ordering_with_inverted_completion_pressure() -> None:
    outcomes, injected = run_two_job_batch("first")

    assert [outcome.dictation.sequence_id for outcome in outcomes] == [1, 2]
    assert [outcome.dictation.raw_text for outcome in outcomes] == ["first", "second-2"]
    assert injected == ["first", "second-2"]
    assert [trace_snapshot(outcome) for outcome in outcomes] == [
        {
            **FULL_RULES_TRACE,
            "stt_ended_ns": 50,
            "normalization_started_ns": 60,
            "normalization_ended_ns": 70,
            "postprocess_started_ns": 80,
            "postprocess_ended_ns": 90,
            "injection_started_ns": 100,
            "injection_ended_ns": 110,
            "completed_ns": 120,
        },
        {
            **FULL_RULES_TRACE,
            "queued_ns": 40,
            "dequeued_ns": 130,
            "stt_started_ns": 140,
            "stt_ended_ns": 150,
            "normalization_started_ns": 160,
            "normalization_ended_ns": 170,
            "postprocess_started_ns": 180,
            "postprocess_ended_ns": 190,
            "injection_started_ns": 200,
            "injection_ended_ns": 210,
            "completed_ns": 220,
        },
    ]


def test_golden_one_job_failure_does_not_block_later_jobs() -> None:
    outcomes, injected = run_two_job_batch(RuntimeError("bad audio"))

    assert [outcome.dictation.sequence_id for outcome in outcomes] == [1, 2]
    assert [(outcome.delivery_status, outcome.error_code) for outcome in outcomes] == [
        (DeliveryStatus.FAILED, "pipeline_error"),
        (DeliveryStatus.INJECTED, None),
    ]
    assert injected == ["second-2"]
    assert trace_snapshot(outcomes[0]) == trace(
        completed=50,
        delivery=DeliveryStatus.FAILED,
        stt_end=None,
        normalization=(None, None),
        postprocess=(None, None),
        injection=(None, None),
        route=None,
        reason=None,
        failed_stage="transcription",
    )
    assert trace_snapshot(outcomes[1]) == {
        **FULL_RULES_TRACE,
        "queued_ns": 40,
        "dequeued_ns": 60,
        "stt_started_ns": 70,
        "stt_ended_ns": 80,
        "normalization_started_ns": 90,
        "normalization_ended_ns": 100,
        "postprocess_started_ns": 110,
        "postprocess_ended_ns": 120,
        "injection_started_ns": 130,
        "injection_ended_ns": 140,
        "completed_ns": 150,
    }
