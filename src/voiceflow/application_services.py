from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING, Protocol, TypeVar

if TYPE_CHECKING:
    from voiceflow.audio.recorder import Recorder
    from voiceflow.audio.release import AudioReleaseCoordinator
    from voiceflow.config import AppConfig
    from voiceflow.correction import CorrectionService
    from voiceflow.dictionary import DictionaryService, ResilientDictionary
    from voiceflow.dictionary.app_scope import AppBundleResolver
    from voiceflow.feedback import RecentAudioBuffer
    from voiceflow.integrations import MacOSFrontmostApplicationDetector, T3CodeBridge
    from voiceflow.normalization import DeterministicNormalizer
    from voiceflow.pipeline import DictationPipeline
    from voiceflow.postprocess.base import PostProcessor
    from voiceflow.postprocess.router import PostprocessRouter
    from voiceflow.recording import RecordingStopCoordinator
    from voiceflow.storage import HistoryResultSink, VoiceBudStorage
    from voiceflow.stt.parakeet import ParakeetTranscriber
    from voiceflow.system.hotkey import PushToTalk
    from voiceflow.system.permissions import PermissionStatus
    from voiceflow.system.text_injector import TextInjector
    from voiceflow.ui.contracts import ApplicationViewBundle, MenuActions
    from voiceflow.ui.status_controller import StatusController


class AppHelperPort(Protocol):
    def callAfter(self, function: Callable[..., object], *args: object) -> None: ...

    def installMachInterrupt(self) -> None: ...

    def runEventLoop(self) -> None: ...

    def stopEventLoop(self) -> None: ...


class PermissionChecker(Protocol):
    def __call__(
        self,
        *,
        request: bool,
        microphone_callback: Callable[[bool], None],
    ) -> PermissionStatus: ...


class ViewFactory(Protocol):
    def create(
        self,
        actions: MenuActions,
        *,
        level_fn: Callable[[], float],
    ) -> ApplicationViewBundle: ...


class StatusFactory(Protocol):
    def __call__(
        self,
        views: ApplicationViewBundle,
        config: AppConfig,
        *,
        postprocess_label: str,
    ) -> StatusController: ...


class PostprocessorFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        state_callback: Callable[..., None],
    ) -> PostProcessor: ...


class TranscriberFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        state_callback: Callable[..., None],
    ) -> ParakeetTranscriber: ...


class InjectorFactory(Protocol):
    def __call__(self, config: AppConfig) -> TextInjector: ...


class StorageFactory(Protocol):
    def __call__(self, config: AppConfig) -> VoiceBudStorage: ...


class DictionaryFactory(Protocol):
    def __call__(self, storage: VoiceBudStorage) -> DictionaryService: ...


class ResilientDictionaryFactory(Protocol):
    def __call__(
        self,
        dictionary: DictionaryService,
        *,
        notifier: Callable[[str], None],
    ) -> ResilientDictionary: ...


class HistorySinkFactory(Protocol):
    def __call__(
        self,
        storage: VoiceBudStorage | None,
        config: AppConfig,
    ) -> HistoryResultSink: ...


class CorrectionFactory(Protocol):
    def __call__(
        self,
        history: HistoryResultSink,
        dictionary: DictionaryService | None,
    ) -> CorrectionService: ...


class FeedbackFactory(Protocol):
    def __call__(self, config: AppConfig) -> RecentAudioBuffer: ...


class NormalizerFactory(Protocol):
    def __call__(self, dictionary: ResilientDictionary | None) -> DeterministicNormalizer: ...


class RouterFactory(Protocol):
    def __call__(self, postprocessor: PostProcessor, config: AppConfig) -> PostprocessRouter: ...


class StopCoordinatorFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        device_name: Callable[[], str],
        capture_destination: Callable[..., object],
        resolve_bundle_id: Callable[[int], str | None],
        store_recent_audio: Callable[..., None],
        submission_allowed: Callable[[], bool],
    ) -> RecordingStopCoordinator: ...


class RecorderFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        on_max_duration: Callable[[], None],
        on_status: Callable[[str], None],
        release_coordinator: AudioReleaseCoordinator,
    ) -> Recorder: ...


class PipelineFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        transcriber: ParakeetTranscriber,
        postprocessor: PostProcessor,
        injector: TextInjector,
        status: StatusController,
        result_sink: HistoryResultSink,
        normalizer: DeterministicNormalizer,
        postprocess_router: PostprocessRouter,
        preferred_terms: Callable[[str | None, str | None], tuple[str, ...]],
        t3_bridge: T3CodeBridge,
    ) -> DictationPipeline: ...


class HotkeyFactory(Protocol):
    def __call__(
        self,
        config: AppConfig,
        *,
        on_start: Callable[[], bool],
        on_stop: Callable[[], bool],
        is_recording: Callable[[], bool],
    ) -> PushToTalk: ...


T = TypeVar("T")


class _CloseState(Enum):
    OPEN = auto()
    CLOSING = auto()
    CLOSED = auto()


@dataclass(slots=True)
class ApplicationServices:
    """Injected composition boundary and owner of application-long resources."""

    app_helper: AppHelperPort
    permission_checker: PermissionChecker
    view_factory: ViewFactory
    status_factory: StatusFactory
    audio_release_factory: Callable[[Callable[[str], None]], AudioReleaseCoordinator]
    postprocessor_factory: PostprocessorFactory
    transcriber_factory: TranscriberFactory
    injector_factory: InjectorFactory
    t3_bridge_factory: Callable[[], T3CodeBridge]
    frontmost_detector_factory: Callable[[], MacOSFrontmostApplicationDetector]
    storage_factory: StorageFactory
    dictionary_factory: DictionaryFactory
    resilient_dictionary_factory: ResilientDictionaryFactory
    history_sink_factory: HistorySinkFactory
    correction_factory: CorrectionFactory
    feedback_factory: FeedbackFactory
    app_bundle_resolver_factory: Callable[[], AppBundleResolver]
    stop_coordinator_factory: StopCoordinatorFactory
    normalizer_factory: NormalizerFactory
    router_factory: RouterFactory
    recorder_factory: RecorderFactory
    pipeline_factory: PipelineFactory
    hotkey_factory: HotkeyFactory
    _close_actions: list[Callable[[], None]] = field(default_factory=list, init=False)
    _close_condition: threading.Condition = field(
        default_factory=threading.Condition,
        init=False,
    )
    _close_state: _CloseState = field(default=_CloseState.OPEN, init=False)
    _closing_thread: int | None = field(default=None, init=False)
    _close_error: BaseException | None = field(default=None, init=False)

    def own(self, resource: T, close: Callable[[T], None]) -> T:
        """Register a resource immediately after construction for reverse teardown."""

        with self._close_condition:
            if self._close_state is not _CloseState.OPEN:
                should_close = True
            else:
                self._close_actions.append(lambda: close(resource))
                should_close = False
        if should_close:
            close(resource)
            raise RuntimeError("Application services are already closed")
        return resource

    def close(self) -> None:
        """Close every owned resource once, in reverse construction order."""

        current_thread = threading.get_ident()
        with self._close_condition:
            if self._close_state is _CloseState.CLOSED:
                return
            if self._close_state is _CloseState.CLOSING:
                if self._closing_thread == current_thread:
                    return
                while self._close_state is _CloseState.CLOSING:
                    self._close_condition.wait()
                if self._close_error is not None:
                    raise self._close_error
                return
            self._close_state = _CloseState.CLOSING
            self._closing_thread = current_thread
            actions = tuple(reversed(self._close_actions))
            self._close_actions.clear()
        first_error: BaseException | None = None
        for action in actions:
            try:
                action()
            except BaseException as exc:
                if first_error is None:
                    first_error = exc
        with self._close_condition:
            self._close_error = first_error
            self._closing_thread = None
            self._close_state = _CloseState.CLOSED
            self._close_condition.notify_all()
        if first_error is not None:
            raise first_error
