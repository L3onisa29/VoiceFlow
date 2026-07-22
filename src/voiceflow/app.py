from __future__ import annotations

import logging
import threading

from voiceflow.audio.recorder import Recorder
from voiceflow.config import AppConfig, load_config
from voiceflow.pipeline import DictationPipeline
from voiceflow.postprocess import create_postprocessor
from voiceflow.state import ActivityState, ModelState, PostprocessState
from voiceflow.stt.parakeet import ParakeetTranscriber
from voiceflow.system.hotkey import PushToTalk
from voiceflow.system.text_injector import TextInjector
from voiceflow.ui.menu_bar import MenuBar
from voiceflow.ui.overlay import Overlay
from voiceflow.ui.status_controller import StatusController

LOGGER = logging.getLogger(__name__)
APP_NAME = "VoiceBud"


def _check_permissions() -> None:
    messages: list[str] = []
    try:
        import Quartz

        if not Quartz.CGPreflightListenEventAccess():
            messages.append("Input Monitoring (global hotkey)")
    except (AttributeError, ImportError):
        pass
    try:
        from ApplicationServices import AXIsProcessTrusted

        if not AXIsProcessTrusted():
            messages.append("Accessibility (text insertion)")
    except ImportError:
        pass
    if messages:
        LOGGER.warning(
            "Grant these permissions in System Settings > Privacy & Security, then restart: %s",
            ", ".join(messages),
        )


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
        self._closing = False
        self.pipeline: DictationPipeline | None = None
        self.hotkey: PushToTalk | None = None

        self.recorder = Recorder(
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            preroll_ms=config.audio.preroll_ms,
        )
        self.overlay = Overlay(lambda: self.recorder.level)
        self.menu_bar = MenuBar(
            on_retry_model=self.retry_model,
            on_retry_codex=self.retry_codex,
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
            show_processing_overlay=config.ui.show_processing_overlay,
            indicator_seconds=config.ui.error_indicator_seconds,
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
            restore_clipboard=config.inject.restore_clipboard,
        )
        self.status.update(
            model=ModelState.NOT_LOADED,
            activity=ActivityState.IDLE,
            postprocess=PostprocessState.CHECKING,
        )

    def start(self) -> None:
        self._start_loading()

    def _start_loading(self) -> None:
        with self._lock:
            if self._loading or self._closing:
                return
            self._loading = True
        threading.Thread(target=self._load_services, name="voiceflow-loader", daemon=True).start()

    def _load_services(self) -> None:
        try:
            self.postprocessor.preflight()
            self.transcriber.load()
        except Exception as exc:
            LOGGER.exception("Model loading failed")
            self.status.update(model=ModelState.ERROR, message=str(exc))
        else:
            self._app_helper.callAfter(self._activate_input)
        finally:
            with self._lock:
                self._loading = False

    def _activate_input(self) -> None:
        with self._lock:
            if self._closing or self.pipeline is not None:
                return
        try:
            self.recorder.start_stream()
            self.pipeline = DictationPipeline(
                transcriber=self.transcriber,
                postprocessor=self.postprocessor,
                injector=self.injector,
                status=self.status,
                max_pending=self.config.pipeline.max_pending,
                shutdown_timeout_seconds=self.config.pipeline.shutdown_timeout_seconds,
            )
            self.hotkey = PushToTalk(
                self.config.hotkey.key,
                self._recording_started,
                self._recording_stopped,
                mode=self.config.hotkey.mode,
            )
            self.hotkey.start()
        except Exception as exc:
            LOGGER.exception("Input activation failed")
            self.status.update(model=ModelState.ERROR, message=str(exc))

    def _recording_started(self) -> None:
        pipeline = self.pipeline
        if pipeline is None or self.status.state.model != ModelState.READY:
            return
        if not pipeline.try_reserve():
            self.status.transient("Coda piena — registrazione non avviata", error=True)
            return
        if not self.recorder.start():
            pipeline.cancel_reservation()
            return
        self.status.update(activity=ActivityState.RECORDING)

    def _recording_stopped(self) -> None:
        pipeline = self.pipeline
        if pipeline is None or not self.recorder.recording:
            return
        audio = self.recorder.stop()
        try:
            pipeline.submit_reserved(audio)
        except RuntimeError:
            LOGGER.info("Discarded recording during shutdown")

    def _on_model_state(self, state: ModelState, detail: str | None) -> None:
        changes: dict[str, object] = {"model": state}
        if state == ModelState.READY:
            changes.update(model_path=detail, message=None)
        elif state == ModelState.ERROR:
            changes["message"] = detail
        self.status.update(**changes)

    def _on_postprocess_state(
        self, state: PostprocessState, _detail: str | None
    ) -> None:
        self.status.update(postprocess=state)

    def retry_model(self) -> None:
        if self.status.state.model != ModelState.ERROR:
            return
        self.status.update(model=ModelState.NOT_LOADED, message=None)
        self._start_loading()

    def retry_codex(self) -> None:
        threading.Thread(
            target=self.postprocessor.retry,
            name="voiceflow-codex-retry",
            daemon=True,
        ).start()

    def quit(self) -> None:
        with self._lock:
            if self._closing:
                return
            self._closing = True
        self.status.update(activity=ActivityState.STOPPING)
        if self.hotkey is not None:
            self.hotkey.stop()
        self.recorder.close()
        self.overlay.close()
        threading.Thread(
            target=self._finish_quit,
            name="voiceflow-shutdown",
            daemon=True,
        ).start()

    def _finish_quit(self) -> None:
        if self.pipeline is not None:
            self.pipeline.shutdown()
        else:
            self.postprocessor.close()
        self._app_helper.callAfter(self._app_helper.stopEventLoop)


def run(config_path: str | None = None) -> int:
    config = load_config(config_path)
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    _check_permissions()
    _rename_process()
    from AppKit import NSApplication
    from PyObjCTools import AppHelper

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(1)
    controller = VoiceBudApplication(config)
    controller.start()
    AppHelper.runEventLoop()
    return 0
