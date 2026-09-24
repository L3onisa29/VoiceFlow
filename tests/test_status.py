from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import pytest

from voiceflow.state import ActivityState, AppState, ModelState, StateStore, SystemState
from voiceflow.ui.overlay import Overlay, content_layout_for_mode, geometry_for_mode
from voiceflow.ui.status_controller import (
    OverlayAccent,
    OverlayMode,
    StatusController,
    present,
)


@pytest.mark.parametrize(
    ("mode", "width", "bottom_offset"),
    [
        (OverlayMode.WAVEFORM, 99, 50),
        (OverlayMode.PROCESSING, 60.5, 50),
        (OverlayMode.SUCCESS, 30, 50),
        (OverlayMode.COPIED, 30, 50),
        (OverlayMode.CANCELLED, 30, 50),
        (OverlayMode.WARNING, 30, 50),
        (OverlayMode.FAILURE, 30, 50),
    ],
)
def test_overlay_geometry_adapts_to_content(
    mode: OverlayMode,
    width: float,
    bottom_offset: int,
) -> None:
    geometry = geometry_for_mode(mode)
    assert geometry.width == width
    assert geometry.height == 30
    assert geometry.bottom_offset == bottom_offset


def test_overlay_relocates_to_pointer_screen_without_duplicate_moves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def rect(x: float, y: float, width: float, height: float) -> Any:
        return SimpleNamespace(
            origin=SimpleNamespace(x=x, y=y),
            size=SimpleNamespace(width=width, height=height),
        )

    class Screen:
        def __init__(self, frame: Any) -> None:
            self._frame = frame

        def frame(self) -> Any:
            return self._frame

        def visibleFrame(self) -> Any:
            return self._frame

    screens = [Screen(rect(0, 0, 1920, 1080)), Screen(rect(1920, 0, 2560, 1440))]
    pointer = {"position": (100, 100)}

    def contains(position: tuple[float, float], frame: Any, _flipped: bool) -> bool:
        x, y = position
        return (
            frame.origin.x <= x < frame.origin.x + frame.size.width
            and frame.origin.y <= y < frame.origin.y + frame.size.height
        )

    monkeypatch.setitem(
        sys.modules,
        "AppKit",
        SimpleNamespace(
            NSEvent=SimpleNamespace(mouseLocation=lambda: pointer["position"]),
            NSMouseInRect=contains,
            NSScreen=SimpleNamespace(screens=lambda: screens, mainScreen=lambda: screens[0]),
        ),
    )
    moves: list[Any] = []
    overlay = Overlay.__new__(Overlay)
    overlay._geometry = geometry_for_mode(OverlayMode.WAVEFORM)
    overlay._placement = None
    overlay.panel = SimpleNamespace(
        setFrame_display_=lambda frame, display: moves.append((frame, display))
    )

    overlay._place_on_pointer_screen()
    overlay._place_on_pointer_screen()
    pointer["position"] = (2000, 100)
    overlay._place_on_pointer_screen()

    assert moves == [
        ((((1920 - 99) / 2, 50), (99, 30)), True),
        (((1920 + (2560 - 99) / 2, 50), (99, 30)), True),
    ]


def test_cancelled_outcome_draws_green_stop_sign_geometry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[tuple[Any, ...]] = []
    path = object()
    monkeypatch.setitem(
        sys.modules,
        "Quartz",
        SimpleNamespace(
            CATransaction=SimpleNamespace(
                begin=lambda: None, commit=lambda: None, setDisableActions_=lambda _value: None
            ),
            CGPathCreateMutable=lambda: path,
            CGPathMoveToPoint=lambda actual, _transform, x, y: commands.append(
                ("move", actual, x, y)
            ),
            CGPathAddLineToPoint=lambda actual, _transform, x, y: commands.append(
                ("line", actual, x, y)
            ),
            CGPathCloseSubpath=lambda actual: commands.append(("close", actual)),
        ),
    )
    outcome = SimpleNamespace(
        setLineWidth_=lambda width: commands.append(("width", width)),
        setPath_=lambda actual: commands.append(("path", actual)),
    )
    overlay = Overlay.__new__(Overlay)
    overlay._geometry = geometry_for_mode(OverlayMode.CANCELLED)
    overlay.outcome_layer = outcome

    overlay._set_outcome_path(OverlayMode.CANCELLED)

    assert commands[0] == ("move", path, 11.0, 5.5)
    assert ("close", path) in commands
    assert ("width", 1.2) in commands
    assert commands[-1] == ("path", path)


@pytest.mark.parametrize(
    "mode",
    [OverlayMode.WAVEFORM, OverlayMode.PROCESSING],
)
def test_active_overlay_uses_twelve_pixel_horizontal_padding(
    mode: OverlayMode,
) -> None:
    content = content_layout_for_mode(mode)
    geometry = geometry_for_mode(mode)

    assert content.padding_x == 12
    assert not content.circular
    assert geometry.width == content.width + 24


