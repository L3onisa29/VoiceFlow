from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voiceflow.state import ActivityState, AppState, ModelState, StateStore


@dataclass(frozen=True, slots=True)
class Presentation:
    icon: str
    text: str
    overlay_visible: bool
    waveform: bool = False


class StatusController:
    """The sole bridge from application state to AppKit views."""

    def __init__(
        self,
        menu_bar: Any,
        overlay: Any,
        *,
        postprocess_label: str,
        dispatch: Callable[..., None] | None = None,
        show_processing_overlay: bool = True,
        indicator_seconds: float = 3.0,
        completion_indicator_seconds: float = 0.8,
    ) -> None:
        if dispatch is None:
            from PyObjCTools import AppHelper

            dispatch = AppHelper.callAfter
        self.menu_bar = menu_bar
        self.overlay = overlay
        self.postprocess_label = postprocess_label
        self.dispatch = dispatch
        self.show_processing_overlay = show_processing_overlay
        self.indicator_seconds = indicator_seconds
        self.completion_indicator_seconds = completion_indicator_seconds
        self.store = StateStore()
        self._transient: Presentation | None = None
        self._lock = threading.RLock()
        self._transient_generation = 0

    @property
    def state(self) -> AppState:
        return self.store.value

    def update(self, **changes: object) -> None:
        state = self.store.update(**changes)
        self._schedule_render(state)

    def transient(
        self,
        text: str,
        *,
        error: bool = False,
        duration: float | None = None,
        icon: str | None = None,
    ) -> None:
        with self._lock:
            self._transient = Presentation(icon or ("🔴" if error else "🟠"), text, True)
            self._transient_generation += 1
            generation = self._transient_generation
        self._schedule_render(self.store.value)
        timer = threading.Timer(
            self.indicator_seconds if duration is None else duration,
            self._clear_generation,
            args=(generation,),
        )
        timer.daemon = True
        timer.start()

    def complete(self) -> None:
        self.transient(
            "Completato",
            icon="🟢",
            duration=self.completion_indicator_seconds,
        )

    def _clear_generation(self, generation: int) -> None:
        with self._lock:
            if generation != self._transient_generation:
                return
        self.clear_transient()

    def clear_transient(self) -> None:
        with self._lock:
            self._transient = None
            self._transient_generation += 1
        self._schedule_render(self.store.value)

    def _schedule_render(self, state: AppState) -> None:
        self.dispatch(self._render, state)

    def _render(self, state: AppState) -> None:
        with self._lock:
            presentation = self._transient or present(state, self.show_processing_overlay)
        self.menu_bar.update(
            icon=presentation.icon,
            text=presentation.text,
            model_path=state.model_path,
            postprocess=self.postprocess_label,
            queue_depth=state.queue_depth,
        )
        self.overlay.update(
            text=presentation.text,
            visible=presentation.overlay_visible,
            waveform=presentation.waveform,
        )


def present(state: AppState, show_processing_overlay: bool = True) -> Presentation:
    if state.model == ModelState.ERROR:
        return Presentation("🔴", state.message or "Errore caricamento modello", True)
    if state.activity == ActivityState.STOPPING:
        return Presentation("⚪", "Arresto…", False)
    if state.activity == ActivityState.RECORDING:
        return Presentation("🔴", "Registrazione…", True, waveform=True)
    if state.activity == ActivityState.QUEUED:
        ahead = max(0, state.queue_depth - 1)
        return Presentation("🟠", f"In attesa — {ahead} davanti", show_processing_overlay)
    if state.activity == ActivityState.TRANSCRIBING:
        return Presentation("🟣", "Trascrizione…", show_processing_overlay)
    if state.activity == ActivityState.POSTPROCESSING:
        return Presentation(
            "🔵", "Post-processing con Codex…", show_processing_overlay
        )
    if state.activity == ActivityState.INJECTING:
        return Presentation("🟣", "Inserimento testo…", show_processing_overlay)
    if state.model == ModelState.RESOLVING:
        return Presentation("⚪", "Ricerca modello Parakeet…", True)
    if state.model == ModelState.DOWNLOADING:
        return Presentation("🟠", "Download modello…", True)
    if state.model == ModelState.LOADING:
        return Presentation("🟠", "Modello in caricamento…", True)
    if state.model == ModelState.READY:
        return Presentation("🟢", "Pronto", False)
    return Presentation("⚪", "Inizializzazione…", True)
