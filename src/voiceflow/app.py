from __future__ import annotations

import logging
import platform
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO, Any

from voiceflow.audio.recorder import Recorder
from voiceflow.config import AppConfig, load_config
from voiceflow.correction import CorrectionService, propose_replacement, word_diff
from voiceflow.diagnostics import BackgroundLogWriter, exception_fields, log_event
from voiceflow.dictionary import DictionaryKind, DictionaryService
from voiceflow.dictionary.app_scope import AppBundleResolver
from voiceflow.feedback import RecentAudioBuffer
from voiceflow.integrations import (
    BridgeOutcome,
    DictationDestination,
    FrontmostApplicationKind,
    MacOSFrontmostApplicationDetector,
    T3CodeBridge,
    T3CodeDestination,
)
from voiceflow.normalization import DeterministicNormalizer
from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess import create_postprocessor
from voiceflow.postprocess.router import PostprocessRouter
from voiceflow.processing import ProcessedDictation, ProcessingIntent
from voiceflow.recording import (
    AccessibilityAtStop,
    CapturedSubmission,
    RecordingLease,
    RecordingStopCoordinator,
    StopDisposition,
    StopResult,
)
from voiceflow.state import ActivityState, ModelState, PostprocessState, SystemState
from voiceflow.storage import HistoryResultSink, VoiceBudStorage
from voiceflow.stt.parakeet import ParakeetTranscriber
from voiceflow.system.hotkey import PushToTalk, hotkey_label
from voiceflow.system.permissions import check_permissions
from voiceflow.system.text_injector import InjectionDestination, TextInjector
from voiceflow.ui.menu_bar import MenuBar
from voiceflow.ui.model_notification import ModelNotification
from voiceflow.ui.overlay import Overlay
from voiceflow.ui.status_controller import StatusController

LOGGER = logging.getLogger(__name__)
APP_NAME = "VoiceBud"
INPUT_HEALTH_CHECK_SECONDS = 5.0


class AlreadyRunningError(RuntimeError):
    pass


@contextmanager
def _single_instance_lock(path: Path | None = None) -> Iterator[IO[str]]:
    import fcntl

    lock_path = path or Path("~/Library/Application Support/VoiceFlow/voicebud.lock").expanduser()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close()
        raise AlreadyRunningError("VoiceBud is already running") from None
    try:
        yield handle
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _configure_logging(config: AppConfig) -> BackgroundLogWriter:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s"
    )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if config.logging.file is not None:
        config.logging.file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                config.logging.file,
                maxBytes=config.logging.max_bytes,
                backupCount=config.logging.backup_count,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
    writer = BackgroundLogWriter(handlers)
    writer.start()
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        handlers=[writer.handler],
        force=True,
    )
    return writer


def _configure_fallback_logging() -> None:
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s [%(threadName)s]: %(message)s"
    )
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        log_file = Path("~/Library/Logs/VoiceBud/voicebud.log").expanduser()
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_file,
                maxBytes=5 * 1024 * 1024,
                backupCount=3,
                encoding="utf-8",
            )
        )
    except OSError:
        pass
    for handler in handlers:
        handler.setFormatter(formatter)
    logging.basicConfig(level=logging.ERROR, handlers=handlers, force=True)


def _rename_process() -> None:
    try:
        from Foundation import NSBundle, NSProcessInfo

        NSProcessInfo.processInfo().setProcessName_(APP_NAME)
        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["CFBundleName"] = APP_NAME
    except (ImportError, AttributeError):
        pass


def _run_event_loop(app_helper: Any) -> None:
    # Cocoa's native event loop does not reliably return control to Python's
    # regular signal handler. PyObjC's Mach interrupt bridge wakes the run loop
    # so terminal SIGINT (Ctrl+C) can stop the application. Install it
    # explicitly: runEventLoop(installInterrupt=True) skips installation when
    # NSApplication.sharedApplication() has already created the application.
    app_helper.installMachInterrupt()
    app_helper.runEventLoop()