@pytest.mark.parametrize(
    "mode",
    [
        OverlayMode.SUCCESS,
        OverlayMode.COPIED,
        OverlayMode.CANCELLED,
        OverlayMode.WARNING,
        OverlayMode.FAILURE,
    ],
)
def test_outcome_overlay_is_circular_without_shared_padding(
    mode: OverlayMode,
) -> None:
    content = content_layout_for_mode(mode)
    geometry = geometry_for_mode(mode)

    assert content.padding_x == 0
    assert content.circular
    assert geometry.width == geometry.height


@pytest.mark.parametrize(
    ("state", "mode", "accent", "visible"),
    [
        (AppState(), OverlayMode.HIDDEN, OverlayAccent.AMBER, False),
        (
            AppState(model=ModelState.RESOLVING),
            OverlayMode.HIDDEN,
            OverlayAccent.AMBER,
            False,
        ),
        (
            AppState(model=ModelState.DOWNLOADING),
            OverlayMode.HIDDEN,
            OverlayAccent.AMBER,
            False,
        ),
        (
            AppState(model=ModelState.LOADING),
            OverlayMode.HIDDEN,
            OverlayAccent.AMBER,
            False,
        ),
        (
            AppState(model=ModelState.READY, recording=True),
            OverlayMode.WAVEFORM,
            OverlayAccent.CORAL,
            True,
        ),
        (
            AppState(activity=ActivityState.TRANSCRIBING),
            OverlayMode.PROCESSING,
            OverlayAccent.PURPLE,
            True,
        ),
        (
            AppState(activity=ActivityState.POSTPROCESSING),
            OverlayMode.PROCESSING,
            OverlayAccent.CYAN,
            True,
        ),
        (
            AppState(activity=ActivityState.INJECTING),
            OverlayMode.PROCESSING,
            OverlayAccent.CYAN,
            True,
        ),
        (
            AppState(model=ModelState.READY),
            OverlayMode.HIDDEN,
            OverlayAccent.AMBER,
            False,
        ),
    ],
)
def test_states_produce_icon_only_overlay_presentations(
    state: AppState,
    mode: OverlayMode,
    accent: OverlayAccent,
    visible: bool,
) -> None:
    overlay = present(state).overlay
    assert overlay.mode == mode
    assert overlay.accent == accent
    assert overlay.visible is visible
    assert not hasattr(overlay, "text")


def test_processing_overlay_can_be_disabled_without_hiding_waveform() -> None:
    processing = present(
        AppState(activity=ActivityState.TRANSCRIBING),
        show_processing_overlay=False,
    ).overlay
    recording = present(
        AppState(model=ModelState.READY, recording=True),
        show_processing_overlay=False,
    ).overlay
    assert processing.mode == OverlayMode.HIDDEN and not processing.visible
    assert recording.mode == OverlayMode.WAVEFORM and recording.visible


def test_menu_presentations_preserve_diagnostics() -> None:
    assert present(AppState()).menu.text == "Inizializzazione…"
    assert present(AppState(model=ModelState.RESOLVING)).menu.text.startswith("Ricerca")
    assert present(AppState(model=ModelState.DOWNLOADING)).menu.text.startswith("Download")
    assert present(AppState(model=ModelState.LOADING)).menu.text.startswith("Modello")
    assert present(AppState(model=ModelState.READY)).menu.text == "Pronto"
    queued = present(AppState(model=ModelState.READY, activity=ActivityState.QUEUED, queue_depth=3))
    assert queued.menu.text == "In attesa — 2 davanti"
    assert present(AppState(activity=ActivityState.TRANSCRIBING)).menu.text == "Trascrizione…"
    assert "Post-processing" in present(AppState(activity=ActivityState.POSTPROCESSING)).menu.text
    assert present(AppState(activity=ActivityState.INJECTING)).menu.text.startswith("Inserimento")


def test_blocking_errors_have_visual_priority() -> None:
    model_error = present(
        AppState(
            model=ModelState.ERROR,
            recording=True,
            model_message="broken",
        )
    )
    system_error = present(
        AppState(
            system=SystemState.ERROR,
            model=ModelState.READY,
            system_message="missing permissions",
        )
    )
    assert model_error.menu.text == "broken"
    assert model_error.overlay.mode == OverlayMode.HIDDEN
    assert system_error.menu.text == "missing permissions"
    assert system_error.overlay.accent == OverlayAccent.RED


class MenuView:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []

    def update(self, **kwargs: Any) -> None:
        self.updates.append(kwargs)


class OverlayView:
    def __init__(self) -> None:
        self.updates: list[Any] = []

    def update(self, presentation: Any) -> None:
        self.updates.append(presentation)


class ModelNotificationView:
    def __init__(self) -> None:
        self.ready_durations: list[float] = []
        self.errors: list[tuple[str | None, float]] = []
        self.closed = False

    def show_ready(self, *, duration: float) -> None:
        self.ready_durations.append(duration)

    def show_error(self, detail: str | None, *, duration: float) -> None:
        self.errors.append((detail, duration))

    def close(self) -> None:
        self.closed = True


