from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import pytest

from voiceflow.state import ModelState
from voiceflow.ui.contracts import MenuActions
from voiceflow.ui.factory import NativeViewFactory
from voiceflow.ui.status_controller import StatusController


def _actions(calls: list[str]) -> MenuActions:
    def record(name: str) -> Callable[[], None]:
        return lambda: calls.append(name)

    return MenuActions(
        retry_input=record("retry_input"),
        retry_model=record("retry_model"),
        retry_postprocess=record("retry_postprocess"),
        copy_last=record("copy_last"),
        copy_raw=record("copy_raw"),
        correct_last=record("correct_last"),
        add_term=record("add_term"),
        clear_history=record("clear_history"),
        deep_next=record("deep_next"),
        quit=record("quit"),
    )


class FakeMenu:
    def __init__(self, **callbacks: Callable[[], None]) -> None:
        self.callbacks = callbacks
        self.updates: list[dict[str, object]] = []

    def update(self, **values: object) -> None:
        self.updates.append(values)


class FakeOverlay:
    def __init__(self, level_fn: Callable[[], float]) -> None:
        self.level_fn = level_fn
        self.updates: list[object] = []
        self.close_calls = 0

    def update(self, presentation: object) -> None:
        self.updates.append(presentation)

    def close(self) -> None:
        self.close_calls += 1


class FakeNotification:
    def __init__(self) -> None:
        self.ready: list[float] = []
        self.errors: list[tuple[str | None, float]] = []
        self.close_calls = 0

    def show_ready(self, *, duration: float) -> None:
        self.ready.append(duration)

    def show_error(self, detail: str | None, *, duration: float) -> None:
        self.errors.append((detail, duration))

    def close(self) -> None:
        self.close_calls += 1


def test_native_factory_wires_every_menu_action_and_bundle_closes_once() -> None:
    calls: list[str] = []
    factory = NativeViewFactory(
        menu_factory=FakeMenu,
        overlay_factory=FakeOverlay,
        model_notification_factory=FakeNotification,
    )

    bundle = factory.create(_actions(calls), level_fn=lambda: 0.25)

    expected = [
        "retry_input",
        "retry_model",
        "retry_postprocess",
        "copy_last",
        "copy_raw",
        "correct_last",
        "add_term",
        "clear_history",
        "deep_next",
        "quit",
    ]
    callback_names = [
        "on_retry_input",
        "on_retry_model",
        "on_retry_postprocess",
        "on_copy_last",
        "on_copy_raw",
        "on_correct_last",
        "on_add_term",
        "on_clear_history",
        "on_deep_next",
        "on_quit",
    ]
    assert isinstance(bundle.menu, FakeMenu)
    for name in callback_names:
        bundle.menu.callbacks[name]()
    assert calls == expected
    assert bundle.overlay.level_fn() == 0.25

    bundle.close()
    bundle.close()
    assert bundle.overlay.close_calls == 1
    assert bundle.model_notification.close_calls == 1


@pytest.mark.parametrize("close_raises", [False, True])
def test_native_factory_closes_overlay_when_notification_construction_fails(
    close_raises: bool,
) -> None:
    original_error = RuntimeError("notification construction failed")
    overlays: list[FakeOverlay] = []

    class FailingCloseOverlay(FakeOverlay):
        def close(self) -> None:
            super().close()
            if close_raises:
                raise OSError("overlay close failed")

    def overlay_factory(level_fn: Callable[[], float]) -> FakeOverlay:
        overlay = FailingCloseOverlay(level_fn)
        overlays.append(overlay)
        return overlay

    def notification_factory() -> FakeNotification:
        raise original_error

    factory = NativeViewFactory(
        menu_factory=FakeMenu,
        overlay_factory=overlay_factory,
        model_notification_factory=notification_factory,
    )

    with pytest.raises(RuntimeError) as caught:
        factory.create(_actions([]), level_fn=lambda: 0.0)

    assert caught.value is original_error
    assert len(overlays) == 1
    assert overlays[0].close_calls == 1


