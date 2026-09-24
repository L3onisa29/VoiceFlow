from __future__ import annotations

import logging
import threading
import time
from typing import Any

import numpy as np
import pytest

from voiceflow.config import RoutingConfig
from voiceflow.integrations import BridgeResult, T3CodeDestination
from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess.base import PostprocessRequest, PostprocessResult
from voiceflow.postprocess.router import PostprocessRouter, RoutingDecision
from voiceflow.processing import (
    DeliveryStatus,
    PipelineOutcome,
    PostprocessRoute,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.state import ActivityState
from voiceflow.system.text_injector import FocusTarget, InjectionDestination

DESTINATION = InjectionDestination.focused(FocusTarget(1, "test-field"))


class Status:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.outcomes: list[dict[str, Any]] = []
        self.copied_outcomes: list[dict[str, Any]] = []

    def update(self, **changes: Any) -> None:
        self.updates.append(changes)

    def transient(self, text: str, **_kwargs: Any) -> None:
        self.messages.append(text)

    def complete(self, **kwargs: Any) -> None:
        self.messages.append("Completato")
        self.outcomes.append(kwargs)

    def copied(self, **kwargs: Any) -> None:
        self.messages.append("Copiato")
        self.copied_outcomes.append(kwargs)


class Postprocessor:
    name = "fake"

    def preflight(self) -> bool:
        return True

    def process(self, request: PostprocessRequest | str) -> PostprocessResult:
        text = request.text if isinstance(request, PostprocessRequest) else request
        return PostprocessResult(f"clean-{text}")

    def retry(self) -> bool:
        return True

    def close(self) -> None:
        pass


class Injector:
    def __init__(self) -> None:
        self.values: list[str] = []
        self.destinations: list[InjectionDestination] = []

    def inject(self, text: str, *, destination: InjectionDestination) -> None:
        self.values.append(text)
        self.destinations.append(destination)


class CapturingSink:
    def __init__(self) -> None:
        self.outcomes: list[PipelineOutcome] = []

    def consume(self, outcome: PipelineOutcome) -> None:
        self.outcomes.append(outcome)


class Bridge:
    def __init__(self) -> None:
        self.deliveries: list[tuple[str, str]] = []
        self.abandoned: list[str] = []
        self.results: dict[str, BridgeResult] = {}

    def deliver_transcription(
        self,
        recording_id: str,
        transcript: str,
    ) -> BridgeResult:
        self.deliveries.append((recording_id, transcript))
        return self.results.get(recording_id, BridgeResult.accepted_result())

    def abandon_recording(self, recording_id: str) -> None:
        self.abandoned.append(recording_id)


def wait_empty(pipeline: DictationPipeline, timeout: float = 2) -> None:
    deadline = time.monotonic() + timeout
    while pipeline.pending and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pipeline.pending == 0


def make_pipeline(transcriber: Any, max_pending: int = 4) -> tuple[DictationPipeline, Injector]:
    injector = Injector()
    pipeline = DictationPipeline(
        transcriber=transcriber,
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=max_pending,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
    )
    return pipeline, injector


def test_fifo_even_when_inputs_have_inverted_artificial_latency() -> None:
    class Transcriber:
        def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
            assert sample_rate == 16000
            delay = float(audio[0])
            time.sleep(delay)
            return str(delay)

    pipeline, injector = make_pipeline(Transcriber())
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.array([0.04], dtype=np.float32),
            destination=DESTINATION,
        )
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.array([0.0], dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == ["clean-0.03999999910593033", "clean-0.0"]
    finally:
        pipeline.shutdown()


def test_backpressure_reserves_before_recording() -> None:
    pipeline, _injector = make_pipeline(type("T", (), {"transcribe": lambda *_: "ok"})(), 1)
    try:
        assert pipeline.try_reserve()
        assert not pipeline.try_reserve()
        pipeline.cancel_reservation()
        assert pipeline.try_reserve()
        pipeline.cancel_reservation()
    finally:
        pipeline.shutdown()


def test_worker_recovers_after_stt_error() -> None:
    class Transcriber:
        calls = 0

        def transcribe(self, _audio: np.ndarray, *, sample_rate: int) -> str:
            assert sample_rate == 16000
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("bad audio")
            return "second"

    pipeline, injector = make_pipeline(Transcriber())
    try:
        for _ in range(2):
            assert pipeline.try_reserve()
            pipeline.submit_reserved(
                np.ones(1, dtype=np.float32),
                destination=DESTINATION,
            )
        wait_empty(pipeline)
        assert injector.values == ["clean-second"]
    finally:
        pipeline.shutdown()


def test_pipeline_owns_and_zeroizes_audio_after_processing() -> None:
    observed: list[np.ndarray] = []

    class Transcriber:
        def transcribe(self, audio: np.ndarray, *, sample_rate: int) -> str:
            assert sample_rate == 16000
            observed.append(audio)
            return "done"

    source = np.array([0.25, -0.5], dtype=np.float32)
    pipeline, _injector = make_pipeline(Transcriber())
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(source, destination=DESTINATION)
        wait_empty(pipeline)

        assert source.tolist() == [0.25, -0.5]
        assert len(observed) == 1
        assert not np.shares_memory(source, observed[0])
        assert observed[0].tolist() == [0, 0]
    finally:
        pipeline.shutdown()


def test_empty_transcript_is_reported() -> None:
    pipeline, injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: ""})()
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == []
        assert pipeline.status.messages == ["Nessuna voce riconosciuta"]
    finally:
        pipeline.shutdown()


