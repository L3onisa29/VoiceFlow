from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import numpy as np

from voiceflow.config import FormattingConfig
from voiceflow.diagnostics import exception_fields, log_event
from voiceflow.integrations import (
    DictationDestination,
    T3CodeBridge,
    T3CodeDestination,
)
from voiceflow.normalization import DeterministicNormalizer
from voiceflow.pipeline_stages import (
    DeliveryStage,
    NormalizationStage,
    PostprocessStage,
    TranscriptionStage,
)
from voiceflow.postprocess.base import PostProcessor, PostprocessResult
from voiceflow.postprocess.router import PostprocessRouter
from voiceflow.processing import (
    DeliveryStatus,
    NullPipelineResultSink,
    PipelineOutcome,
    PipelineResultSink,
    PipelineTrace,
    ProcessedDictation,
    ProcessingIntent,
)
from voiceflow.state import ActivityState, SystemState
from voiceflow.system.text_injector import FocusChangedError
from voiceflow.transcription import TranscriptionResult

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AudioJob:
    sequence_id: int
    audio: np.ndarray
    sample_rate: int
    destination: DictationDestination
    processing_intent: ProcessingIntent
    trace: PipelineTrace
    app_bundle_id: str | None = None


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
        result_sink: PipelineResultSink | None = None,
        clock_ns: Callable[[], int] = time.perf_counter_ns,
        normalizer: DeterministicNormalizer | None = None,
        postprocess_router: PostprocessRouter | None = None,
        formatting: FormattingConfig | None = None,
        preferred_terms: Callable[[str | None, str | None], tuple[str, ...]] | None = None,
        t3_bridge: T3CodeBridge | None = None,
    ) -> None:
        if injection_scheduler is None:
            from PyObjCTools import AppHelper

            injection_scheduler = AppHelper.callAfter
        self.transcriber = transcriber
        self.transcription_stage = TranscriptionStage(transcriber, clock_ns=clock_ns)
        self.postprocessor = postprocessor
        self.injector = injector
        self.status = status
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.injection_timeout_seconds = injection_timeout_seconds
        self.result_sink = result_sink or NullPipelineResultSink()
        self._clock_ns = clock_ns
        self.normalizer = normalizer or DeterministicNormalizer()
        self.postprocess_router = postprocess_router
        self.formatting = formatting or FormattingConfig()
        self.normalization_stage = NormalizationStage(
            self.normalizer,
            self.formatting,
            clock_ns=clock_ns,
        )
        self._preferred_terms = preferred_terms or (lambda _language, _app: ())
        self.postprocess_stage = PostprocessStage(
            self.postprocessor,
            postprocess_router=self.postprocess_router,
            preferred_terms=self._preferred_terms,
            on_provider_route=lambda: self.status.update(activity=ActivityState.POSTPROCESSING),
            clock_ns=clock_ns,
        )
        self.t3_bridge = t3_bridge
        self._schedule_injection = injection_scheduler
        self._slots = threading.BoundedSemaphore(max_pending)
        self._queue: queue.Queue[AudioJob | None] = queue.Queue()
        self._lock = threading.RLock()
        self._accepting = True
        self._cancelled = threading.Event()
        self._publish_stopping = True
        self._sequence = 0
        self._pending = 0
        self._next_processing_intent = ProcessingIntent.AUTO
        self.delivery_stage = DeliveryStage(
            self.injector,
            self.status,
            injection_scheduler=self._schedule_injection,
            injection_timeout_seconds=self.injection_timeout_seconds,
            t3_bridge=self.t3_bridge,
            is_cancelled=self._cancelled.is_set,
            raise_if_cancelled=self._raise_if_cancelled,
            clock_ns=clock_ns,
        )
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
        destination: DictationDestination,
        sample_rate: int = 16000,
        processing_intent: ProcessingIntent | None = None,
        app_bundle_id: str | None = None,
    ) -> int:
        with self._lock:
            if not self._accepting:
                self.cancel_reservation()
                raise RuntimeError("Pipeline is shutting down")
            self._sequence += 1
            sequence_id = self._sequence
            pending = self._pending
            intent = processing_intent or self._next_processing_intent
            self._next_processing_intent = ProcessingIntent.AUTO
        self._queue.put(
            AudioJob(
                sequence_id,
                np.asarray(audio, dtype=np.float32),
                sample_rate,
                destination,
                intent,
                PipelineTrace(queued_ns=self._clock_ns()),
                app_bundle_id,
            )
        )
        self.status.update(activity=ActivityState.QUEUED, queue_depth=pending)
        log_event(
            LOGGER,
            logging.DEBUG,
            "dictation.queued",
            "Dictation queued",
            context=_destination_context(destination, app_bundle_id, self.injector),
            job_id=sequence_id,
            queue_depth=pending,
            intent=intent,
            audio_seconds=len(audio) / sample_rate,
        )
        return sequence_id

    def request_next_processing_intent(self, intent: ProcessingIntent) -> None:
        with self._lock:
            self._next_processing_intent = intent

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                self._process(job)
            except PipelineCancelled:
                log_event(
                    LOGGER,
                    logging.DEBUG,
                    "dictation.cancelled",
                    "Dictation cancelled",
                    context=self._job_context(job),
                )
            except FocusChangedError as exc:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "delivery.focus_changed",
                    "Focused input changed; dictation kept on clipboard",
                    context=self._job_context(job),
                    **exception_fields(exc),
                )
                self.status.transient(
                    "Destinazione cambiata — testo lasciato negli appunti",
                    error=True,
                )
            except PermissionError as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "delivery.permission_denied",
                    "Text delivery lost a required permission",
                    context=self._job_context(job),
                    **exception_fields(exc),
                )
                self.status.update(system=SystemState.ERROR, system_message=str(exc))
                self.status.transient(str(exc), error=True)
            except Exception:
                # _process logs the failed stage and traceback before propagating.
                pass
            finally:
                self._finish_job()
                self._queue.task_done()

    def _process(self, job: AudioJob) -> None:
        trace = job.trace
        trace.dequeued_ns = self._clock_ns()
        transcription = TranscriptionResult.empty(
            audio_duration_seconds=len(job.audio) / job.sample_rate
        )
        raw_text = normalized_text = final_text = ""
        delivery = DeliveryStatus.NOT_ATTEMPTED
        error_code: str | None = None
        stage = "startup"
        try:
            self._raise_if_cancelled()
            log_event(
                LOGGER,
                logging.DEBUG,
                "dictation.started",
                "Processing dictation",
                context=self._job_context(job),
                queue_seconds=trace.queue_seconds,
            )
            self.status.update(activity=ActivityState.TRANSCRIBING)
            stage = "transcription"
            transcription_result = self.transcription_stage.run(
                job.audio,
                sample_rate=job.sample_rate,
                trace=trace,
            )
            transcription = transcription_result.transcription
            raw_text = transcription_result.raw_text
            self._raise_if_cancelled()
            if not raw_text:
                delivery = DeliveryStatus.EMPTY
                error_code = "empty_transcript"
                self._abandon_t3(job)
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "transcription.empty",
                    "No speech was recognized",
                    context=self._job_context(job),
                    stt_seconds=trace.stt_seconds,
                )
                self.status.transient("Nessuna voce riconosciuta", error=True)
                return

            stage = "normalization"
            normalization_result = self.normalization_stage.run(
                raw_text,
                language=transcription.language,
                app_bundle_id=job.app_bundle_id,
                trace=trace,
            )
            normalization = normalization_result.normalization
            normalized_text = normalization_result.normalized_text
            stage = "postprocess"
            postprocess_result = self.postprocess_stage.run(
                normalization,
                raw_text=raw_text,
                transcription=transcription,
                processing_intent=job.processing_intent,
                app_bundle_id=job.app_bundle_id,
                trace=trace,
            )
            result = postprocess_result.postprocess
            self._raise_if_cancelled()
            self._record_postprocess_completion(job, result, trace)
            final_text = postprocess_result.final_text
            self.status.update(activity=ActivityState.INJECTING)
            self._raise_if_cancelled()
            stage = "delivery"
            delivery_result = self.delivery_stage.run(
                final_text,
                destination=job.destination,
                used_fallback=result.used_fallback,
                fallback_provider=self.postprocessor.name,
                trace=trace,
                log_context=self._job_context(job),
            )
            delivery = delivery_result.delivery_status
            error_code = delivery_result.error_code
        except PipelineCancelled:
            delivery = DeliveryStatus.CANCELLED
            error_code = "cancelled"
            trace.failed_stage = stage
            self._abandon_t3(job)
            raise
        except FocusChangedError:
            delivery = DeliveryStatus.FOCUS_CHANGED
            error_code = "focus_changed"
            trace.failed_stage = stage
            raise
        except Exception as exc:
            delivery = DeliveryStatus.FAILED
            error_code = _error_code(exc)
            trace.failed_stage = stage
            self._abandon_t3(job)
            log_event(
                LOGGER,
                logging.ERROR,
                "dictation.failed",
                "Dictation processing failed",
                context=self._job_context(job),
                stage=stage,
                error_code=error_code,
                exc_info=True,
                **exception_fields(exc),
            )
            if not isinstance(exc, PermissionError) and not self._cancelled.is_set():
                self.status.transient(_failure_message(stage, exc), error=True)
            raise
        finally:
            trace.delivery_status = delivery
            trace.completed_ns = self._clock_ns()
            outcome = PipelineOutcome(
                dictation=ProcessedDictation(
                    sequence_id=job.sequence_id,
                    raw_text=raw_text,
                    normalized_text=normalized_text,
                    final_text=final_text,
                    transcription=transcription,
                    trace=trace,
                    processing_intent=job.processing_intent,
                    app_bundle_id=job.app_bundle_id,
                ),
                delivery_status=delivery,
                error_code=error_code,
            )
            try:
                self.result_sink.consume(outcome)
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "history.write_failed",
                    "Pipeline result could not be stored",
                    context=self._job_context(job),
                    exc_info=True,
                    **exception_fields(exc),
                )
            log_event(
                LOGGER,
                logging.DEBUG if error_code is None else logging.WARNING,
                "dictation.completed",
                "Dictation finished",
                context=self._job_context(job),
                delivery_status=delivery,
                error_code=error_code,
                failed_stage=trace.failed_stage,
                route=trace.route,
                fallback_code=trace.fallback_code,
                raw_characters=len(raw_text),
                final_characters=len(final_text),
                stt_seconds=trace.stt_seconds,
                postprocess_seconds=trace.postprocess_seconds,
                delivery_seconds=trace.delivery_seconds,
                total_seconds=trace.total_seconds,
            )

    def _record_postprocess_completion(
        self,
        job: AudioJob,
        result: PostprocessResult,
        trace: PipelineTrace,
    ) -> None:
        if result.used_fallback:
            trace.fallback_code = result.fallback_code or "provider_error"
        log_event(
            LOGGER,
            logging.DEBUG,
            "postprocess.completed",
            "Post-processing route completed",
            context=self._job_context(job),
            provider=self.postprocessor.name,
            route=trace.route,
            route_reason=trace.reason,
            fallback=result.used_fallback,
            fallback_code=trace.fallback_code,
            postprocess_seconds=trace.postprocess_seconds,
        )
        if result.used_fallback:
            log_event(
                LOGGER,
                logging.WARNING,
                "postprocess.fallback",
                "Post-processor unavailable; normalized text will be used",
                context=self._job_context(job),
                provider=self.postprocessor.name,
                fallback_code=trace.fallback_code,
                provider_error=result.error,
            )

    def _abandon_t3(self, job: AudioJob) -> None:
        if isinstance(job.destination, T3CodeDestination) and self.t3_bridge is not None:
            self.t3_bridge.abandon_recording(job.destination.recording_id)

    def _raise_if_cancelled(self) -> None:
        if self._cancelled.is_set():
            raise PipelineCancelled("Pipeline is shutting down")

    def _job_context(self, job: AudioJob) -> dict[str, object]:
        return {
            "job_id": job.sequence_id,
            **_destination_context(
                job.destination,
                job.app_bundle_id,
                self.injector,
            ),
        }

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
                self._abandon_t3(job)
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


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, TimeoutError):
        return "injection_timeout"
    if isinstance(exc, PermissionError):
        return "permission_denied"
    return "pipeline_error"


