from __future__ import annotations

import time
from typing import Any

import numpy as np

from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess.base import PostprocessResult


class Status:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.messages: list[str] = []

    def update(self, **changes: Any) -> None:
        self.updates.append(changes)

    def transient(self, text: str, **_kwargs: Any) -> None:
        self.messages.append(text)

    def complete(self) -> None:
        self.messages.append("Completato")


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

    def inject(self, text: str) -> None:
        self.values.append(text)


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
        def transcribe(self, audio: np.ndarray) -> str:
            delay = float(audio[0])
            time.sleep(delay)
            return str(delay)

    pipeline, injector = make_pipeline(Transcriber())
    try:
        assert pipeline.try_reserve()
        pipeline.submit_reserved(np.array([0.04], dtype=np.float32))
        assert pipeline.try_reserve()
        pipeline.submit_reserved(np.array([0.0], dtype=np.float32))
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

        def transcribe(self, _audio: np.ndarray) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("bad audio")
            return "second"

    pipeline, injector = make_pipeline(Transcriber())
    try:
        for _ in range(2):
            assert pipeline.try_reserve()
            pipeline.submit_reserved(np.ones(1, dtype=np.float32))
        wait_empty(pipeline)
        assert injector.values == ["clean-second"]
    finally:
        pipeline.shutdown()