def test_logs_include_delivery_context_without_transcript(
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript = "private dictation sentinel"
    pipeline, _injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: transcript})()
    )
    caplog.set_level(logging.DEBUG, logger="voiceflow.pipeline")
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
            app_bundle_id="com.example.editor",
        )
        wait_empty(pipeline)
    finally:
        pipeline.shutdown()

    assert "event=dictation.completed" in caplog.text
    assert 'target_platform="focused_input"' in caplog.text
    assert 'target_app="com.example.editor"' in caplog.text
    assert 'operation="input_write"' in caplog.text
    assert 'delivery_status="injected"' in caplog.text
    assert "delivery_seconds=" in caplog.text
    assert transcript not in caplog.text


def test_pipeline_leaves_postprocess_state_to_provider() -> None:
    pipeline, injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})()
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == ["clean-raw text"]
        assert all("postprocess" not in update for update in pipeline.status.updates)
        assert pipeline.status.outcomes == [{"fallback": False, "detail": None}]
    finally:
        pipeline.shutdown()


def test_pipeline_executes_the_single_precomputed_router_decision() -> None:
    entered = threading.Event()
    release = threading.Event()
    preferred_term_lookups: list[tuple[str | None, str | None]] = []

    class BlockingPostprocessor(Postprocessor):
        def __init__(self) -> None:
            self.preferred_terms: list[tuple[str, ...]] = []

        def process(self, request: PostprocessRequest | str) -> PostprocessResult:
            assert isinstance(request, PostprocessRequest)
            self.preferred_terms.append(request.preferred_terms)
            entered.set()
            release.wait(timeout=1)
            return super().process(request)

    class NonStableRouter(PostprocessRouter):
        def __init__(self, provider: Postprocessor) -> None:
            super().__init__(provider, RoutingConfig())
            self.decide_calls = 0

        def decide(self, request: PostprocessRequest) -> RoutingDecision:
            del request
            self.decide_calls += 1
            if self.decide_calls == 1:
                return RoutingDecision(PostprocessRoute.PROVIDER, RouteReason.DEEP_REQUESTED)
            return RoutingDecision(PostprocessRoute.RULES_ONLY, RouteReason.CLEAN_FAST_PATH)

    postprocessor = BlockingPostprocessor()
    router = NonStableRouter(postprocessor)
    injector = Injector()
    status = Status()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})(),
        postprocessor=postprocessor,
        postprocess_router=router,
        injector=injector,
        status=status,
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        preferred_terms=lambda language, app_bundle_id: (
            preferred_term_lookups.append((language, app_bundle_id)) or ("VoiceBud",)
        ),
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
            app_bundle_id="com.example.editor",
        )
        assert entered.wait(timeout=1)
        assert router.decide_calls == 1
        assert preferred_term_lookups == [(None, "com.example.editor")]
        assert postprocessor.preferred_terms == [("VoiceBud",)]
        assert status.updates[-1] == {"activity": ActivityState.POSTPROCESSING}

        release.set()
        wait_empty(pipeline)
        assert injector.values == ["clean-raw text"]
        assert router.decide_calls == 1
        assert preferred_term_lookups == [(None, "com.example.editor")]
    finally:
        release.set()
        pipeline.shutdown()


