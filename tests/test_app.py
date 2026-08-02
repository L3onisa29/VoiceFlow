from __future__ import annotations

import threading
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voiceflow.app import VoiceBudApplication, _run_event_loop, _single_instance_lock, run
from voiceflow.config import AppConfig
from voiceflow.integrations import (
    BridgeResult,
    FrontmostApplication,
    FrontmostApplicationKind,
    T3CodeDestination,
)
from voiceflow.recording import RecordingPhase, RecordingStopCoordinator
from voiceflow.state import ActivityState, ModelState, SystemState
from voiceflow.system.permissions import PermissionStatus
from voiceflow.system.text_injector import FocusTarget, InjectionDestination


class Status:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.messages: list[str] = []
        self.state = SimpleNamespace(
            model=ModelState.READY,
            system=SystemState.READY,
        )
        self.closed = 0

    def update(self, **changes: Any) -> None:
        self.updates.append(changes)
        for key, value in changes.items():
            setattr(self.state, key, value)

    def transient(self, text: str, **_kwargs: Any) -> None:
        self.messages.append(text)

    def close(self) -> None:
        self.closed += 1


class TrackingLock:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._owner: int | None = None
        self._depth = 0

    @property
    def held_by_current_thread(self) -> bool:
        return self._owner == threading.get_ident()

    def __enter__(self) -> TrackingLock:
        self._lock.acquire()
        self._owner = threading.get_ident()
        self._depth += 1
        return self

    def __exit__(self, *_args: object) -> None:
        self._depth -= 1
        if self._depth == 0:
            self._owner = None
        self._lock.release()


class Helper:
    def __init__(self) -> None:
        self.scheduled: list[Any] = []
        self.stopped = 0

    def callAfter(self, function: Any, *args: Any) -> None:
        self.scheduled.append((function, args))

    def stopEventLoop(self) -> None:
        self.stopped += 1


class RecordingPipeline:
    def __init__(self) -> None:
        self.reservations = 0
        self.cancelled = 0
        self.submissions: list[tuple[np.ndarray, dict[str, Any]]] = []
        self.shutdown_calls: list[dict[str, Any]] = []

    def try_reserve(self) -> bool:
        self.reservations += 1
        return True

    def cancel_reservation(self) -> None:
        self.cancelled += 1

    def submit_reserved(self, audio: np.ndarray, **kwargs: Any) -> int:
        self.submissions.append((audio, kwargs))
        return len(self.submissions)

    def shutdown(self, **kwargs: Any) -> None:
        self.shutdown_calls.append(kwargs)


class Bridge:
    def __init__(self, result: BridgeResult | None = None) -> None:
        self.result = result or BridgeResult.accepted_result()
        self.reserved: list[str] = []
        self.abandoned: list[str] = []

    def reserve_recording(self, recording_id: str) -> BridgeResult:
        self.reserved.append(recording_id)
        return self.result

    def abandon_recording(self, recording_id: str) -> None:
        self.abandoned.append(recording_id)


class RecordingRecorder:
    device_name = "Test microphone"
    sample_rate = 16000
    last_duration = 1.0
    last_rms = 0.5
    last_peak = 0.8

    def __init__(self) -> None:
        self.recording = False

    def start(self) -> bool:
        self.recording = True
        return True

    def stop(self) -> np.ndarray:
        self.recording = False
        return np.ones(160, dtype=np.float32)

    def close(self) -> None:
        pass


def make_recording_app(app_config: AppConfig) -> VoiceBudApplication:
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.config = app_config
    app._lock = threading.RLock()
    app._closing = False
    app._recording_phase = RecordingPhase.IDLE
    app._recording_destination = None
    app._recording_lease = None
    app.pipeline = RecordingPipeline()
    app.recorder = RecordingRecorder()
    app.status = Status()
    app.t3_bridge = Bridge()
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.OTHER, "example.other")
    )
    app.recording_stop_coordinator = RecordingStopCoordinator(
        audio_config=app_config.audio,
        device_name=lambda: app.recorder.device_name,
        capture_destination=lambda: app.injector.capture_destination(),
        resolve_bundle_id=lambda pid: app.app_bundle_resolver.resolve(pid),
        submission_allowed=lambda: not app._closing,
    )
    return app


