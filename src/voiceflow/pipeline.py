from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from voiceflow.postprocess.base import PostProcessor
from voiceflow.state import ActivityState, PostprocessState

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioJob:
    sequence_id: int
    audio: np.ndarray


class DictationPipeline:
    def __init__(
        self,
        *,
        transcriber: Any,
        postprocessor: PostProcessor,
        injector: Any,
        status: Any,
        max_pending: int,
        shutdown_timeout_seconds: float,
        injection_scheduler: Callable[[Callable[[], None]], None] | None = None,
    ) -> None:
        if injection_scheduler is None:
            from PyObjCTools import AppHelper

            injection_scheduler = AppHelper.callAfter
        self.transcriber = transcriber
        self.postprocessor = postprocessor
        self.injector = injector
        self.status = status
        self.max_pending = max_pending
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self._schedule_injection = injection_scheduler
        self._slots = threading.BoundedSemaphore(max_pending)
        self._queue: queue.Queue[AudioJob | None] = queue.Queue()
        self._lock = threading.RLock()
        self._accepting = True
        self._sequence = 0
        self._pending = 0
        self._worker = threading.Thread(target=self._run, name="voiceflow-pipeline", daemon=True)
        self._worker.start()

    @property
    def pending(self) -> int:
        with self._lock:
            return self._pending

    def try_reserve(self) -> bool:
        with self._lock:
            if not self._accepting:
                return False
            acquired = self._slots.acquire(blocking=False)
            if acquired:
                self._pending += 1
                self.status.update(queue_depth=self._pending)
            return acquired

    def cancel_reservation(self) -> None:
        with self._lock:
            if self._pending <= 0:
                return
            self._pending -= 1
            self._slots.release()
            self.status.update(queue_depth=self._pending)

    def submit_reserved(self, audio: np.ndarray) -> int:
        with self._lock:
            if not self._accepting:
                self.cancel_reservation()
                raise RuntimeError("Pipeline is shutting down")
            self._sequence += 1
            sequence_id = self._sequence
            pending = self._pending
        self._queue.put(AudioJob(sequence_id, np.asarray(audio, dtype=np.float32)))
        self.status.update(activity=ActivityState.QUEUED, queue_depth=pending)
        return sequence_id

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                self._process(job)
            except Exception:
                LOGGER.exception("Dictation job %s failed", job.sequence_id)
                self.status.transient("Elaborazione non riuscita", error=True)
            finally:
                self._finish_job()
                self._queue.task_done()

    def _process(self, job: AudioJob) -> None:
        LOGGER.info("Processing dictation job %s", job.sequence_id)
        self.status.update(activity=ActivityState.TRANSCRIBING)
        raw_text = self.transcriber.transcribe(job.audio)
        if not raw_text:
            return
        self.status.update(
            activity=ActivityState.POSTPROCESSING,
            postprocess=PostprocessState.RUNNING,
        )
        result = self.postprocessor.process(raw_text)
        if result.used_fallback:
            self.status.update(postprocess=PostprocessState.DEGRADED)
            self.status.transient("Codex non disponibile — usato testo originale")
        else:
            self.status.update(postprocess=PostprocessState.READY)
        self.status.update(activity=ActivityState.INJECTING)
        self._inject_on_main_thread(result.text)
        if not result.used_fallback:
            self.status.complete()

    def _inject_on_main_thread(self, text: str) -> None:
        completed = threading.Event()
        error: list[BaseException] = []

        def inject() -> None:
            try:
                self.injector.inject(text)
            except BaseException as exc:
                error.append(exc)
            finally:
                completed.set()

        self._schedule_injection(inject)
        completed.wait()
        if error:
            raise error[0]

    def _finish_job(self) -> None:
        with self._lock:
            self._pending -= 1
            pending = self._pending
            self._slots.release()
        self.status.update(activity=ActivityState.IDLE, queue_depth=pending)

    def shutdown(self) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
        self.status.update(activity=ActivityState.STOPPING)
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            if job is not None:
                with self._lock:
                    self._pending -= 1
                    self._slots.release()
            self._queue.task_done()
        self.postprocessor.close()
        self._queue.put(None)
        self._worker.join(timeout=self.shutdown_timeout_seconds)