@pytest.mark.parametrize(
    ("provider_name", "routing_mode", "expected_reason"),
    [
        ("fake", "rules_only", RouteReason.RULES_ONLY_MODE),
        ("none", "always", RouteReason.PROVIDER_DISABLED),
    ],
)
def test_rules_only_routes_never_publish_postprocessing(
    provider_name: str,
    routing_mode: str,
    expected_reason: RouteReason,
) -> None:
    preferred_term_lookups: list[tuple[str | None, str | None]] = []

    class RulesOnlyProvider(Postprocessor):
        def __init__(self) -> None:
            self.name = provider_name
            self.calls = 0

        def process(self, text: PostprocessRequest | str) -> PostprocessResult:
            self.calls += 1
            return super().process(text.text if isinstance(text, PostprocessRequest) else text)

    postprocessor = RulesOnlyProvider()
    status = Status()
    sink = CapturingSink()
    injector = Injector()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})(),
        postprocessor=postprocessor,
        postprocess_router=PostprocessRouter(
            postprocessor,
            RoutingConfig(mode=routing_mode),
        ),
        injector=injector,
        status=status,
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
        preferred_terms=lambda language, app_bundle_id: (
            preferred_term_lookups.append((language, app_bundle_id)) or ("unused",)
        ),
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
    finally:
        pipeline.shutdown()

    assert postprocessor.calls == 0
    assert preferred_term_lookups == []
    assert injector.values == ["raw text"]
    assert all(update.get("activity") != ActivityState.POSTPROCESSING for update in status.updates)
    assert sink.outcomes[0].dictation.trace.route == PostprocessRoute.RULES_ONLY
    assert sink.outcomes[0].dictation.trace.reason == expected_reason


@pytest.mark.parametrize(
    ("provider_name", "expected_lookups"),
    [
        ("fake", [(None, "com.example.editor")]),
        ("none", []),
    ],
)
def test_legacy_postprocess_only_loads_preferred_terms_for_provider(
    provider_name: str,
    expected_lookups: list[tuple[str | None, str | None]],
) -> None:
    class CapturingPostprocessor(Postprocessor):
        def __init__(self) -> None:
            self.name = provider_name
            self.requests: list[PostprocessRequest | str] = []

        def process(self, request: PostprocessRequest | str) -> PostprocessResult:
            self.requests.append(request)
            text = request.text if isinstance(request, PostprocessRequest) else request
            return PostprocessResult(text)

    lookups: list[tuple[str | None, str | None]] = []
    postprocessor = CapturingPostprocessor()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})(),
        postprocessor=postprocessor,
        injector=Injector(),
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        preferred_terms=lambda language, app_bundle_id: (
            lookups.append((language, app_bundle_id)) or ("VoiceBud",)
        ),
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
            app_bundle_id="com.example.editor",
        )
        wait_empty(pipeline)
    finally:
        pipeline.shutdown()

    assert lookups == expected_lookups
    assert len(postprocessor.requests) == 1
    request = postprocessor.requests[0]
    if provider_name == "fake":
        assert isinstance(request, PostprocessRequest)
        assert request.preferred_terms == ("VoiceBud",)
    else:
        assert request == "raw text"