def test_destination_is_captured_when_recording_stops(
    app_config: AppConfig,
) -> None:
    destination = InjectionDestination.focused(FocusTarget(7, "selected-during-recording"))
    capture_calls = 0

    def capture_destination() -> InjectionDestination:
        nonlocal capture_calls
        capture_calls += 1
        return destination

    app = make_recording_app(app_config)
    app.injector = SimpleNamespace(capture_destination=capture_destination)

    assert app._recording_started()
    assert capture_calls == 0
    assert app._recording_stopped()

    assert capture_calls == 1
    assert len(app.pipeline.submissions) == 1
    _audio, kwargs = app.pipeline.submissions[0]
    assert kwargs["destination"] == destination


def test_missing_destination_at_stop_uses_clipboard_only(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.injector = SimpleNamespace(
        capture_destination=lambda: (_ for _ in ()).throw(RuntimeError("No focused destination"))
    )

    assert app._recording_started()
    assert app._recording_stopped()

    _audio, kwargs = app.pipeline.submissions[0]
    assert kwargs["destination"].uses_clipboard_only


def test_t3_frontmost_reserves_uuid_v4_and_keeps_destination(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(
            FrontmostApplicationKind.T3_CODE,
            "com.t3tools.t3code",
        )
    )
    capture_calls = 0

    def capture_destination() -> InjectionDestination:
        nonlocal capture_calls
        capture_calls += 1
        return InjectionDestination.clipboard_only()

    app.injector = SimpleNamespace(capture_destination=capture_destination)

    assert app._recording_started()
    assert len(app.t3_bridge.reserved) == 1
    recording_id = app.t3_bridge.reserved[0]
    assert uuid.UUID(recording_id).version == 4
    assert app._recording_destination == T3CodeDestination(recording_id)

    app.frontmost_detector = SimpleNamespace(
        detect=lambda: pytest.fail("focus must not be checked at stop")
    )
    assert app._recording_stopped()
    assert capture_calls == 0
    _audio, kwargs = app.pipeline.submissions[0]
    assert kwargs["destination"] == T3CodeDestination(recording_id)


@pytest.mark.parametrize(
    "result",
    [
        BridgeResult.rejected(),
        BridgeResult.ambiguous(),
        BridgeResult.validation_error(),
    ],
)
def test_t3_reservation_failure_never_starts_recorder_or_falls_back(
    app_config: AppConfig,
    result: BridgeResult,
) -> None:
    app = make_recording_app(app_config)
    app.t3_bridge = Bridge(result)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    capture = pytest.fail
    app.injector = SimpleNamespace(capture_destination=capture)

    assert not app._recording_started()
    assert not app.recorder.recording
    assert app.pipeline.cancelled == 1
    assert app._recording_destination is None


def test_frontmost_detection_failure_stops_before_recording(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.UNAVAILABLE)
    )

    assert not app._recording_started()
    assert not app.recorder.recording
    assert app.pipeline.cancelled == 1
    assert app.t3_bridge.reserved == []