class VoiceBudApplication:
    def __init__(self, config: AppConfig) -> None:
        from PyObjCTools import AppHelper

        self.config = config
        self._app_helper = AppHelper
        self._lock = threading.RLock()
        self._loading = False
        self._postprocess_loading = False
        self._closing = False
        self._recording_reserved = False
        self._recording_destination: DictationDestination | AccessibilityAtStop | None = None
        self._recording_lease: RecordingLease | None = None
        self._input_health_stop: threading.Event | None = None
        self._input_health_thread: threading.Thread | None = None
        self.pipeline: DictationPipeline | None = None
        self.hotkey: PushToTalk | None = None
        self.recorder: Recorder | None = None

        self.overlay = Overlay(self._audio_level)
        self.model_notification = ModelNotification()
        self.menu_bar = MenuBar(
            on_retry_input=self.retry_input,
            on_retry_model=self.retry_model,
            on_retry_postprocess=self.retry_postprocess,
            on_copy_last=self.copy_last_text,
            on_copy_raw=self.copy_raw_text,
            on_correct_last=self.correct_last,
            on_add_term=self.add_term,
            on_clear_history=self.clear_history,
            on_deep_next=self.request_deep_cleanup,
            on_quit=self.quit,
        )
        postprocess_label = config.postprocess.provider
        if config.postprocess.provider == "codex":
            postprocess_label += f" / {config.postprocess.codex.model}"
        elif config.postprocess.provider == "ollama":
            postprocess_label += f" / {config.postprocess.ollama.model}"
        self.status = StatusController(
            self.menu_bar,
            self.overlay,
            postprocess_label=postprocess_label,
            model_notification=self.model_notification,
            stt_provider=config.stt.provider,
            show_processing_overlay=config.ui.show_processing_overlay,
            error_indicator_seconds=config.ui.error_indicator_seconds,
            completion_indicator_seconds=config.ui.completion_indicator_seconds,
        )
        self.postprocessor = create_postprocessor(
            config.postprocess,
            state_callback=self._on_postprocess_state,
        )
        self.transcriber = ParakeetTranscriber(
            config.stt,
            state_callback=self._on_model_state,
        )
        self.injector = TextInjector(
            method=config.inject.method,
            clipboard_clear_after_seconds=config.inject.clipboard_clear_after_seconds,
        )
        self.t3_bridge = T3CodeBridge()
        self.frontmost_detector = MacOSFrontmostApplicationDetector()
        self.storage: VoiceBudStorage | None = None
        self.dictionary: DictionaryService | None = None
        storage_failed = False
        try:
            self.storage = VoiceBudStorage(
                config.storage.database_path,
                history=config.history,
            )
            if config.dictionary.enabled:
                self.dictionary = DictionaryService(self.storage)
        except Exception:
            storage_failed = True
            LOGGER.exception("Optional local storage initialization failed")
        self.history_sink = HistoryResultSink(self.storage, config.history)
        self.correction_service = CorrectionService(self.history_sink, self.dictionary)
        self.audio_feedback = RecentAudioBuffer.for_pipeline(
            config.feedback.recent_audio_seconds,
            max_pending=config.pipeline.max_pending,
        )
        self.app_bundle_resolver = AppBundleResolver()
        self.recording_stop_coordinator = RecordingStopCoordinator(
            audio_config=config.audio,
            device_name=lambda: self.recorder.device_name if self.recorder is not None else "audio",
            capture_destination=lambda: self.injector.capture_destination(),
            resolve_bundle_id=lambda pid: self.app_bundle_resolver.resolve(pid),
            store_recent_audio=lambda identifier, audio, sample_rate: self.audio_feedback.store(
                identifier,
                audio,
                sample_rate,
            ),
            submission_allowed=self._recording_submission_allowed,
        )
        self.normalizer = DeterministicNormalizer(self.dictionary)
        self.postprocess_router = PostprocessRouter(self.postprocessor, config.postprocess.routing)
        self.status.update(
            system=SystemState.CHECKING,
            model=ModelState.NOT_LOADED,
            activity=ActivityState.IDLE,
            postprocess=PostprocessState.CHECKING,
        )
        if storage_failed:
            self.status.transient(
                "Archivio locale non disponibile — dettatura ancora attiva",
                error=True,
            )

    def start(self) -> None:
        permissions = self._check_permissions(request=True)
        if not permissions.ready:
            LOGGER.error("%s", permissions.message)
            self.status.update(
                system=SystemState.ERROR,
                system_message=permissions.message,
            )
            return
        self.status.update(system=SystemState.READY, system_message=None)
        self._start_postprocess_preflight()
        self._start_loading()

    def _check_permissions(self, *, request: bool):
        return check_permissions(
            request=request,
            microphone_callback=self._microphone_permission_decided,
        )

    def _microphone_permission_decided(self, granted: bool) -> None:
        LOGGER.info("Microphone permission decision: %s", "granted" if granted else "denied")
        self._app_helper.callAfter(self.retry_input)

    def _audio_level(self) -> float:
        recorder = self.recorder
        return recorder.level if recorder is not None else 0.0

    def _start_loading(self) -> None:
        with self._lock:
            if self._loading or self._closing:
                return
            self._loading = True
        threading.Thread(target=self._load_services, name="voiceflow-loader", daemon=True).start()

    def _start_postprocess_preflight(self) -> None:
        with self._lock:
            if self._postprocess_loading or self._closing:
                return
            self._postprocess_loading = True
        threading.Thread(
            target=self._preflight_postprocessor,
            name="voiceflow-postprocess-preflight",
            daemon=True,
        ).start()

    def _preflight_postprocessor(self) -> None:
        try:
            self.postprocessor.preflight()
        except Exception as exc:
            LOGGER.exception("Post-processor preflight failed")
            self.status.update(
                postprocess=PostprocessState.ERROR,
                postprocess_message=str(exc),
            )
        finally:
            with self._lock:
                self._postprocess_loading = False

    def _load_services(self) -> None:
        try:
            self.transcriber.load()
        except Exception as exc:
            LOGGER.exception("Model loading failed")
            self.status.update(model=ModelState.ERROR, model_message=str(exc))
        else:
            self._app_helper.callAfter(self._activate_input)
        finally:
            with self._lock:
                self._loading = False

    def _activate_input(self) -> None:
        with self._lock:
            if self._closing or self.pipeline is not None:
                return
        recorder: Recorder | None = None
        pipeline: DictationPipeline | None = None
        hotkey: PushToTalk | None = None
        try:
            recorder = Recorder(
                sample_rate=self.config.audio.sample_rate,
                channels=self.config.audio.channels,
                device=self.config.audio.device,
                maximum_recording_seconds=self.config.audio.maximum_recording_seconds,
                on_max_duration=self._recording_limit_reached,
                on_status=self._audio_status,
            )
            pipeline = DictationPipeline(
                transcriber=self.transcriber,
                postprocessor=self.postprocessor,
                injector=self.injector,
                status=self.status,
                max_pending=self.config.pipeline.max_pending,
                shutdown_timeout_seconds=self.config.pipeline.shutdown_timeout_seconds,
                injection_timeout_seconds=self.config.pipeline.injection_timeout_seconds,
                result_sink=getattr(self, "history_sink", None),
                normalizer=getattr(self, "normalizer", None),
                postprocess_router=getattr(self, "postprocess_router", None),
                formatting=self.config.formatting,
                preferred_terms=self._preferred_terms,
                t3_bridge=self.t3_bridge,
            )
            hotkey = PushToTalk(
                self.config.hotkey.key,
                self._recording_started,
                self._recording_stopped,
                mode=self.config.hotkey.mode,
                is_recording=self._is_recording,
            )
            hotkey.start()
        except Exception as exc:
            LOGGER.exception("Input activation failed")
            if hotkey is not None:
                with suppress(Exception):
                    hotkey.stop()
            if pipeline is not None:
                with suppress(Exception):
                    pipeline.shutdown(
                        close_postprocessor=False,
                        publish_stopping=False,
                    )
            if recorder is not None:
                with suppress(Exception):
                    recorder.close()
            self.status.update(
                system=SystemState.ERROR,
                system_message=str(exc),
                activity=ActivityState.IDLE,
            )
            return
        self.recorder = recorder
        self.pipeline = pipeline
        self.hotkey = hotkey
        self._start_input_health_monitor(recorder, hotkey)
        self.status.update(
            system=SystemState.READY,
            activity=ActivityState.IDLE,
            input_device=recorder.device_name,
            system_message=None,
        )
        LOGGER.info("Audio input ready: %s at %d Hz", recorder.device_name, recorder.sample_rate)
        LOGGER.info(
            "Hotkey ready: %s (%s mode)",
            hotkey_label(self.config.hotkey.key),
            self.config.hotkey.mode,
        )

    def _is_recording(self) -> bool:
        with self._lock:
            recorder = self.recorder
            return bool(self._recording_reserved and recorder is not None and recorder.recording)

    def _recording_submission_allowed(self) -> bool:
        with self._lock:
            return not self._closing

    def _recording_started(self) -> bool:
        with self._lock:
            pipeline = self.pipeline
            recorder = self.recorder
            state = self.status.state
            if (
                self._closing
                or pipeline is None
                or recorder is None
                or state.model != ModelState.READY
                or state.system != SystemState.READY
                or self._recording_reserved
            ):
                return False
            lease = RecordingLease.try_reserve(pipeline, self.t3_bridge)
            if lease is None:
                self.status.transient("Coda piena — registrazione non avviata", error=True)
                return False
            try:
                frontmost = self.frontmost_detector.detect()
            except Exception as exc:
                log_event(
                    LOGGER,
                    logging.ERROR,
                    "destination.detection_failed",
                    "Frontmost application detection failed",
                    host_platform="macos",
                    operation="destination_detection",
                    exc_info=True,
                    **exception_fields(exc),
                )
                frontmost = None
            if frontmost is None or frontmost.kind == FrontmostApplicationKind.UNAVAILABLE:
                log_event(
                    LOGGER,
                    logging.WARNING,
                    "destination.unavailable",
                    "Recording was not started because the destination is unknown",
                    host_platform="macos",
                    target_platform="unknown",
                    operation="destination_detection",
                )
                lease.abort()
                self._recording_destination = None
                self.status.transient(
                    "Impossibile determinare in modo sicuro l’app in primo piano",
                    error=True,
                )
                return False
            if frontmost.kind == FrontmostApplicationKind.T3_CODE:
                recording_id = str(uuid.uuid4())
                reservation = self.t3_bridge.reserve_recording(recording_id)
                if reservation.outcome != BridgeOutcome.ACCEPTED:
                    log_event(
                        LOGGER,
                        logging.WARNING,
                        "integration.t3.reservation_rejected",
                        "T3 Code recording reservation failed",
                        host_platform="macos",
                        target_platform="t3_code",
                        operation="integration",
                        integration_outcome=reservation.outcome,
                        error_code=reservation.error_code,
                        remote_code=getattr(reservation, "remote_code", None),
                    )
                    lease.abort()
                    self._recording_destination = None
                    self.status.transient(
                        reservation.message or "Impossibile riservare la dettatura in T3 Code",
                        error=True,
                    )
                    return False
                lease.attach_t3(recording_id)
                destination: DictationDestination | AccessibilityAtStop = T3CodeDestination(
                    recording_id
                )
            else:
                destination = AccessibilityAtStop.REQUIRED
            try:
                started = recorder.start()
            except Exception as exc:
                lease.abort()
                self._recording_destination = None
                LOGGER.exception("Cannot start audio input")
                self._audio_status(str(exc))
                return False
            if not started:
                lease.abort()
                self._recording_destination = None
                return False
            self._recording_reserved = True
            self._recording_destination = destination
            self._recording_lease = lease
            self.status.update(recording=True)
            log_event(
                LOGGER,
                logging.DEBUG,
                "recording.started",
                "Recording started",
                host_platform="macos",
                target_platform=(
                    "t3_code" if isinstance(destination, T3CodeDestination) else "focused_input"
                ),
                target_app=frontmost.bundle_identifier or "unknown",
                operation=(
                    "integration" if isinstance(destination, T3CodeDestination) else "input_write"
                ),
                input_device=recorder.device_name,
            )
            return True

    def _recording_stopped(self) -> bool:
        with self._lock:
            recorder = self.recorder
            lease = self._recording_lease
            if (
                self._closing
                or self.pipeline is None
                or recorder is None
                or not recorder.recording
                or not self._recording_reserved
                or self._recording_destination is None
                or lease is None
            ):
                return False
            recording_destination = self._recording_destination
            try:
                audio = recorder.stop()
            except Exception:
                self._recording_reserved = False
                self._recording_destination = None
                self._recording_lease = None
                with suppress(Exception):
                    lease.abort()
                self.status.update(recording=False)
                LOGGER.exception("Cannot stop audio input")
                self.status.transient("Arresto della registrazione non riuscito", error=True)
                return False
            self._recording_reserved = False
            self._recording_destination = None
            self.status.update(recording=False)
        LOGGER.debug(
            "Captured %.2fs from %s (rms=%.5f, peak=%.5f)",
            recorder.last_duration,
            recorder.device_name,
            recorder.last_rms,
            recorder.last_peak,
        )
        captured = CapturedSubmission(
            audio=audio,
            sample_rate=recorder.sample_rate,
            duration_seconds=recorder.last_duration,
            rms=recorder.last_rms,
            peak=recorder.last_peak,
            recording_destination=recording_destination,
        )
        result = self.recording_stop_coordinator.submit(captured, lease)
        with self._lock:
            if self._recording_lease is lease:
                self._recording_lease = None
        return self._present_stop_result(result, captured)

    def _present_stop_result(self, result: StopResult, captured: CapturedSubmission) -> bool:
        destination = result.destination
        if result.disposition == StopDisposition.REJECTED:
            log_event(
                LOGGER,
                logging.WARNING,
                "recording.rejected",
                "Captured audio was rejected",
                host_platform="macos",
                target_platform=(
                    "t3_code" if isinstance(destination, T3CodeDestination) else "focused_input"
                ),
                operation=(
                    "integration" if isinstance(destination, T3CodeDestination) else "input_write"
                ),
                reason=result.message,
                audio_seconds=captured.duration_seconds,
                rms=captured.rms,
                peak=captured.peak,
            )
            self.status.transient(result.message or "Registrazione rifiutata", error=True)
            return True
        if result.disposition == StopDisposition.DISCARDED:
            return False
        if result.disposition == StopDisposition.FAILED:
            self.status.transient(result.message or "Registrazione non accodata", error=True)
            return True

        assert destination is not None and result.sequence_id is not None
        clipboard_only = (
            isinstance(destination, InjectionDestination) and destination.uses_clipboard_only
        )
        log_event(
            LOGGER,
            logging.DEBUG,
            "recording.submitted",
            "Recording submitted for processing",
            job_id=result.sequence_id,
            host_platform="macos",
            target_platform=(
                "t3_code"
                if isinstance(destination, T3CodeDestination)
                else ("clipboard" if clipboard_only else "focused_input")
            ),
            target_app=result.app_bundle_id or "unknown",
            operation=(
                "integration"
                if isinstance(destination, T3CodeDestination)
                else ("clipboard_write" if clipboard_only else "input_write")
            ),
        )
        return True

    def _preferred_terms(self, language: str | None, app_bundle_id: str | None) -> tuple[str, ...]:
        if self.dictionary is None:
            return ()
        try:
            return self.dictionary.preferred_terms(
                language=language,
                app_bundle_id=app_bundle_id,
            )
        except Exception:
            self.dictionary.enabled = False
            LOGGER.exception("Dictionary disabled after a local storage failure")
            self.status.transient(
                "Dizionario non disponibile — dettatura ancora attiva",
                error=True,
            )
            return ()

    def request_deep_cleanup(self) -> None:
        pipeline = self.pipeline
        if pipeline is None:
            self.status.transient("Ingresso non attivo", error=True)
            return
        pipeline.request_next_processing_intent(ProcessingIntent.DEEP)
        self.status.transient("Pulizia approfondita richiesta per la prossima dettatura")

    def _last_dictation(self) -> ProcessedDictation | None:
        outcome = self.history_sink.last_outcome
        if outcome is None or not outcome.dictation.final_text:
            self.status.transient("Nessuna trascrizione recente", error=True)
            return None
        return outcome.dictation

    def copy_last_text(self) -> None:
        item = self._last_dictation()
        if item is not None:
            text = self.history_sink.last_corrected_text or item.final_text
            self.injector.copy_to_clipboard(text)
            self.status.transient("Ultimo testo copiato")

    def copy_raw_text(self) -> None:
        item = self._last_dictation()
        if item is not None:
            self.injector.copy_to_clipboard(item.raw_text)
            self.status.transient("Transcript originale copiato")

    def correct_last(self) -> None:
        item = self._last_dictation()
        if item is None:
            return
        from voiceflow.ui.correction_panel import confirm, edit_correction

        corrected = edit_correction(item.raw_text, item.final_text)
        if not corrected or corrected == item.final_text:
            return
        proposal = propose_replacement(item.final_text, corrected)
        confirmed = bool(
            proposal
            and self.dictionary is not None
            and confirm(
                "Aggiungere questa regola al dizionario?",
                f"“{proposal.spoken_form}” → “{proposal.written_form}”\n"
                + "\n".join(word_diff(item.final_text, corrected)),
            )
        )
        self.correction_service.save(
            corrected,
            confirm_replacement=confirmed,
            language=item.transcription.language,
            app_bundle_id=item.app_bundle_id,
        )
        if confirm(
            "Salvare l’audio nel corpus benchmark locale?",
            "Il file resta solo nella directory locale configurata e non viene caricato.",
        ):
            try:
                self.audio_feedback.save_confirmed(
                    str(item.sequence_id),
                    self.config.feedback.benchmark_directory,
                    reference=corrected,
                    confirmed=True,
                )
            except ValueError:
                self.status.transient("Audio recente scaduto o non disponibile", error=True)
        self.status.transient("Correzione salvata")

    def add_term(self) -> None:
        if self.dictionary is None or not self.dictionary.enabled:
            self.status.transient("Dizionario non disponibile", error=True)
            return
        from voiceflow.ui.correction_panel import confirm, prompt_term

        values = prompt_term()
        if values is None:
            return
        spoken, written = values
        if not confirm("Confermare la nuova regola?", f"“{spoken}” → “{written}”"):
            return
        self.dictionary.add(
            kind=DictionaryKind.PREFERRED_TERM,
            spoken_form=spoken,
            written_form=written,
        )
        self.status.transient("Termine aggiunto")

    def clear_history(self) -> None:
        if self.storage is None:
            self.status.transient("Cronologia non disponibile", error=True)
            return
        from voiceflow.ui.correction_panel import confirm

        if confirm(
            "Cancellare la cronologia?",
            "Le voci della cronologia locale verranno eliminate.",
        ):
            self.storage.clear_history()
            self.status.transient("Cronologia cancellata")

    def _recording_limit_reached(self) -> None:
        self._app_helper.callAfter(self._finish_recording_at_limit)

    def _finish_recording_at_limit(self) -> None:
        if self._recording_stopped():
            if self.hotkey is not None:
                self.hotkey.mark_recording_stopped()
            self.status.transient(
                f"Durata massima di {self.config.audio.maximum_recording_seconds:g}s raggiunta"
            )

    def _audio_status(self, detail: str) -> None:
        self.status.update(
            system=SystemState.ERROR,
            system_message=f"Problema ingresso audio: {detail}",
        )
        self.status.transient(f"Problema ingresso audio: {detail}", error=True)

    def _start_input_health_monitor(self, recorder: Recorder, hotkey: PushToTalk) -> None:
        stop = threading.Event()
        thread = threading.Thread(
            target=self._monitor_input_health,
            args=(recorder, hotkey, stop),
            name="voiceflow-input-health",
            daemon=True,
        )
        with self._lock:
            if self._closing:
                return
            self._input_health_stop = stop
            self._input_health_thread = thread
        thread.start()

    def _monitor_input_health(
        self,
        recorder: Recorder,
        hotkey: PushToTalk,
        stop: threading.Event,
    ) -> None:
        while not stop.wait(INPUT_HEALTH_CHECK_SECONDS):
            with self._lock:
                if self._closing or self.recorder is not recorder or self.hotkey is not hotkey:
                    return
            try:
                permissions = self._check_permissions(request=False)
                if not permissions.ready:
                    detail = permissions.message
                elif recorder.recording and not recorder.active:
                    detail = "Il flusso del microfono si è interrotto durante la registrazione"
                elif not recorder.recording and recorder.active:
                    detail = "Il microfono è rimasto attivo fuori dalla registrazione"
                elif not hotkey.alive:
                    detail = "Il listener della hotkey non è più attivo"
                else:
                    continue
            except Exception as exc:
                LOGGER.exception("Input health check failed")
                detail = f"Controllo ingresso non riuscito: {exc}"
            self._app_helper.callAfter(
                self._report_input_health_error,
                recorder,
                hotkey,
                detail,
            )
            return

    def _report_input_health_error(
        self,
        recorder: Recorder,
        hotkey: PushToTalk,
        detail: str,
    ) -> None:
        with self._lock:
            if self._closing or self.recorder is not recorder or self.hotkey is not hotkey:
                return
        LOGGER.error("Input health check failed: %s", detail)
        self.status.update(system=SystemState.ERROR, system_message=detail)
        self.status.transient(detail, error=True)

    def _stop_input_health_monitor(self) -> None:
        with self._lock:
            stop = self._input_health_stop
            thread = self._input_health_thread
            self._input_health_stop = None
            self._input_health_thread = None
        if stop is not None:
            stop.set()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=0.2)

    def _on_model_state(self, state: ModelState, detail: str | None) -> None:
        changes: dict[str, object] = {"model": state}
        if state == ModelState.READY:
            changes.update(model_path=detail, model_message=None)
        elif state == ModelState.ERROR:
            changes["model_message"] = detail
        self.status.update(**changes)

    def _on_postprocess_state(self, state: PostprocessState, detail: str | None) -> None:
        self.status.update(postprocess=state, postprocess_message=detail)

    def retry_model(self) -> None:
        if self.status.state.model != ModelState.ERROR:
            return
        self.status.update(model=ModelState.NOT_LOADED, model_message=None)
        self._start_loading()

    def retry_input(self) -> None:
        with self._lock:
            if self._closing:
                return
        permissions = self._check_permissions(request=True)
        if not permissions.ready:
            self.status.update(
                system=SystemState.ERROR,
                system_message=permissions.message,
            )
            return
        if self.pipeline is not None:
            if self.status.state.system != SystemState.ERROR:
                return
            active_jobs = self.pipeline.pending - int(self._recording_reserved)
            if active_jobs > 0:
                self.status.transient(
                    "Attendi il completamento dell’elaborazione prima di riattivare l’ingresso",
                    error=True,
                )
                return
            self._deactivate_input()
        self.status.update(system=SystemState.READY, system_message=None)
        if self.status.state.model == ModelState.READY:
            self._activate_input()
        else:
            self._start_postprocess_preflight()
            self._start_loading()

    def retry_postprocess(self) -> None:
        with self._lock:
            if self._closing:
                return
            if self.pipeline is not None and self.pipeline.pending:
                self.status.transient(
                    "Attendi il completamento dell’elaborazione prima di riprovare",
                    error=True,
                )
                return
        self._start_postprocess_preflight()

    def _deactivate_input(self) -> None:
        self._stop_input_health_monitor()
        hotkey, recorder, pipeline = self.hotkey, self.recorder, self.pipeline
        self.hotkey = None
        self.recorder = None
        self.pipeline = None
        if hotkey is not None:
            hotkey.stop()
        if recorder is not None:
            if recorder.recording:
                with suppress(Exception):
                    recorder.stop()
            with suppress(Exception):
                recorder.close()
        self._abort_current_recording_lease()
        self._recording_reserved = False
        self._recording_destination = None
        if pipeline is not None:
            pipeline.shutdown(
                close_postprocessor=False,
                publish_stopping=False,
            )
        self.status.update(
            activity=ActivityState.IDLE,
            recording=False,
            input_device=None,
            queue_depth=0,
        )

    def quit(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        self._stop_input_health_monitor()
        self.status.update(activity=ActivityState.STOPPING)
        if self.hotkey is not None:
            self.hotkey.stop()
        if self.recorder is not None and self.recorder.recording:
            with suppress(Exception):
                self.recorder.stop()
        self._abort_current_recording_lease()
        self._recording_reserved = False
        self._recording_destination = None
        if self.recorder is not None:
            with suppress(Exception):
                self.recorder.close()
        self.status.close()
        self.overlay.close()
        threading.Thread(
            target=self._finish_quit,
            name="voiceflow-shutdown",
            daemon=True,
        ).start()

    def _finish_quit(self) -> None:
        try:
            if self.pipeline is not None:
                self.pipeline.shutdown()
            else:
                self.postprocessor.close()
        except Exception:
            LOGGER.exception("Application shutdown failed")
        finally:
            with suppress(Exception):
                self.injector.close()
            with suppress(Exception):
                self.audio_feedback.clear()
            storage = getattr(self, "storage", None)
            if storage is not None:
                with suppress(Exception):
                    storage.close()
            self._app_helper.callAfter(self._app_helper.stopEventLoop)

    def _abort_current_recording_lease(self) -> None:
        lease = getattr(self, "_recording_lease", None)
        self._recording_lease = None
        if lease is not None:
            with suppress(Exception):
                lease.abort()


def run(config_path: str | None = None, *, debug: bool = False) -> int:
    try:
        config = load_config(config_path)
    except Exception:
        _configure_fallback_logging()
        LOGGER.exception("Configuration loading failed")
        return 2
    if debug:
        config.logging.level = "DEBUG"
    log_writer = _configure_logging(config)
    macos_version = platform.mac_ver()[0] or "unknown"
    log_event(
        LOGGER,
        logging.INFO,
        "application.starting",
        "VoiceBud starting",
        host_platform="macos",
        macos_version=macos_version,
        config_path=config.source_path,
        log_level=config.logging.level,
        log_file=config.logging.file or "disabled",
        stt_provider=config.stt.provider,
        postprocess_provider=config.postprocess.provider,
        injection_method=config.inject.method,
    )
    try:
        try:
            with _single_instance_lock():
                _rename_process()
                from AppKit import NSApplication
                from PyObjCTools import AppHelper

                app = NSApplication.sharedApplication()
                app.setActivationPolicy_(1)
                controller = VoiceBudApplication(config)
                controller.start()
                _run_event_loop(AppHelper)
        except AlreadyRunningError as exc:
            log_event(
                LOGGER,
                logging.ERROR,
                "application.already_running",
                "VoiceBud is already running",
                host_platform="macos",
                **exception_fields(exc),
            )
            return 1
        return 0
    finally:
        log_event(
            LOGGER,
            logging.DEBUG,
            "application.stopped",
            "VoiceBud stopped",
            host_platform="macos",
        )
        log_writer.close()
