from __future__ import annotations

from typing import Any

from voiceflow.state import ActivityState, AppState, ModelState
from voiceflow.ui.status_controller import StatusController, present


def test_presentations_cover_model_and_activity_states() -> None:
    assert present(AppState()).text == "Inizializzazione…"
    assert present(AppState(model=ModelState.RESOLVING)).text.startswith("Ricerca")
    assert present(AppState(model=ModelState.DOWNLOADING)).text.startswith("Download")
    assert present(AppState(model=ModelState.LOADING)).text.startswith("Modello")
    assert present(AppState(model=ModelState.READY)).text == "Pronto"
    assert present(AppState(model=ModelState.READY, activity=ActivityState.RECORDING)).waveform
    queued = present(
        AppState(model=ModelState.READY, activity=ActivityState.QUEUED, queue_depth=3)
    )
    assert queued.text == "In attesa — 2 davanti"
    assert present(AppState(activity=ActivityState.TRANSCRIBING)).text == "Trascrizione…"
    assert "Codex" in present(AppState(activity=ActivityState.POSTPROCESSING)).text
    assert present(AppState(activity=ActivityState.INJECTING)).text.startswith("Inserimento")


def test_model_error_has_visual_priority() -> None:
    shown = present(
        AppState(
            model=ModelState.ERROR,
            activity=ActivityState.RECORDING,
            message="broken",
        )
    )
    assert shown.text == "broken" and shown.icon == "🔴"


def test_controller_dispatches_all_ui_work() -> None:
    calls: list[tuple[Any, tuple[Any, ...]]] = []

    class View:
        def __init__(self) -> None:
            self.updates: list[dict[str, Any]] = []

        def update(self, **kwargs: Any) -> None:
            self.updates.append(kwargs)

    menu, overlay = View(), View()
    controller = StatusController(
        menu,
        overlay,
        postprocess_label="codex / luna",
        dispatch=lambda function, *args: calls.append((function, args)),
    )
    controller.update(model=ModelState.READY)
    assert not menu.updates and len(calls) == 1
    function, args = calls.pop()
    function(*args)
    assert menu.updates[0]["text"] == "Pronto"
    assert overlay.updates[0]["visible"] is False
