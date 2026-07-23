from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from voiceflow.postprocess.base import PostProcessor
from voiceflow.state import ActivityState, SystemState
from voiceflow.system.text_injector import FocusChangedError, InjectionDestination

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioJob:
    sequence_id: int
    audio: np.ndarray
    sample_rate: int
    injection_destination: InjectionDestination


class PipelineCancelled(RuntimeError):
    pass


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
        injection_timeout_seconds: float = 2,
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
        self.injection_timeout_seconds = injection_timeout_seconds
        self._schedule_injection = injection_scheduler
        self._slots = threading.BoundedSemaphore(max_pending)
        self._queue: queue.Queue[AudioJob | None] = queue.Queue()
        self._lock = threading.RLock()
        self._accepting = True
        self._cancelled = threading.Event()
        self._publish_stopping = True
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
            pending = self._pending
            self._slots.release()
        changes: dict[str, object] = {"queue_depth": pending}
        if pending == 0:
            changes["activity"] = (
                ActivityState.STOPPING
                if self._cancelled.is_set() and self._publish_stopping
                else ActivityState.IDLE
            )
        self.status.update(**changes)

    def submit_reserved(
        self,
        audio: np.ndarray,
        *,
        injection_destination: InjectionDestination,
        sample_rate: int = 16000,
    ) -> int:
        with self._lock:
            if not self._accepting:
                self.cancel_reservation()
                raise RuntimeError("Pipeline is shutting down")
            self._sequence += 1
            sequence_id = self._sequence
            pending = self._pending
        self._queue.put(
            AudioJob(
                sequence_id,
                np.asarray(audio, dtype=np.float32),
                sample_rate,
                injection_destination,
            )
        )
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
            except PipelineCancelled:
                LOGGER.info("Dictation job %s cancelled", job.sequence_id)
            except FocusChangedError as exc:
                LOGGER.info("Dictation job %s kept on clipboard: %s", job.sequence_id, exc)
                self.status.transient(
                    "Destinazione cambiata — testo lasciato negli appunti",
                    error=True,
                )
            except PermissionError as exc:
                LOGGER.error(
                    "Dictation job %s lost a required permission: %s",
                    job.sequence_id,
                    exc,
                )
                self.status.update(system=SystemState.ERROR, system_message=str(exc))
                self.status.transient(str(exc), error=True)
            except Exception:
                LOGGER.exception("Dictation job %s failed", job.sequence_id)
                if not self._cancelled.is_set():
                    self.status.transient("Elaborazione non riuscita", error=True)
            finally:
                self._finish_job()
                self._queue.task_done()

    def _process(self, job: AudioJob) -> None:
        self._raise_if_cancelled()
        LOGGER.info("Processing dictation job %s", job.sequence_id)
        self.status.update(activity=ActivityState.TRANSCRIBING)
        raw_text = self.transcriber.transcribe(job.audio, sample_rate=job.sample_rate)
        self._raise_if_cancelled()
        if not raw_text:
            self.status.transient("Nessuna voce riconosciuta", error=True)
            return
        if self.postprocessor.name == "none":
            result = self.postprocessor.process(raw_text)
        else:
            self.status.update(activity=ActivityState.POSTPROCESSING)
            result = self.postprocessor.process(raw_text)
            self._raise_if_cancelled()
            if result.used_fallback:
                LOGGER.warning(
                    "%s post-processing fallback for job %s: %s",
                    self.postprocessor.name,
                    job.sequence_id,
                    result.error or "unknown error",
                )
        self.status.update(activity=ActivityState.INJECTING)
        self._raise_if_cancelled()
        self._inject_on_main_thread(result.text, destination=job.injection_destination)
        self._raise_if_cancelled()
        completion_details: list[str] = []
        if job.injection_destination.uses_clipboard_only:
            completion_details.append("Nessuna destinazione — testo lasciato negli appunti")
        if result.used_fallback:
            completion_details.append(
                f"{self.postprocessor.name} non disponibile — usato testo originale"
            )
        self.status.complete(
            fallback=result.used_fallback,
            detail="; ".join(completion_details) or None,
        )

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise PipelineCancelled("Pipeline is shutting down")

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
                if callback_state != "pending" or self._cancelled.is_set():
                    callback_state = "cancelled"
                    completed.set()
                    return
                callback_state = "started"
                started.set()
            try:
                self.injector.inject(text, destination=destination)
            except BaseException as exc:
                error.append(exc)
            finally:
                completed.set()

        self._schedule_injection(inject)
        if not started.wait(timeout=self.injection_timeout_seconds):
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
                    f"{self.injection_timeout_seconds:g} seconds"
                )
        completed.wait()
        if error:
            raise error[0]

    def _finish_job(self) -> None:
        with self._lock:
            self._pending -= 1
            pending = self._pending
            self._slots.release()
        if self._cancelled.is_set() and self._publish_stopping:
            activity = ActivityState.STOPPING
        elif self._cancelled.is_set():
            self.status.update(queue_depth=pending)
            return
        else:
            activity = ActivityState.QUEUED if pending else ActivityState.IDLE
        self.status.update(activity=activity, queue_depth=pending)

    def shutdown(
        self,
        *,
        close_postprocessor: bool = True,
        publish_stopping: bool = True,
    ) -> None:
        with self._lock:
            if not self._accepting:
                return
            self._accepting = False
            self._publish_stopping = publish_stopping
            self._cancelled.set()
        if publish_stopping:
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
        self.status.update(queue_depth=self.pending)
        self._queue.put(None)
        if close_postprocessor:
            try:
                self.postprocessor.close()
            except Exception:
                LOGGER.exception("Post-processor shutdown failed")
        self._worker.join(timeout=self.shutdown_timeout_seconds)
        if self._worker.is_alive():
            LOGGER.warning(
                "Pipeline worker did not stop within %.1f seconds",
                self.shutdown_timeout_seconds,
            )