def test_factory_module_import_does_not_initialize_native_ui() -> None:
    code = """
import sys
import voiceflow.ui.factory
assert "voiceflow.ui.menu_bar" not in sys.modules
assert "voiceflow.ui.overlay" not in sys.modules
assert "voiceflow.ui.model_notification" not in sys.modules
assert "AppKit" not in sys.modules
assert "Quartz" not in sys.modules
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr


def test_status_controller_exercises_typed_views_without_duplicate_renders() -> None:
    menu = FakeMenu()
    overlay = FakeOverlay(lambda: 0.0)
    notification = FakeNotification()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        model_notification=notification,
        dispatch=lambda function, *args: function(*args),
    )

    controller.update(model=ModelState.LOADING)
    controller.update(model=ModelState.LOADING)
    controller.update(model=ModelState.ERROR, model_message="missing")

    assert len(menu.updates) == 2
    assert len(overlay.updates) == 1
    assert notification.errors == [("missing", 3.0)]
    controller.close()
    controller.close()
    assert notification.close_calls == 1


def test_stale_transient_timer_cannot_clear_newer_outcome(
    monkeypatch: Any,
) -> None:
    timers: list[Any] = []

    class Timer:
        daemon = False

        def __init__(self, duration: float, callback: Callable[[], None]) -> None:
            self.duration = duration
            self.callback = callback
            self.cancelled = False
            timers.append(self)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            self.cancelled = True

    monkeypatch.setattr("voiceflow.ui.status_controller.threading.Timer", Timer)
    menu = FakeMenu()
    overlay = FakeOverlay(lambda: 0.0)
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        dispatch=lambda function, *args: function(*args),
    )

    controller.transient("first")
    controller.complete(detail="second")
    timers[0].callback()

    assert menu.updates[-1]["text"] == "second"
    controller.close()
    controller.close()
    assert [timer.duration for timer in timers] == [3.0, 0.8]
    assert timers[1].cancelled


def test_success_warning_and_error_timers_are_cancelled_on_close(
    monkeypatch: Any,
) -> None:
    timers: list[Any] = []

    class Timer:
        daemon = False

        def __init__(self, duration: float, _callback: Callable[[], None]) -> None:
            self.duration = duration
            self.cancelled = False
            timers.append(self)

        def start(self) -> None:
            pass

        def cancel(self) -> None:
            self.cancelled = True

    monkeypatch.setattr("voiceflow.ui.status_controller.threading.Timer", Timer)
    controller = StatusController(
        FakeMenu(),
        FakeOverlay(lambda: 0.0),
        postprocess_label="none",
        dispatch=lambda function, *args: function(*args),
        error_indicator_seconds=4.0,
        completion_indicator_seconds=0.5,
    )

    controller.complete()
    controller.transient("warning")
    controller.transient("error", error=True)
    controller.close()

    assert [timer.duration for timer in timers] == [0.5, 4.0, 4.0]
    assert all(timer.cancelled for timer in timers)


def test_native_factory_loads_concrete_views_only_when_create_is_called(
    monkeypatch: Any,
) -> None:
    calls: list[str] = []
    menu_module = SimpleNamespace(
        MenuBar=lambda **kwargs: (calls.append("menu"), FakeMenu(**kwargs))[1]
    )
    overlay_module = SimpleNamespace(
        Overlay=lambda level_fn: (calls.append("overlay"), FakeOverlay(level_fn))[1]
    )
    notice_module = SimpleNamespace(
        ModelNotification=lambda: (calls.append("notification"), FakeNotification())[1]
    )
    monkeypatch.setitem(sys.modules, "voiceflow.ui.menu_bar", menu_module)
    monkeypatch.setitem(sys.modules, "voiceflow.ui.overlay", overlay_module)
    monkeypatch.setitem(sys.modules, "voiceflow.ui.model_notification", notice_module)

    bundle = NativeViewFactory().create(_actions([]), level_fn=lambda: 0.0)

    assert calls == ["menu", "overlay", "notification"]
    bundle.close()