def test_clipboard_only_destination_is_an_expected_completion() -> None:
    pipeline, injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})()
    )
    destination = InjectionDestination.clipboard_only()
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=destination,
        )
        wait_empty(pipeline)
        assert injector.values == ["clean-raw text"]
        assert injector.destinations == [destination]
        assert pipeline.status.outcomes == []
        assert pipeline.status.copied_outcomes == [
            {
                "detail": "Nessuna destinazione — testo copiato negli appunti",
            }
        ]
    finally:
        pipeline.shutdown()


def test_successful_fallback_is_reported_only_after_original_text_is_inserted() -> None:
    class FallbackPostprocessor(Postprocessor):
        def process(self, request: PostprocessRequest | str) -> PostprocessResult:
            text = request.text if isinstance(request, PostprocessRequest) else request
            return PostprocessResult(text, used_fallback=True, error="provider unavailable")

    injector = Injector()
    status = Status()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "original"})(),
        postprocessor=FallbackPostprocessor(),
        injector=injector,
        status=status,
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == ["original"]
        assert status.outcomes == [
            {
                "fallback": True,
                "detail": "fake non disponibile — usato testo normalizzato",
            }
        ]
        assert all("postprocess" not in update for update in status.updates)
    finally:
        pipeline.shutdown()


def test_injection_scheduler_timeout_does_not_hang_worker() -> None:
    callbacks: list[Any] = []
    injector = Injector()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_timeout_seconds=0.01,
        injection_scheduler=callbacks.append,
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == []
        assert callbacks
        callbacks[0]()
        assert injector.values == []
    finally:
        pipeline.shutdown()


def test_shutdown_cancels_active_job_before_postprocess_or_injection() -> None:
    entered = threading.Event()
    release = threading.Event()

    class Transcriber:
        def transcribe(self, *_args: Any, **_kwargs: Any) -> str:
            entered.set()
            release.wait(timeout=1)
            return "must not be inserted"

    pipeline, injector = make_pipeline(Transcriber())
    assert pipeline.try_reserve()
    pipeline.submit_reserved(
        np.ones(1, dtype=np.float32),
        destination=DESTINATION,
    )
    assert entered.wait(timeout=1)

    pipeline.shutdown()
    release.set()
    pipeline._worker.join(timeout=1)

    assert not pipeline._worker.is_alive()
    assert injector.values == []


def test_started_injection_is_allowed_to_complete_without_false_timeout() -> None:
    injected = threading.Event()

    class SlowInjector(Injector):
        def inject(self, text: str, *, destination: InjectionDestination) -> None:
            time.sleep(0.03)
            super().inject(text, destination=destination)
            injected.set()

    injector = SlowInjector()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_timeout_seconds=0.01,
        injection_scheduler=lambda callback: threading.Thread(target=callback).start(),
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injected.is_set()
        assert injector.values == ["clean-text"]
        assert "Elaborazione non riuscita" not in pipeline.status.messages
    finally:
        pipeline.shutdown()


