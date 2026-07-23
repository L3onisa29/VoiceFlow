from __future__ import annotations

import threading
import time
from typing import Any

import numpy as np

from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess.base import PostprocessResult
from voiceflow.system.text_injector import FocusTarget, InjectionDestination

DESTINATION = InjectionDestination.focused(FocusTarget(1, "test-field"))


class Status:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.outcomes: list[dict[str, Any]] = []

    def update(self, **changes: Any) -> None:
        self.updates.append(changes)

    def transient(self, text: str, **_kwargs: Any) -> None:
        self.messages.append(text)

    def complete(self, **kwargs: Any) -> None:
        self.messages.append("Completato")
        self.outcomes.append(kwargs)


class Postprocessor:
    name = "fake"

    def preflight(self) -> bool:
        return True

    def process(self, text: str) -> PostprocessResult:
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
            injection_destination=DESTINATION,
        )
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.array([0.0], dtype=np.float32),
            injection_destination=DESTINATION,
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
                injection_destination=DESTINATION,
            )
        wait_empty(pipeline)
        assert injector.values == ["clean-second"]
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
            injection_destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == []
        assert pipeline.status.messages == ["Nessuna voce riconosciuta"]
    finally:
        pipeline.shutdown()


def test_pipeline_leaves_postprocess_state_to_provider() -> None:
    pipeline, injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})()
    )
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            injection_destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == ["clean-raw text"]
        assert all("postprocess" not in update for update in pipeline.status.updates)
        assert pipeline.status.outcomes == [{"fallback": False, "detail": None}]
    finally:
        pipeline.shutdown()


def test_clipboard_only_destination_is_an_expected_completion() -> None:
    pipeline, injector = make_pipeline(
        type("T", (), {"transcribe": lambda *_args, **_kwargs: "raw text"})()
    )
    destination = InjectionDestination.clipboard_only()
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(
            np.ones(1, dtype=np.float32),
            injection_destination=destination,
        )
        wait_empty(pipeline)
        assert injector.values == ["clean-raw text"]
        assert injector.destinations == [destination]
        assert pipeline.status.outcomes == [
            {
                "fallback": False,
                "detail": "Nessuna destinazione — testo lasciato negli appunti",
            }
        ]
    finally:
        pipeline.shutdown()


def test_successful_fallback_is_reported_only_after_original_text_is_inserted() -> None:
    class FallbackPostprocessor(Postprocessor):
        def process(self, text: str) -> PostprocessResult:
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
            injection_destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injector.values == ["original"]
        assert status.outcomes == [
            {
                "fallback": True,
                "detail": "fake non disponibile — usato testo originale",
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
            injection_destination=DESTINATION,
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
        injection_destination=DESTINATION,
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
            injection_destination=DESTINATION,
        )
        wait_empty(pipeline)
        assert injected.is_set()
        assert injector.values == ["clean-text"]
        assert "Elaborazione non riuscita" not in pipeline.status.messages
    finally:
        pipeline.shutdown()
