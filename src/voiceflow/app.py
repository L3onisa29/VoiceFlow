from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import IO

from voiceflow.audio.quality import capture_rejection_reason
from voiceflow.audio.recorder import Recorder
from voiceflow.config import AppConfig, load_config
from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess import create_postprocessor
from voiceflow.state import ActivityState, ModelState, PostprocessState, SystemState
from voiceflow.stt.parakeet import ParakeetTranscriber
from voiceflow.system.hotkey import PushToTalk
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


def _configure_logging(config: AppConfig) -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
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
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        handlers=handlers,
        force=True,
    )


def _configure_fallback_logging() -> None:
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
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
        self.status.update(
            system=SystemState.CHECKING,
            model=ModelState.NOT_LOADED,
            activity=ActivityState.IDLE,
            postprocess=PostprocessState.CHECKING,
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
            self.config.hotkey.key,
            self.config.hotkey.mode,
        )

    def _is_recording(self) -> bool:
        with self._lock:
            recorder = self.recorder
            return bool(self._recording_reserved and recorder is not None and recorder.recording)

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
            if not pipeline.try_reserve():
                self.status.transient("Coda piena — registrazione non avviata", error=True)
                return False
            try:
                started = recorder.start()
            except Exception as exc:
                pipeline.cancel_reservation()
                LOGGER.exception("Cannot start audio input")
                self._audio_status(str(exc))
                return False
            if not started:
                pipeline.cancel_reservation()
                return False
            self._recording_reserved = True
            self.status.update(recording=True)
            LOGGER.info("Recording started")
            return True

    def _recording_stopped(self) -> bool:
        with self._lock:
            pipeline = self.pipeline
            recorder = self.recorder
            if (
                self._closing
                or pipeline is None
                or recorder is None
                or not recorder.recording
                or not self._recording_reserved
            ):
                return False
            audio = recorder.stop()
            self._recording_reserved = False
            self.status.update(recording=False)
        LOGGER.info(
            "Captured %.2fs from %s (rms=%.5f, peak=%.5f)",
            recorder.last_duration,
            recorder.device_name,
            recorder.last_rms,
            recorder.last_peak,
        )
        rejection = capture_rejection_reason(
            duration_seconds=recorder.last_duration,
            rms=recorder.last_rms,
            device_name=recorder.device_name,
            config=self.config.audio,
        )
        if rejection is not None:
            pipeline.cancel_reservation()
            self.status.transient(rejection, error=True)
            return True
        try:
            destination = self.injector.capture_destination()
        except Exception as exc:
            LOGGER.warning(
                "Cannot capture text destination at recording stop; using clipboard: %s",
                exc,
            )
            destination = InjectionDestination.clipboard_only()
        try:
            with self._lock:
                if self._closing:
                    pipeline.cancel_reservation()
                    return False
                pipeline.submit_reserved(
                    audio,
                    injection_destination=destination,
                    sample_rate=recorder.sample_rate,
                )
        except RuntimeError:
            LOGGER.info("Discarded recording during shutdown")
        return True

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
                recorder.stop()
            recorder.close()
        if self._recording_reserved and pipeline is not None:
            pipeline.cancel_reservation()
        self._recording_reserved = False
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
            self.recorder.stop()
        if self._recording_reserved and self.pipeline is not None:
            self.pipeline.cancel_reservation()
        self._recording_reserved = False
        if self.recorder is not None:
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
            self._app_helper.callAfter(self._app_helper.stopEventLoop)


def run(config_path: str | None = None) -> int:
    try:
        config = load_config(config_path)
    except Exception:
        _configure_fallback_logging()
        LOGGER.exception("Configuration loading failed")
        return 2
    _configure_logging(config)
    try:
        with _single_instance_lock():
            _rename_process()
            from AppKit import NSApplication
            from PyObjCTools import AppHelper

            app = NSApplication.sharedApplication()
            app.setActivationPolicy_(1)
            controller = VoiceBudApplication(config)
            controller.start()
            AppHelper.runEventLoop()
    except AlreadyRunningError as exc:
        LOGGER.error("%s", exc)
        return 1
    return 0