def test_recorder_failure_after_t3_acceptance_abandons_session(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    app.recorder.start = lambda: False

    assert not app._recording_started()
    assert app.t3_bridge.abandoned == app.t3_bridge.reserved
    assert app._recording_destination is None


def test_rejected_t3_audio_abandons_session(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    app.injector = SimpleNamespace(
        capture_destination=lambda: pytest.fail("must not capture Accessibility")
    )
    monkeypatch.setattr(
        "voiceflow.recording.stop_coordinator.capture_rejection_reason",
        lambda **_kwargs: "Audio rifiutato",
    )

    assert app._recording_started()
    recording_id = app.t3_bridge.reserved[0]
    assert app._recording_stopped()
    assert app.t3_bridge.abandoned == [recording_id]
    assert app.pipeline.submissions == []


def test_t3_stop_failure_cancels_and_abandons(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    app.recorder.stop = lambda: (_ for _ in ()).throw(RuntimeError("stop failed"))

    assert app._recording_started()
    recording_id = app.t3_bridge.reserved[0]
    assert not app._recording_stopped()
    assert app.pipeline.cancelled == 1
    assert app.t3_bridge.abandoned == [recording_id]
    assert app._recording_destination is None


def test_shutdown_during_t3_recording_abandons_session(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    app.hotkey = None
    app._input_health_stop = None
    app._input_health_thread = None

    assert app._recording_started()
    recording_id = app.t3_bridge.reserved[0]
    pipeline = app.pipeline
    app._deactivate_input()

    assert pipeline.cancelled == 1
    assert app.t3_bridge.abandoned == [recording_id]
    assert app._recording_destination is None


def test_deactivate_between_submission_gate_and_commit_aborts_once(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.injector = SimpleNamespace(capture_destination=InjectionDestination.clipboard_only)
    app.hotkey = None
    app._input_health_stop = None
    app._input_health_thread = None
    assert app._recording_started()
    owned_lease = app._recording_lease
    assert owned_lease is not None
    commit_entered = threading.Event()
    release_commit = threading.Event()

    class BarrierLease:
        def __init__(self) -> None:
            self.abort_calls = 0

        def abort(self) -> None:
            self.abort_calls += 1
            owned_lease.abort()

        def commit_submission(self, audio: np.ndarray, **kwargs: Any) -> int:
            commit_entered.set()
            assert release_commit.wait(timeout=1)
            return owned_lease.commit_submission(audio, **kwargs)

    barrier_lease = BarrierLease()
    app_for_test: Any = app
    app_for_test._recording_lease = barrier_lease
    pipeline = app.pipeline
    stopped: list[bool] = []
    worker = threading.Thread(target=lambda: stopped.append(app._recording_stopped()))
    worker.start()
    assert commit_entered.wait(timeout=1)

    app._deactivate_input()
    release_commit.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert stopped == [False]
    assert pipeline.submissions == []
    assert pipeline.cancelled == 1
    assert barrier_lease.abort_calls == 1


def test_recording_state_is_shared_independently_from_display_context(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.injector = SimpleNamespace(capture_destination=InjectionDestination.clipboard_only)

    assert not app._is_recording()
    assert app._recording_started()
    assert app._recording_phase == RecordingPhase.RECORDING
    assert app._is_recording()
    assert app._recording_stopped()
    assert app._recording_phase == RecordingPhase.IDLE
    assert not app._is_recording()


def test_blocking_detector_does_not_hold_lock_and_rejects_duplicate_events(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    entered = threading.Event()
    release = threading.Event()

    def detect() -> FrontmostApplication:
        entered.set()
        assert release.wait(timeout=1)
        return FrontmostApplication(FrontmostApplicationKind.OTHER, "example.other")

    app.frontmost_detector = SimpleNamespace(detect=detect)
    started: list[bool] = []
    worker = threading.Thread(target=lambda: started.append(app._recording_started()))
    worker.start()
    assert entered.wait(timeout=1)

    with app._lock:
        assert app._recording_phase == RecordingPhase.STARTING
    assert not app._recording_started()
    assert not app._recording_stopped()

    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert started == [True]


def test_deactivate_during_blocking_t3_reservation_compensates(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.hotkey = None
    app._input_health_stop = None
    app._input_health_thread = None
    entered = threading.Event()
    release = threading.Event()

    class BlockingBridge(Bridge):
        def reserve_recording(self, recording_id: str) -> BridgeResult:
            self.reserved.append(recording_id)
            entered.set()
            assert release.wait(timeout=1)
            return self.result

    app.t3_bridge = BlockingBridge()
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: FrontmostApplication(FrontmostApplicationKind.T3_CODE)
    )
    started: list[bool] = []
    pipeline = app.pipeline
    worker = threading.Thread(target=lambda: started.append(app._recording_started()))
    worker.start()
    assert entered.wait(timeout=1)

    app._deactivate_input()
    release.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert started == [False]
    assert pipeline.cancelled == 1
    assert app.t3_bridge.abandoned == app.t3_bridge.reserved
    assert app._recording_phase == RecordingPhase.IDLE


def test_quit_during_blocking_recorder_start_compensates(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.hotkey = None
    app._input_health_stop = None
    app._input_health_thread = None
    app.overlay = SimpleNamespace(close=lambda: None)
    app._finish_quit = lambda: None
    entered = threading.Event()
    release = threading.Event()

    def start() -> bool:
        entered.set()
        assert release.wait(timeout=1)
        app.recorder.recording = True
        return True

    app.recorder.start = start
    started: list[bool] = []
    pipeline = app.pipeline
    worker = threading.Thread(target=lambda: started.append(app._recording_started()))
    worker.start()
    assert entered.wait(timeout=1)

    app.quit()
    release.set()
    worker.join(timeout=1)

    assert not worker.is_alive()
    assert started == [False]
    assert pipeline.cancelled == 1
    assert app._closing
    assert app._recording_phase == RecordingPhase.IDLE


def test_deactivate_after_start_commit_prevents_stale_success_publication(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.hotkey = None
    app._input_health_stop = None
    app._input_health_thread = None
    pipeline = app.pipeline
    original_commit = app._commit_recording_start

    def commit_then_deactivate(*args: Any) -> bool:
        committed = original_commit(*args)
        assert committed
        app._deactivate_input()
        return committed

    app._commit_recording_start = commit_then_deactivate

    assert not app._recording_started()
    assert app._recording_phase == RecordingPhase.IDLE
    assert app.pipeline is None
    assert app.recorder is None
    assert pipeline.cancelled == 1
    assert not any(update.get("recording") is True for update in app.status.updates)


def test_blocking_recorder_stop_does_not_hold_application_lock(
    app_config: AppConfig,
) -> None:
    app = make_recording_app(app_config)
    app.injector = SimpleNamespace(capture_destination=InjectionDestination.clipboard_only)
    assert app._recording_started()
    entered = threading.Event()
    release = threading.Event()
    original_stop = app.recorder.stop

    def stop() -> np.ndarray:
        entered.set()
        assert release.wait(timeout=1)
        return original_stop()

    app.recorder.stop = stop
    stopped: list[bool] = []
    worker = threading.Thread(target=lambda: stopped.append(app._recording_stopped()))
    worker.start()
    assert entered.wait(timeout=1)

    with app._lock:
        assert app._recording_phase == RecordingPhase.STOPPING
    assert not app._recording_started()

    release.set()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert stopped == [True]


@pytest.mark.parametrize(
    "frontmost_kind",
    [FrontmostApplicationKind.OTHER, FrontmostApplicationKind.T3_CODE],
)
def test_native_recording_calls_never_run_under_application_lock(
    app_config: AppConfig,
    frontmost_kind: FrontmostApplicationKind,
) -> None:
    app = make_recording_app(app_config)
    lock = TrackingLock()
    app._lock = lock

    def assert_unlocked() -> None:
        assert not lock.held_by_current_thread

    class GuardedPipeline(RecordingPipeline):
        def try_reserve(self) -> bool:
            assert_unlocked()
            return super().try_reserve()

        def cancel_reservation(self) -> None:
            assert_unlocked()
            super().cancel_reservation()

        def submit_reserved(self, audio: np.ndarray, **kwargs: Any) -> int:
            assert_unlocked()
            return super().submit_reserved(audio, **kwargs)

    class GuardedBridge(Bridge):
        def reserve_recording(self, recording_id: str) -> BridgeResult:
            assert_unlocked()
            return super().reserve_recording(recording_id)

        def abandon_recording(self, recording_id: str) -> None:
            assert_unlocked()
            super().abandon_recording(recording_id)

    class GuardedRecorder(RecordingRecorder):
        def start(self) -> bool:
            assert_unlocked()
            return super().start()

        def stop(self) -> np.ndarray:
            assert_unlocked()
            return super().stop()

    app.pipeline = GuardedPipeline()
    app.t3_bridge = GuardedBridge()
    app.recorder = GuardedRecorder()
    app.frontmost_detector = SimpleNamespace(
        detect=lambda: assert_unlocked() or FrontmostApplication(frontmost_kind, "target.app")
    )
    app.injector = SimpleNamespace(
        capture_destination=lambda: assert_unlocked() or InjectionDestination.clipboard_only()
    )

    assert app._recording_started()
    assert app._recording_stopped()


def test_recording_cannot_start_after_closing(app_config: AppConfig) -> None:
    app = make_recording_app(app_config)
    app._closing = True

    assert not app._recording_started()
    assert app._recording_phase == RecordingPhase.IDLE
    assert app.pipeline.reservations == 0


def test_stt_loading_is_independent_from_postprocess_preflight() -> None:
    loaded: list[str] = []
    helper = Helper()
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.transcriber = SimpleNamespace(load=lambda: loaded.append("stt"))
    app.postprocessor = SimpleNamespace(
        preflight=lambda: pytest.fail("postprocess must not gate STT")
    )
    app.status = Status()
    app._app_helper = helper
    app._lock = threading.RLock()
    app._loading = True
    app._activate_input = lambda: None

    app._load_services()

    assert loaded == ["stt"]
    assert helper.scheduled == [(app._activate_input, ())]
    assert not app._loading


def test_activation_rollback_does_not_publish_stopping(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shutdown_calls: list[dict[str, Any]] = []

    class Recorder:
        device_name = "Default"
        sample_rate = 16000

        def __init__(self, **_kwargs: Any) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    class Pipeline:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def shutdown(self, **kwargs: Any) -> None:
            shutdown_calls.append(kwargs)

    class Hotkey:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("listener failed")

        def stop(self) -> None:
            pass

    monkeypatch.setattr("voiceflow.app.Recorder", Recorder)
    monkeypatch.setattr("voiceflow.app.DictationPipeline", Pipeline)
    monkeypatch.setattr("voiceflow.app.PushToTalk", Hotkey)

    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.config = app_config
    app._lock = threading.RLock()
    app._closing = False
    app.pipeline = None
    app.hotkey = None
    app.recorder = None
    app.transcriber = object()
    app.postprocessor = object()
    app.injector = object()
    app.t3_bridge = object()
    app.status = Status()

    app._activate_input()

    assert shutdown_calls == [
        {
            "close_postprocessor": False,
            "publish_stopping": False,
        }
    ]
    assert app.status.updates[-1]["activity"] == ActivityState.IDLE
    assert app.status.updates[-1]["system"] == SystemState.ERROR


def test_finish_quit_stops_event_loop_even_if_shutdown_raises() -> None:
    helper = Helper()
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.pipeline = SimpleNamespace(
        shutdown=lambda: (_ for _ in ()).throw(RuntimeError("shutdown failed"))
    )
    app.postprocessor = object()
    app._app_helper = helper

    app._finish_quit()

    assert helper.scheduled == [(helper.stopEventLoop, ())]


def test_event_loop_installs_mach_interrupt_for_ctrl_c() -> None:
    calls: list[tuple[str, dict[str, Any]]] = []
    app_helper = SimpleNamespace(
        installMachInterrupt=lambda: calls.append(("install", {})),
        runEventLoop=lambda **kwargs: calls.append(("run", kwargs)),
    )

    _run_event_loop(app_helper)

    assert calls == [("install", {}), ("run", {})]


def test_single_instance_lock_rejects_second_owner(tmp_path: Path) -> None:
    lock_path = tmp_path / "voicebud.lock"
    with (
        _single_instance_lock(lock_path),
        pytest.raises(RuntimeError, match="already running"),
        _single_instance_lock(lock_path),
    ):
        pass


def test_input_health_monitor_reports_revoked_permissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("voiceflow.app.INPUT_HEALTH_CHECK_SECONDS", 0.001)
    helper = Helper()
    status = Status()
    recorder = SimpleNamespace(active=True)
    hotkey = SimpleNamespace(alive=True)
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app._lock = threading.RLock()
    app._closing = False
    app._app_helper = helper
    app.status = status
    app.recorder = recorder
    app.hotkey = hotkey
    app._check_permissions = lambda *, request: PermissionStatus(False, True, True)

    app._monitor_input_health(recorder, hotkey, threading.Event())

    assert len(helper.scheduled) == 1
    callback, args = helper.scheduled[0]
    callback(*args)
    assert status.state.system == SystemState.ERROR
    assert "Input Monitoring" in status.messages[-1]


def test_invalid_configuration_is_logged_without_starting_appkit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fallback_configured: list[bool] = []
    monkeypatch.setattr(
        "voiceflow.app.load_config",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad config")),
    )
    monkeypatch.setattr(
        "voiceflow.app._configure_fallback_logging",
        lambda: fallback_configured.append(True),
    )

    assert run("invalid.yaml") == 2
    assert fallback_configured == [True]