def test_pipeline_trace_covers_success_empty_fallback_and_injection_failure() -> None:
    def run_case(
        text: str,
        *,
        postprocessor: Any | None = None,
        injector: Any | None = None,
    ) -> PipelineOutcome:
        sink = CapturingSink()
        pipeline = DictationPipeline(
            transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: text})(),
            postprocessor=postprocessor or Postprocessor(),
            injector=injector or Injector(),
            status=Status(),
            max_pending=1,
            shutdown_timeout_seconds=1,
            injection_scheduler=lambda callback: callback(),
            result_sink=sink,
        )
        try:
            assert pipeline.try_reserve()
            pipeline.submit_reserved(
                np.ones(160, dtype=np.float32),
                destination=DESTINATION,
            )
            wait_empty(pipeline)
            assert len(sink.outcomes) == 1
            return sink.outcomes[0]
        finally:
            pipeline.shutdown()

    success = run_case("raw")
    trace = success.dictation.trace
    assert success.delivery_status == DeliveryStatus.INJECTED
    assert trace.queued_ns <= trace.dequeued_ns <= trace.stt_started_ns  # type: ignore[operator]
    assert trace.stt_ended_ns is not None
    assert trace.normalization_ended_ns is not None
    assert trace.injection_ended_ns is not None
    assert trace.delivery_seconds is not None
    assert trace.completed_ns is not None

    empty = run_case("")
    assert empty.delivery_status == DeliveryStatus.EMPTY
    assert empty.error_code == "empty_transcript"
    assert empty.dictation.trace.delivery_seconds is None

    class Fallback(Postprocessor):
        def process(self, request: PostprocessRequest | str) -> PostprocessResult:
            text = request.text if isinstance(request, PostprocessRequest) else request
            return PostprocessResult(
                text,
                used_fallback=True,
                fallback_code="provider_unavailable",
            )

    fallback = run_case("normalized", postprocessor=Fallback())
    assert fallback.dictation.final_text == "normalized"
    assert fallback.dictation.trace.fallback_code == "provider_unavailable"

    class BrokenInjector(Injector):
        def inject(self, text: str, *, destination: InjectionDestination) -> None:
            raise RuntimeError("failed")

    failed = run_case("raw", injector=BrokenInjector())
    assert failed.delivery_status == DeliveryStatus.FAILED
    assert failed.error_code == "pipeline_error"
    assert failed.dictation.trace.delivery_seconds is not None


def test_processing_intent_is_one_shot_and_cancellation_is_traced() -> None:
    sink = CapturingSink()
    entered = threading.Event()
    release = threading.Event()

    class BlockingTranscriber:
        calls = 0

        def transcribe(self, *_args: Any, **_kwargs: Any) -> str:
            self.calls += 1
            if self.calls == 1:
                entered.set()
                release.wait(timeout=1)
            return "text"

    pipeline = DictationPipeline(
        transcriber=BlockingTranscriber(),
        postprocessor=Postprocessor(),
        injector=Injector(),
        status=Status(),
        max_pending=2,
        shutdown_timeout_seconds=0.05,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
    )
    assert pipeline.try_reserve()
    pipeline.request_next_processing_intent(ProcessingIntent.DEEP)
    pipeline.submit_reserved(np.ones(1), destination=DESTINATION)
    assert entered.wait(timeout=1)
    pipeline.shutdown()
    release.set()
    pipeline._worker.join(timeout=1)
    assert sink.outcomes[0].delivery_status == DeliveryStatus.CANCELLED
    assert sink.outcomes[0].dictation.processing_intent == ProcessingIntent.DEEP

    second_sink = CapturingSink()
    second_pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=Injector(),
        status=Status(),
        max_pending=2,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=second_sink,
    )
    try:
        second_pipeline.request_next_processing_intent(ProcessingIntent.DEEP)
        for _ in range(2):
            assert second_pipeline.try_reserve()
            second_pipeline.submit_reserved(np.ones(1), destination=DESTINATION)
        wait_empty(second_pipeline)
        assert [result.dictation.processing_intent for result in second_sink.outcomes] == [
            ProcessingIntent.DEEP,
            ProcessingIntent.AUTO,
        ]
    finally:
        second_pipeline.shutdown()


def test_result_sink_failure_does_not_stop_following_dictations() -> None:
    class BrokenSink:
        def consume(self, _outcome: PipelineOutcome) -> None:
            raise OSError("storage unavailable")

    injector = Injector()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=2,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=BrokenSink(),
    )
    try:
        for _ in range(2):
            assert pipeline.try_reserve()
            pipeline.submit_reserved(np.ones(1), destination=DESTINATION)
        wait_empty(pipeline)
        assert injector.values == ["clean-text", "clean-text"]
    finally:
        pipeline.shutdown()


def test_t3_delivery_uses_final_text_and_never_calls_injector() -> None:
    bridge = Bridge()
    injector = Injector()
    sink = CapturingSink()
    recording_id = "4bb0832c-8f9f-4bb5-92a0-a7bb367d240c"
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw"})(),
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
        t3_bridge=bridge,
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1),
            destination=T3CodeDestination(recording_id),
        )
        wait_empty(pipeline)
        assert bridge.deliveries == [(recording_id, "clean-raw")]
        assert injector.values == []
        assert sink.outcomes[0].delivery_status == DeliveryStatus.T3_DELIVERED
        assert sink.outcomes[0].dictation.final_text == "clean-raw"
    finally:
        pipeline.shutdown()