def test_model_notification_replaces_loading_overlay() -> None:
    menu, overlay = MenuView(), OverlayView()
    notification = ModelNotificationView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        model_notification=notification,
        dispatch=lambda function, *args: function(*args),
        error_indicator_seconds=4,
        ready_notification_seconds=3,
    )

    controller.update(system=SystemState.READY, model=ModelState.LOADING)
    assert overlay.updates[-1].mode == OverlayMode.HIDDEN
    assert notification.ready_durations == []

    controller.update(model=ModelState.READY)
    assert notification.ready_durations == []
    controller.update(input_device="MacBook Microphone")
    assert notification.ready_durations == [3]
    controller.update(input_device="MacBook Microphone")
    assert notification.ready_durations == [3]

    controller.update(model=ModelState.ERROR, model_message="model file missing")
    assert notification.errors == [("model file missing", 4)]
    assert overlay.updates[-1].mode == OverlayMode.HIDDEN
    controller.close()
    assert not notification.closed


def test_identical_updates_do_not_schedule_duplicate_render() -> None:
    calls: list[tuple[Any, tuple[Any, ...]]] = []
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="codex / luna",
        dispatch=lambda function, *args: calls.append((function, args)),
    )

    controller.update(model=ModelState.READY)
    controller.update(model=ModelState.READY)

    assert len(calls) == 1
    function, args = calls.pop()
    function(*args)
    assert menu.updates[0]["text"] == "Pronto"
    assert overlay.updates[0].visible is False


def test_rapid_updates_are_coalesced_and_render_latest_state() -> None:
    calls: list[tuple[Any, tuple[Any, ...]]] = []
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        dispatch=lambda function, *args: calls.append((function, args)),
    )

    controller.update(model=ModelState.LOADING)
    controller.update(model=ModelState.READY)
    controller.update(recording=True)

    assert len(calls) == 1
    function, args = calls.pop()
    function(*args)
    assert menu.updates == [
        {
            "icon": "🔴",
            "text": "Registrazione…",
            "model_path": None,
            "provider": "CPUExecutionProvider",
            "postprocess": "none",
            "postprocess_state": "checking",
            "postprocess_detail": None,
            "input_device": None,
            "queue_depth": 0,
        }
    ]
    assert overlay.updates[0].mode == OverlayMode.WAVEFORM


def test_menu_and_overlay_are_deduplicated_independently() -> None:
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        dispatch=lambda function, *args: function(*args),
    )
    controller.update(model=ModelState.READY)
    controller.update(model_path="/models/parakeet")
    assert len(menu.updates) == 2
    assert len(overlay.updates) == 1


def test_success_copy_fallback_and_failure_use_outcome_icons() -> None:
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="codex",
        dispatch=lambda function, *args: function(*args),
        indicator_seconds=60,
        completion_indicator_seconds=60,
    )

    controller.complete()
    assert overlay.updates[-1].mode == OverlayMode.SUCCESS
    assert overlay.updates[-1].accent == OverlayAccent.GREEN
    controller.copied()
    assert menu.updates[-1]["icon"] == "⧉"
    assert menu.updates[-1]["text"] == "Testo copiato negli appunti"
    assert overlay.updates[-1].mode == OverlayMode.COPIED
    assert overlay.updates[-1].accent == OverlayAccent.CYAN
    controller.cancelled()
    assert menu.updates[-1]["icon"] == "🟢"
    assert menu.updates[-1]["text"] == "Dettatura annullata"
    assert overlay.updates[-1].mode == OverlayMode.CANCELLED
    assert overlay.updates[-1].accent == OverlayAccent.GREEN
    controller.complete(fallback=True, detail="usato testo originale")
    assert overlay.updates[-1].mode == OverlayMode.WARNING
    assert overlay.updates[-1].accent == OverlayAccent.AMBER
    controller.transient("inserimento fallito", error=True)
    assert overlay.updates[-1].mode == OverlayMode.FAILURE
    assert overlay.updates[-1].accent == OverlayAccent.RED
    controller.close()


def test_new_recording_overrides_old_transient() -> None:
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        dispatch=lambda function, *args: function(*args),
        indicator_seconds=60,
    )
    controller.transient("old error", error=True)
    controller.update(recording=True)
    assert overlay.updates[-1].mode == OverlayMode.WAVEFORM
    controller.close()


def test_stopping_overrides_transient_and_close_rejects_late_renders() -> None:
    calls: list[tuple[Any, tuple[Any, ...]]] = []
    menu, overlay = MenuView(), OverlayView()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="none",
        dispatch=lambda function, *args: calls.append((function, args)),
        indicator_seconds=60,
    )
    controller.transient("old error", error=True)
    controller.update(activity=ActivityState.STOPPING)
    function, args = calls.pop()
    function(*args)
    assert overlay.updates[-1].visible is False
    assert menu.updates[-1]["text"] == "Arresto…"

    update_count = len(overlay.updates)
    controller.update(model=ModelState.READY)
    controller.close()
    function, args = calls.pop()
    function(*args)
    controller.transient("late")
    assert len(overlay.updates) == update_count


def test_state_store_reports_identical_update_without_replacing_object() -> None:
    store = StateStore()
    updated, changed = store.update_if_changed(model=ModelState.READY)
    assert changed and updated is store.value
    same, changed = store.update_if_changed(model=ModelState.READY)
    assert not changed and same is updated
