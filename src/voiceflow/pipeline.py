from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

import numpy as np

from voiceflow.config import FormattingConfig
from voiceflow.diagnostics import exception_fields, log_event
from voiceflow.integrations import (
    BridgeOutcome,
    DictationDestination,
    T3CodeBridge,
    T3CodeDestination,
)
from voiceflow.normalization import DeterministicNormalizer, NormalizationRequest
from voiceflow.postprocess.base import PostProcessor, PostprocessRequest
from voiceflow.postprocess.router import PostprocessRouter
from voiceflow.processing import (
    DeliveryStatus,
    NullPipelineResultSink,
    PipelineOutcome,
    PipelineResultSink,
    PipelineTrace,
    PostprocessRoute,
    ProcessedDictation,
    ProcessingIntent,
    RouteReason,
)
from voiceflow.state import ActivityState, SystemState
from voiceflow.system.text_injector import FocusChangedError, InjectionDestination
from voiceflow.transcription import TranscriptionResult, TranscriptSegment

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
        self.postprocessor = postprocessor
        self.injector = injector
        self.status = status
        self.max_pending = max_pending
        self.shutdown_timeout_seconds = shutdown_timeout_seconds
        self.injection_timeout_seconds = injection_timeout_seconds
        self.result_sink = result_sink or NullPipelineResultSink()
        self._clock_ns = clock_ns
        self.normalizer = normalizer or DeterministicNormalizer()
        self.postprocess_router = postprocess_router
        self.formatting = formatting or FormattingConfig()
        self._preferred_terms = preferred_terms or (lambda _language, _app: ())
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
            trace.stt_started_ns = self._clock_ns()
            transcribed = self.transcriber.transcribe(job.audio, sample_rate=job.sample_rate)
            trace.stt_ended_ns = self._clock_ns()
            transcription = _coerce_transcription(
                transcribed,
                audio_duration_seconds=len(job.audio) / job.sample_rate,
                inference_seconds=trace.stt_seconds or 0,
            )
            raw_text = transcription.text
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
            trace.normalization_started_ns = self._clock_ns()
            normalization = self.normalizer.normalize(
                NormalizationRequest(
                    raw_text,
                    language=transcription.language,
                    app_bundle_id=job.app_bundle_id,
                    spoken_punctuation=self.formatting.spoken_punctuation,
                    explicit_lists=self.formatting.explicit_lists,
                    structured_tokens=self.formatting.structured_tokens,
                    backtrack=self.formatting.backtrack,
                )
            )
            normalized_text = normalization.text
            trace.normalization_ended_ns = self._clock_ns()
            stage = "postprocess"
            trace.postprocess_started_ns = self._clock_ns()
            request = PostprocessRequest(
                text=normalized_text,
                raw_text=raw_text,
                transcription=transcription,
                language=transcription.language,
                processing_intent=job.processing_intent,
                detected_signals=normalization.detected_signals,
                protected_spans=normalization.protected_spans,
            )
            try:
                if self.postprocess_router is not None:
                    decision = self.postprocess_router.decide(request)
                    if decision.route == PostprocessRoute.PROVIDER:
                        request = replace(
                            request,
                            preferred_terms=self._preferred_terms(
                                transcription.language,
                                job.app_bundle_id,
                            ),
                        )
                        self.status.update(activity=ActivityState.POSTPROCESSING)
                    result = self.postprocess_router.process_decision(request, decision)
                    trace.route = result.route
                    trace.reason = result.reason
                elif self.postprocessor.name == "none":
                    trace.route = PostprocessRoute.RULES_ONLY
                    trace.reason = RouteReason.PROVIDER_DISABLED
                    result = self.postprocessor.process(normalized_text)
                else:
                    trace.route = PostprocessRoute.PROVIDER
                    trace.reason = RouteReason.ALWAYS_MODE
                    request = replace(
                        request,
                        preferred_terms=self._preferred_terms(
                            transcription.language,
                            job.app_bundle_id,
                        ),
                    )
                    self.status.update(activity=ActivityState.POSTPROCESSING)
                    result = self.postprocessor.process(request)
            finally:
                trace.postprocess_ended_ns = self._clock_ns()
            self._raise_if_cancelled()
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
            final_text = result.text
            self.status.update(activity=ActivityState.INJECTING)
            self._raise_if_cancelled()
            stage = "delivery"
            trace.injection_started_ns = self._clock_ns()
            try:
                if isinstance(job.destination, T3CodeDestination):
                    delivery, error_code = self._deliver_to_t3(job, final_text)
                else:
                    self._inject_on_main_thread(final_text, destination=job.destination)
            finally:
                trace.injection_ended_ns = self._clock_ns()
            self._raise_if_cancelled()
            if not isinstance(job.destination, T3CodeDestination):
                delivery = (
                    DeliveryStatus.CLIPBOARD_ONLY
                    if job.destination.uses_clipboard_only
                    else DeliveryStatus.INJECTED
                )
            completion_details: list[str] = []
            if (
                isinstance(job.destination, InjectionDestination)
                and job.destination.uses_clipboard_only
            ):
                completion_details.append("Nessuna destinazione — testo lasciato negli appunti")
            if result.used_fallback:
                completion_details.append(
                    f"{self.postprocessor.name} non disponibile — usato testo normalizzato"
                )
            if delivery == DeliveryStatus.T3_DELIVERED:
                completion_details.insert(0, "Consegnato a T3 Code")
            if delivery in {
                DeliveryStatus.INJECTED,
                DeliveryStatus.CLIPBOARD_ONLY,
                DeliveryStatus.T3_DELIVERED,
            }:
                self.status.complete(
                    fallback=result.used_fallback,
                    detail="; ".join(completion_details) or None,
                )
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

    def _deliver_to_t3(
        self,
        job: AudioJob,
        final_text: str,
    ) -> tuple[DeliveryStatus, str | None]:
        destination = job.destination
        if not isinstance(destination, T3CodeDestination):
            raise TypeError("T3 destination required")
        if self.t3_bridge is None:
            self.status.transient("Bridge T3 Code non disponibile", error=True)
            return DeliveryStatus.T3_FAILED, "t3_validation_error"
        result = self.t3_bridge.deliver_transcription(
            recording_id=destination.recording_id,
            transcript=final_text,
        )
        log_event(
            LOGGER,
            logging.DEBUG if result.outcome == BridgeOutcome.ACCEPTED else logging.WARNING,
            "integration.t3.delivery",
            "T3 Code delivery returned",
            context=self._job_context(job),
            integration_outcome=result.outcome,
            error_code=result.error_code,
            remote_code=getattr(result, "remote_code", None),
        )
        if result.outcome == BridgeOutcome.ACCEPTED:
            return DeliveryStatus.T3_DELIVERED, None
        if result.outcome == BridgeOutcome.REJECTED:
            self.status.transient("T3 Code ha rifiutato la trascrizione", error=True)
            return DeliveryStatus.T3_REJECTED, result.error_code or "t3_rejected"
        if result.outcome == BridgeOutcome.AMBIGUOUS:
            self.status.transient(
                "Consegna a T3 Code non verificabile — nessun nuovo tentativo",
                error=True,
            )
            return DeliveryStatus.T3_AMBIGUOUS, result.error_code or "t3_ambiguous"
        self.status.transient(
            result.message or "Consegna a T3 Code non riuscita",
            error=True,
        )
        return DeliveryStatus.T3_FAILED, result.error_code or "t3_validation_error"

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


def _coerce_transcription(
    result: Any,
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