def test_multiple_t3_jobs_keep_ids_and_fail_independently() -> None:
    bridge = Bridge()
    rejected_id = "36c45265-0d77-4fa4-9687-f07a64c8bbc0"
    accepted_id = "ae22dd5b-a6de-464c-bc05-84a24e50cc55"
    bridge.results[rejected_id] = BridgeResult.rejected()
    sink = CapturingSink()
    injector = Injector()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=injector,
        status=Status(),
        max_pending=2,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
        t3_bridge=bridge,
    )
    try:
        for recording_id in (rejected_id, accepted_id):
            assert pipeline.try_reserve()
            pipeline.submit_reserved(
                np.ones(1),
                destination=T3CodeDestination(recording_id),
            )
        wait_empty(pipeline)
        assert [recording_id for recording_id, _text in bridge.deliveries] == [
            rejected_id,
            accepted_id,
        ]
        assert [outcome.delivery_status for outcome in sink.outcomes] == [
            DeliveryStatus.T3_REJECTED,
            DeliveryStatus.T3_DELIVERED,
        ]
        assert sink.outcomes[0].error_code == "t3_rejected"
        assert injector.values == []
    finally:
        pipeline.shutdown()


def test_ambiguous_t3_delivery_is_not_retried() -> None:
    bridge = Bridge()
    recording_id = "7f17c860-cd2e-4708-8dd0-18687798cd90"
    bridge.results[recording_id] = BridgeResult.ambiguous()
    sink = CapturingSink()
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: "text"})(),
        postprocessor=Postprocessor(),
        injector=Injector(),
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        result_sink=sink,
        t3_bridge=bridge,
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1),
            destination=T3CodeDestination(recording_id),
        )
        wait_empty(pipeline)
        assert bridge.deliveries == [(recording_id, "clean-text")]
        assert sink.outcomes[0].delivery_status == DeliveryStatus.T3_AMBIGUOUS
        assert sink.outcomes[0].error_code == "t3_ambiguous"
    finally:
        pipeline.shutdown()


def test_empty_and_cancelled_t3_jobs_abandon_reservations() -> None:
    bridge = Bridge()
    empty_id = "17f73d09-b22c-4b19-8963-fdf94d7312fc"
    pipeline = DictationPipeline(
        transcriber=type("T", (), {"transcribe": lambda *_args, **_kwargs: ""})(),
        postprocessor=Postprocessor(),
        injector=Injector(),
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=1,
        injection_scheduler=lambda callback: callback(),
        t3_bridge=bridge,
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1),
            destination=T3CodeDestination(empty_id),
        )
        wait_empty(pipeline)
        assert bridge.abandoned == [empty_id]
        assert bridge.deliveries == []
    finally:
        pipeline.shutdown()

    queued_bridge = Bridge()
    queued_id = "46180495-64b0-4d53-bb54-bda7fd0e1aef"
    entered = threading.Event()
    release = threading.Event()

    class BlockingTranscriber:
        def transcribe(self, *_args: Any, **_kwargs: Any) -> str:
            entered.set()
            release.wait(timeout=1)
            return "text"

    queued_pipeline = DictationPipeline(
        transcriber=BlockingTranscriber(),
        postprocessor=Postprocessor(),
        injector=Injector(),
        status=Status(),
        max_pending=1,
        shutdown_timeout_seconds=0.01,
        injection_scheduler=lambda callback: callback(),
        t3_bridge=queued_bridge,
    )
    assert queued_pipeline.try_reserve()
    queued_pipeline.submit_reserved(
        np.ones(1),
        destination=T3CodeDestination(queued_id),
    )
    assert entered.wait(timeout=1)
    queued_pipeline.shutdown()
    release.set()
    queued_pipeline._worker.join(timeout=1)
    assert queued_id in queued_bridge.abandoned
