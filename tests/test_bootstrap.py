from __future__ import annotations

import subprocess
import sys
import threading
from collections.abc import Callable
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from voiceflow.app import VoiceBudApplication, run
from voiceflow.application_services import ApplicationServices
from voiceflow.bootstrap import build_application, build_application_services
from voiceflow.config import AppConfig
from voiceflow.recording import RecordingPhase
from voiceflow.state import ModelState, SystemState


class FakeResource:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    def close(self, **_kwargs: object) -> None:
        self.events.append(f"close:{self.name}")

    def clear(self) -> None:
        self.events.append(f"close:{self.name}")


class FakeStatus(FakeResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("status", events)
        self.state = SimpleNamespace(model=ModelState.READY, system=SystemState.READY)
        self.updates: list[dict[str, object]] = []

    def update(self, **changes: object) -> None:
        self.updates.append(changes)
        for name, value in changes.items():
            setattr(self.state, name, value)

    def transient(self, _message: str, **_kwargs: object) -> None:
        pass


class FakeViews(FakeResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("views", events)
        self.menu = SimpleNamespace(update=lambda **_kwargs: None)
        self.overlay = SimpleNamespace(update=lambda _presentation: None, close=lambda: None)
        self.model_notification = SimpleNamespace(
            show_ready=lambda **_kwargs: None,
            show_error=lambda *_args, **_kwargs: None,
            close=lambda: None,
        )


class FakePostprocessor(FakeResource):
    name = "fake"

    def __init__(self, events: list[str]) -> None:
        super().__init__("postprocessor", events)

    def preflight(self) -> bool:
        return True


class FakeTranscriber:
    def load(self) -> None:
        pass


class FakeInjector(FakeResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("injector", events)

    def capture_destination(self) -> object:
        return object()


class FakeStorage(FakeResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("storage", events)


class FakeFeedback(FakeResource):
    def __init__(self, events: list[str]) -> None:
        super().__init__("feedback", events)

    def store(self, *_args: object) -> None:
        pass


class FakeHistory:
    last_outcome = None
    last_corrected_text = None


class FakeHelper:
    def __init__(self) -> None:
        self.calls: list[tuple[Callable[..., object], tuple[object, ...]]] = []

    def callAfter(self, function: Callable[..., object], *args: object) -> None:
        self.calls.append((function, args))

    def installMachInterrupt(self) -> None:
        pass

    def runEventLoop(self) -> None:
        pass

    def stopEventLoop(self) -> None:
        pass


def fake_services(
    events: list[str],
    *,
    fail_at: str | None = None,
) -> ApplicationServices:
    def create(name: str, factory: Callable[[], Any]) -> Any:
        events.append(f"create:{name}")
        if fail_at == name:
            raise RuntimeError(f"factory:{name}")
        return factory()

    return ApplicationServices(
        app_helper=FakeHelper(),
        permission_checker=lambda **_kwargs: SimpleNamespace(ready=True, message=""),
        view_factory=SimpleNamespace(
            create=lambda _actions, **_kwargs: create("views", lambda: FakeViews(events))
        ),
        status_factory=lambda _views, _config, **_kwargs: create(
            "status", lambda: FakeStatus(events)
        ),
        audio_release_factory=lambda _callback: create(
            "audio_release", lambda: FakeResource("audio_release", events)
        ),
        postprocessor_factory=lambda _config, **_kwargs: create(
            "postprocessor", lambda: FakePostprocessor(events)
        ),
        transcriber_factory=lambda _config, **_kwargs: create("transcriber", FakeTranscriber),
        injector_factory=lambda _config: create("injector", lambda: FakeInjector(events)),
        t3_bridge_factory=lambda: create("t3_bridge", object),
        frontmost_detector_factory=lambda: create("frontmost_detector", object),
        storage_factory=lambda _config: create("storage", lambda: FakeStorage(events)),
        dictionary_factory=lambda _storage: create("dictionary", object),
        resilient_dictionary_factory=lambda _dictionary, **_kwargs: create(
            "resilient_dictionary", object
        ),
        history_sink_factory=lambda _storage, _config: create("history_sink", FakeHistory),
        correction_factory=lambda _history, _dictionary: create("correction", object),
        feedback_factory=lambda _config: create("feedback", lambda: FakeFeedback(events)),
        app_bundle_resolver_factory=lambda: create(
            "resolver", lambda: SimpleNamespace(resolve=lambda _pid: None)
        ),
        stop_coordinator_factory=lambda _config, **_kwargs: create("stop_coordinator", object),
        normalizer_factory=lambda _dictionary: create("normalizer", object),
        router_factory=lambda _postprocessor, _config: create("router", object),
        recorder_factory=lambda _config, **_kwargs: create("recorder", object),
        pipeline_factory=lambda _config, **_kwargs: create("pipeline", object),
        hotkey_factory=lambda _config, **_kwargs: create("hotkey", object),
    )


def test_pure_composition_modules_import_without_native_initialization() -> None:
    script = """
import sys
import voiceflow.application_services
import voiceflow.bootstrap
assert 'AppKit' not in sys.modules
assert 'PyObjCTools.AppHelper' not in sys.modules
assert 'sounddevice' not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_native_service_builder_defers_resource_construction(app_config: AppConfig) -> None:
    services = build_application_services(app_config)

    assert services._close_actions == []
    assert services.recorder_factory is not None
    assert services.pipeline_factory is not None
    assert services.hotkey_factory is not None
    services.close()


def test_native_feedback_factory_cleans_stale_transactions(
    app_config: AppConfig,
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    stale = corpus / ".voicebud-audio-orphan.wav"
    stale.write_bytes(b"stale")
    confirmed = corpus / "confirmed.wav"
    confirmed.write_bytes(b"confirmed")
    app_config.feedback.benchmark_directory = corpus
    services = build_application_services(app_config)

    feedback = services.feedback_factory(app_config)
    try:
        assert not stale.exists()
        assert confirmed.read_bytes() == b"confirmed"
    finally:
        feedback.clear()
        services.close()


def test_application_constructs_entirely_from_fake_services(app_config: AppConfig) -> None:
    events: list[str] = []
    services = fake_services(events)

    app = VoiceBudApplication(app_config, services)

    assert app.services is services
    assert app.pipeline is None
    assert app.hotkey is None
    assert app.recorder is None
    assert events == [
        "create:views",
        "create:status",
        "create:audio_release",
        "create:postprocessor",
        "create:transcriber",
        "create:injector",
        "create:t3_bridge",
        "create:frontmost_detector",
        "create:storage",
        "create:dictionary",
        "create:resilient_dictionary",
        "create:history_sink",
        "create:correction",
        "create:feedback",
        "create:resolver",
        "create:stop_coordinator",
        "create:normalizer",
        "create:router",
    ]


def test_dynamic_input_factories_receive_the_complete_dependency_graph(
    app_config: AppConfig,
) -> None:
    events: list[str] = []
    services = fake_services(events)
    app = VoiceBudApplication(app_config, services)
    captured: dict[str, dict[str, object]] = {}

    class Recorder:
        device_name = "Fake microphone"
        sample_rate = 16000

        def close(self) -> None:
            events.append("close:recorder")

    class Pipeline:
        pending = 0

        def shutdown(self, **_kwargs: object) -> None:
            events.append("close:pipeline")

    class Hotkey:
        def start(self) -> None:
            events.append("start:hotkey")

        def stop(self) -> None:
            events.append("close:hotkey")

    def recorder_factory(_config: AppConfig, **kwargs: object) -> Recorder:
        captured["recorder"] = kwargs
        return Recorder()

    def pipeline_factory(_config: AppConfig, **kwargs: object) -> Pipeline:
        captured["pipeline"] = kwargs
        return Pipeline()

    def hotkey_factory(_config: AppConfig, **kwargs: object) -> Hotkey:
        captured["hotkey"] = kwargs
        return Hotkey()

    services.recorder_factory = recorder_factory
    services.pipeline_factory = pipeline_factory
    services.hotkey_factory = hotkey_factory
    app._start_input_health_monitor = lambda _recorder, _hotkey: None

    app._activate_input()

    assert isinstance(app.recorder, Recorder)
    assert isinstance(app.pipeline, Pipeline)
    assert isinstance(app.hotkey, Hotkey)
    assert captured["recorder"]["release_coordinator"] is app.audio_release_coordinator
    assert captured["pipeline"] == {
        "transcriber": app.transcriber,
        "postprocessor": app.postprocessor,
        "injector": app.injector,
        "status": app.status,
        "result_sink": app.history_sink,
        "normalizer": app.normalizer,
        "postprocess_router": app.postprocess_router,
        "preferred_terms": app._preferred_terms,
        "t3_bridge": app.t3_bridge,
    }
    assert set(captured["hotkey"]) == {
        "on_start",
        "on_stop",
        "on_cancel",
        "is_recording",
    }
    assert events[-1] == "start:hotkey"


@pytest.mark.parametrize(
    "failure",
    [
        "views",
        "status",
        "audio_release",
        "postprocessor",
        "transcriber",
        "injector",
        "t3_bridge",
        "frontmost_detector",
        "history_sink",
        "correction",
        "feedback",
        "resolver",
        "stop_coordinator",
        "normalizer",
        "router",
    ],
)
def test_build_application_rolls_back_each_required_factory_failure(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    events: list[str] = []
    services = fake_services(events, fail_at=failure)
    monkeypatch.setattr("voiceflow.bootstrap.build_application_services", lambda _config: services)

    with pytest.raises(RuntimeError, match=f"factory:{failure}"):
        build_application(app_config)

    assert events.count("close:views") <= 1
    assert events.count("close:status") <= 1
    assert events.count("close:postprocessor") <= 1
    assert events.count("close:injector") <= 1
    assert events.count("close:storage") <= 1
    assert events.count("close:feedback") <= 1


def test_build_rollback_preserves_original_error_when_close_fails(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    services = fake_services(events, fail_at="router")
    services.own(object(), lambda _resource: (_ for _ in ()).throw(ValueError("close failed")))
    monkeypatch.setattr("voiceflow.bootstrap.build_application_services", lambda _config: services)

    with pytest.raises(RuntimeError, match="factory:router"):
        build_application(app_config)


def test_application_services_close_is_concurrent_idempotent_and_reverse_order() -> None:
    events: list[str] = []
    services = fake_services(events)
    for name in ("one", "two", "three"):
        services.own(name, lambda resource: events.append(f"close:{resource}"))

    threads = [threading.Thread(target=services.close) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=1)

    assert all(not thread.is_alive() for thread in threads)
    assert events[-3:] == ["close:three", "close:two", "close:one"]


def test_application_services_concurrent_close_waits_and_shares_first_error() -> None:
    events: list[str] = []
    services = fake_services(events)
    entered = threading.Event()
    release = threading.Event()
    outcomes: list[str] = []

    def blocking_close(_resource: object) -> None:
        entered.set()
        assert release.wait(timeout=1)
        raise RuntimeError("cleanup failed")

    def close() -> None:
        try:
            services.close()
        except RuntimeError as exc:
            outcomes.append(str(exc))

    services.own(object(), blocking_close)
    initiating = threading.Thread(target=close)
    waiting = threading.Thread(target=close)
    initiating.start()
    assert entered.wait(timeout=1)
    waiting.start()
    waiting.join(timeout=0.05)
    assert waiting.is_alive()

    release.set()
    initiating.join(timeout=1)
    waiting.join(timeout=1)

    assert not initiating.is_alive()
    assert not waiting.is_alive()
    assert outcomes == ["cleanup failed", "cleanup failed"]
    services.close()


def test_application_services_reentrant_close_does_not_deadlock_or_rerun() -> None:
    events: list[str] = []
    services = fake_services(events)

    def reentrant_close(name: str) -> None:
        events.append(f"close:{name}")
        services.close()

    services.own("before", lambda name: events.append(f"close:{name}"))
    services.own("reentrant", reentrant_close)

    services.close()

    assert events[-2:] == ["close:reentrant", "close:before"]


def test_application_services_rejects_and_closes_late_resource() -> None:
    events: list[str] = []
    services = fake_services(events)
    services.close()

    with pytest.raises(RuntimeError, match="already closed"):
        services.own("late", lambda resource: events.append(f"close:{resource}"))

    assert events[-1] == "close:late"


def test_application_services_runs_all_closers_and_raises_first_error() -> None:
    events: list[str] = []
    services = fake_services(events)

    def failing_close(name: str) -> None:
        events.append(f"close:{name}")
        raise RuntimeError(name)

    services.own("first", failing_close)
    services.own("second", failing_close)

    with pytest.raises(RuntimeError, match="second"):
        services.close()

    assert events[-2:] == ["close:second", "close:first"]


def test_application_quit_closes_notification_and_overlay_once(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    services = fake_services(events)

    class Views(FakeViews):
        def __init__(self) -> None:
            super().__init__(events)
            self.overlay = SimpleNamespace(
                update=lambda _presentation: None,
                close=lambda: events.append("close:overlay"),
            )
            self.model_notification = SimpleNamespace(
                show_ready=lambda **_kwargs: None,
                show_error=lambda *_args, **_kwargs: None,
                close=lambda: events.append("close:notice"),
            )
            self.closed = False

        def close(self) -> None:
            if self.closed:
                return
            self.closed = True
            self.model_notification.close()
            self.overlay.close()
            events.append("close:views")

    services.view_factory = SimpleNamespace(
        create=lambda _actions, **_kwargs: Views(),
    )
    app = VoiceBudApplication(app_config, services)

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr("voiceflow.app.threading.Thread", ImmediateThread)

    app.quit()
    app.quit()
    services.close()

    assert events.count("close:notice") == 1
    assert events.count("close:overlay") == 1


def test_partial_application_construction_leaves_view_bundle_as_only_ui_owner(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    services = fake_services(events)

    class Views(FakeViews):
        def __init__(self) -> None:
            super().__init__(events)
            self.overlay = SimpleNamespace(
                update=lambda _presentation: None,
                close=lambda: events.append("close:overlay"),
            )
            self.model_notification = SimpleNamespace(
                show_ready=lambda **_kwargs: None,
                show_error=lambda *_args, **_kwargs: None,
                close=lambda: events.append("close:notice"),
            )
            self.closed = False

        def close(self) -> None:
            if self.closed:
                return
            self.closed = True
            self.model_notification.close()
            self.overlay.close()

    services.view_factory = SimpleNamespace(create=lambda _actions, **_kwargs: Views())
    services.status_factory = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        RuntimeError("status failed")
    )
    monkeypatch.setattr("voiceflow.bootstrap.build_application_services", lambda _config: services)

    with pytest.raises(RuntimeError, match="status failed"):
        build_application(app_config)

    assert events.count("close:notice") == 1
    assert events.count("close:overlay") == 1


def test_deactivate_closes_dynamic_resources_in_reverse_order_despite_failures() -> None:
    events: list[str] = []
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app._lock = threading.RLock()
    app._input_health_stop = None
    app._input_health_thread = None
    app._recording_lease = None
    app._recording_destination = object()
    app._recording_phase = RecordingPhase.RECORDING
    app.status = FakeStatus(events)

    class Hotkey:
        def stop(self) -> None:
            events.append("close:hotkey")
            raise RuntimeError("hotkey")

    class Pipeline:
        def shutdown(self, **_kwargs: object) -> None:
            events.append("close:pipeline")
            raise RuntimeError("pipeline")

    class Recorder:
        recording = True

        def stop(self) -> None:
            events.append("stop:recorder")

        def close(self) -> None:
            events.append("close:recorder")

    app.hotkey = Hotkey()
    app.pipeline = Pipeline()
    app.recorder = Recorder()

    app._deactivate_input()

    assert [event for event in events if event.startswith(("close:", "stop:"))][-4:] == [
        "close:hotkey",
        "close:pipeline",
        "stop:recorder",
        "close:recorder",
    ]


@pytest.mark.parametrize(
    ("recording", "expected"),
    [
        (False, ["close:hotkey", "close:pipeline", "close:recorder"]),
        (
            True,
            ["close:hotkey", "close:pipeline", "stop:recorder", "close:recorder"],
        ),
        (None, []),
    ],
    ids=["active", "recording", "loading"],
)
def test_quit_closes_dynamic_resources_in_reverse_order(
    monkeypatch: pytest.MonkeyPatch,
    recording: bool | None,
    expected: list[str],
) -> None:
    events: list[str] = []
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app._lock = threading.RLock()
    app._closing = False
    app._loading = recording is None
    app._input_health_stop = None
    app._input_health_thread = None
    app._recording_lease = None
    app._recording_destination = None
    app._recording_phase = RecordingPhase.RECORDING if recording else RecordingPhase.IDLE
    app.status = SimpleNamespace(update=lambda **_kwargs: None)
    app.services = SimpleNamespace(close=lambda: events.append("close:services"))
    app._app_helper = SimpleNamespace(
        callAfter=lambda _function, *_args: events.append("stop:event-loop"),
        stopEventLoop=lambda: None,
    )

    if recording is None:
        app.hotkey = None
        app.pipeline = None
        app.recorder = None
    else:

        class Hotkey:
            def stop(self) -> None:
                events.append("close:hotkey")

        class Pipeline:
            def shutdown(self, **_kwargs: object) -> None:
                events.append("close:pipeline")

        class Recorder:
            def __init__(self) -> None:
                self.recording = recording

            def stop(self) -> None:
                events.append("stop:recorder")
                self.recording = False

            def close(self) -> None:
                events.append("close:recorder")

        app.hotkey = Hotkey()
        app.pipeline = Pipeline()
        app.recorder = Recorder()

    class ImmediateThread:
        def __init__(self, *, target: Callable[[], None], **_kwargs: object) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr("voiceflow.app.threading.Thread", ImmediateThread)

    app.quit()

    dynamic_events = [
        event for event in events if event not in {"close:services", "stop:event-loop"}
    ]
    assert dynamic_events == expected
    assert events[-2:] == ["close:services", "stop:event-loop"]


def test_finish_quit_keeps_postprocessor_owned_by_services() -> None:
    calls: list[object] = []
    app = VoiceBudApplication.__new__(VoiceBudApplication)
    app.pipeline = SimpleNamespace(shutdown=lambda **kwargs: calls.append(kwargs))
    app.services = SimpleNamespace(close=lambda: calls.append("services.close"))
    helper = FakeHelper()
    app._app_helper = helper

    app._finish_quit()

    assert calls == [{"close_postprocessor": False}, "services.close"]
    assert helper.calls == [(helper.stopEventLoop, ())]


def test_run_builds_the_controller_through_bootstrap(
    app_config: AppConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []
    helper = FakeHelper()
    controller = SimpleNamespace(
        services=SimpleNamespace(app_helper=helper),
        start=lambda: calls.append("start"),
    )
    native_app = SimpleNamespace(setActivationPolicy_=lambda value: calls.append(("policy", value)))
    monkeypatch.setattr("voiceflow.app.load_config", lambda _path: app_config)
    monkeypatch.setattr(
        "voiceflow.app._configure_logging",
        lambda _config: SimpleNamespace(close=lambda: calls.append("log.close")),
    )
    monkeypatch.setattr("voiceflow.app._single_instance_lock", lambda: nullcontext())
    monkeypatch.setattr("voiceflow.app._rename_process", lambda: calls.append("rename"))
    monkeypatch.setattr(
        "voiceflow.app._run_event_loop", lambda selected: calls.append(("event_loop", selected))
    )
    monkeypatch.setattr(
        "voiceflow.bootstrap.build_application",
        lambda config: calls.append(("build", config)) or controller,
    )
    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSApplication=SimpleNamespace(sharedApplication=lambda: native_app),
        ),
    )

    assert run() == 0
    assert ("build", app_config) in calls
    assert "start" in calls
    assert ("event_loop", helper) in calls
    assert calls[-1] == "log.close"
