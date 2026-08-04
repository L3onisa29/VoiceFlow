from __future__ import annotations

from contextlib import suppress
from typing import TYPE_CHECKING, cast

from voiceflow.application_services import AppHelperPort, ApplicationServices
from voiceflow.config import AppConfig

if TYPE_CHECKING:
    from voiceflow.app import VoiceBudApplication


def build_application_services(_config: AppConfig) -> ApplicationServices:
    """Build the native dependency graph without constructing the controller."""

    from PyObjCTools import AppHelper

    from voiceflow.audio.recorder import Recorder
    from voiceflow.audio.release import AudioReleaseCoordinator
    from voiceflow.correction import CorrectionService
    from voiceflow.dictionary import DictionaryService, ResilientDictionary
    from voiceflow.dictionary.app_scope import AppBundleResolver
    from voiceflow.feedback import RecentAudioBuffer
    from voiceflow.integrations import MacOSFrontmostApplicationDetector, T3CodeBridge
    from voiceflow.normalization import DeterministicNormalizer
    from voiceflow.pipeline import DictationPipeline
    from voiceflow.postprocess import create_postprocessor
    from voiceflow.postprocess.router import PostprocessRouter
    from voiceflow.recording import RecordingStopCoordinator
    from voiceflow.storage import HistoryResultSink, VoiceBudStorage
    from voiceflow.stt.parakeet import ParakeetTranscriber
    from voiceflow.system.hotkey import PushToTalk
    from voiceflow.system.permissions import check_permissions
    from voiceflow.system.text_injector import TextInjector
    from voiceflow.ui.factory import NativeViewFactory
    from voiceflow.ui.status_controller import StatusController

    return ApplicationServices(
        app_helper=cast(AppHelperPort, AppHelper),
        permission_checker=check_permissions,
        view_factory=NativeViewFactory(),
        status_factory=lambda views, config, postprocess_label: StatusController(
            views.menu,
            views.overlay,
            postprocess_label=postprocess_label,
            model_notification=views.model_notification,
            stt_provider=config.stt.provider,
            show_processing_overlay=config.ui.show_processing_overlay,
            error_indicator_seconds=config.ui.error_indicator_seconds,
            completion_indicator_seconds=config.ui.completion_indicator_seconds,
        ),
        audio_release_factory=lambda on_saturated: AudioReleaseCoordinator(
            on_saturated=on_saturated
        ),
        postprocessor_factory=lambda config, state_callback: create_postprocessor(
            config.postprocess,
            state_callback=state_callback,
        ),
        transcriber_factory=lambda config, state_callback: ParakeetTranscriber(
            config.stt,
            state_callback=state_callback,
        ),
        injector_factory=lambda config: TextInjector(
            method=config.inject.method,
            clipboard_clear_after_seconds=config.inject.clipboard_clear_after_seconds,
        ),
        t3_bridge_factory=T3CodeBridge,
        frontmost_detector_factory=MacOSFrontmostApplicationDetector,
        storage_factory=lambda config: VoiceBudStorage(
            config.storage.database_path,
            history=config.history,
        ),
        dictionary_factory=DictionaryService,
        resilient_dictionary_factory=lambda dictionary, notifier: ResilientDictionary(
            dictionary,
            notifier=notifier,
        ),
        history_sink_factory=lambda storage, config: HistoryResultSink(storage, config.history),
        correction_factory=lambda history, dictionary: CorrectionService(history, dictionary),
        feedback_factory=lambda config: RecentAudioBuffer.for_pipeline(
            config.feedback.recent_audio_seconds,
            max_pending=config.pipeline.max_pending,
        ),
        app_bundle_resolver_factory=AppBundleResolver,
        stop_coordinator_factory=lambda config, **kwargs: RecordingStopCoordinator(
            audio_config=config.audio,
            **kwargs,
        ),
        normalizer_factory=DeterministicNormalizer,
        router_factory=lambda postprocessor, config: PostprocessRouter(
            postprocessor,
            config.postprocess.routing,
        ),
        recorder_factory=lambda config, **kwargs: Recorder(
            sample_rate=config.audio.sample_rate,
            channels=config.audio.channels,
            device=config.audio.device,
            maximum_recording_seconds=config.audio.maximum_recording_seconds,
            **kwargs,
        ),
        pipeline_factory=lambda config, **kwargs: DictationPipeline(
            **kwargs,
            max_pending=config.pipeline.max_pending,
            shutdown_timeout_seconds=config.pipeline.shutdown_timeout_seconds,
            injection_timeout_seconds=config.pipeline.injection_timeout_seconds,
            formatting=config.formatting,
        ),
        hotkey_factory=lambda config, on_start, on_stop, is_recording: PushToTalk(
            config.hotkey.key,
            on_start,
            on_stop,
            mode=config.hotkey.mode,
            is_recording=is_recording,
        ),
    )


def build_application(config: AppConfig) -> VoiceBudApplication:
    """Build the controller and roll back every partial resource on failure."""

    services = build_application_services(config)
    try:
        from voiceflow.app import VoiceBudApplication

        return VoiceBudApplication(config, services)
    except BaseException:
        with suppress(BaseException):
            services.close()
        raise