def _destination_context(
    destination: DictationDestination,
    app_bundle_id: str | None,
    injector: Any,
) -> dict[str, object]:
    if isinstance(destination, T3CodeDestination):
        return {
            "host_platform": "macos",
            "target_platform": "t3_code",
            "target_app": "com.t3tools.t3code",
            "operation": "integration",
            "delivery_channel": "t3_bridge",
        }
    if destination.uses_clipboard_only:
        return {
            "host_platform": "macos",
            "target_platform": "clipboard",
            "target_app": app_bundle_id or "unknown",
            "operation": "clipboard_write",
            "delivery_channel": "clipboard",
        }
    method = getattr(injector, "method", "paste")
    return {
        "host_platform": "macos",
        "target_platform": "focused_input",
        "target_app": app_bundle_id or "unknown",
        "operation": "input_write",
        "delivery_channel": f"accessibility_{method}",
    }


def _failure_message(stage: str, exc: BaseException) -> str:
    labels = {
        "startup": "Avvio elaborazione",
        "transcription": "Trascrizione",
        "normalization": "Normalizzazione",
        "postprocess": "Post-processing",
        "delivery": "Scrittura del testo",
    }
    detail = str(exc).replace("\r", " ").replace("\n", " ").strip()
    if len(detail) > 120:
        detail = detail[:119] + "…"
    reason = f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
    return f"{labels.get(stage, 'Elaborazione')} non riuscita — {reason}"
