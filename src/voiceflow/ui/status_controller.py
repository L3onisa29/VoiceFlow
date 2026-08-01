from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Any

from voiceflow.state import (
    ActivityState,
    AppState,
    ModelState,
    PostprocessState,
    StateStore,
    SystemState,
)


class OverlayMode(StrEnum):
    HIDDEN = "hidden"
    WAVEFORM = "waveform"
    PROCESSING = "processing"
    SUCCESS = "success"
    WARNING = "warning"
    FAILURE = "failure"


class OverlayAccent(StrEnum):
    CORAL = "coral"
    AMBER = "amber"
    PURPLE = "purple"
    CYAN = "cyan"
    GREEN = "green"
    RED = "red"


@dataclass(frozen=True, slots=True)
class MenuPresentation:
    icon: str
    text: str
    model_path: str | None = None
    provider: str = ""
    postprocess: str = ""
    postprocess_state: str = ""
    postprocess_detail: str | None = None
    input_device: str | None = None
    queue_depth: int = 0


@dataclass(frozen=True, slots=True)
class OverlayPresentation:
    mode: OverlayMode
    accent: OverlayAccent
    visible: bool


@dataclass(frozen=True, slots=True)
class Presentation:
    menu: MenuPresentation
    overlay: OverlayPresentation


@dataclass(frozen=True, slots=True)
class ModelNotice:
    error: bool
    detail: str | None = None


HIDDEN_OVERLAY = OverlayPresentation(
    OverlayMode.HIDDEN,
    OverlayAccent.AMBER,
    False,
)


class StatusController:
    """The sole bridge from application state to AppKit views."""

    def __init__(
        self,
        menu_bar: Any,
        overlay: Any,
        *,
        postprocess_label: str,
        model_notification: Any | None = None,
        stt_provider: str = "CPUExecutionProvider",
        dispatch: Callable[..., None] | None = None,
        show_processing_overlay: bool = True,
        indicator_seconds: float | None = None,
        error_indicator_seconds: float = 3.0,
        completion_indicator_seconds: float = 0.8,
        ready_notification_seconds: float = 3.0,
    ) -> None:
        if dispatch is None:
            from PyObjCTools import AppHelper

            dispatch = AppHelper.callAfter
        self.menu_bar = menu_bar
        self.overlay = overlay
        self.model_notification = model_notification
        self.postprocess_label = postprocess_label
        self.stt_provider = stt_provider
        self.dispatch = dispatch
        self.show_processing_overlay = show_processing_overlay
        self.error_indicator_seconds = (
            error_indicator_seconds if indicator_seconds is None else indicator_seconds
        )
        self.completion_indicator_seconds = completion_indicator_seconds
        self.ready_notification_seconds = ready_notification_seconds
        self.store = StateStore()
        self._transient: Presentation | None = None
        self._lock = threading.RLock()
        self._transient_generation = 0
        self._timers: set[threading.Timer] = set()
        self._render_pending = False
        self._last_menu: MenuPresentation | None = None
        self._last_overlay: OverlayPresentation | None = None
        self._pending_model_notice: ModelNotice | None = None
        self._app_was_ready = _app_is_ready(self.store.value)
        self._closed = False

    @property
    def state(self) -> AppState:
        return self.store.value

    def update(self, **changes: object) -> None:
        with self._lock:
            if self._closed:
                return
            previous = self.store.value
            state, changed = self.store.update_if_changed(**changes)
            if not changed:
                return
            self._queue_model_notice(previous, state)
            self._schedule_render_locked()

    def _queue_model_notice(self, previous: AppState, state: AppState) -> None:
        is_ready = _app_is_ready(state)
        if state.model == ModelState.ERROR and previous.model != ModelState.ERROR:
            self._pending_model_notice = ModelNotice(True, state.model_message)
        elif is_ready and not self._app_was_ready:
            self._pending_model_notice = ModelNotice(False)
        self._app_was_ready = is_ready

    def transient(
        self,
        text: str,
        *,
        error: bool = False,
        duration: float | None = None,
        icon: str | None = None,
    ) -> None:
        mode = OverlayMode.FAILURE if error else OverlayMode.WARNING
        accent = OverlayAccent.RED if error else OverlayAccent.AMBER
        menu_icon = icon or ("🔴" if error else "🟠")
        with self._lock:
            if self._closed:
                return
            candidate = Presentation(
                MenuPresentation(menu_icon, text),
                OverlayPresentation(mode, accent, True),
            )
            if candidate == self._transient:
                return
            self._transient = candidate
            self._transient_generation += 1
            generation = self._transient_generation
            self._schedule_render_locked()
        self._start_transient_timer(
            generation,
            self.error_indicator_seconds if duration is None else duration,
        )

    def complete(self, *, fallback: bool = False, detail: str | None = None) -> None:
        if fallback:
            text = detail or "Completato — usato testo originale"
            icon = "🟠"
            mode = OverlayMode.WARNING
            accent = OverlayAccent.AMBER
        else:
            text = detail or "Completato"
            icon = "🟢"
            mode = OverlayMode.SUCCESS
            accent = OverlayAccent.GREEN
        self._set_outcome(
            text=text,
            icon=icon,
            mode=mode,
            accent=accent,
            duration=self.completion_indicator_seconds,
        )

    def _set_outcome(
        self,
        *,
        text: str,
        icon: str,
        mode: OverlayMode,
        accent: OverlayAccent,
        duration: float,
    ) -> None:
        with self._lock:
            if self._closed:
                return
            self._transient = Presentation(
                MenuPresentation(icon, text),
                OverlayPresentation(mode, accent, True),
            )
            self._transient_generation += 1
            generation = self._transient_generation
            self._schedule_render_locked()
        self._start_transient_timer(generation, duration)

    def _start_transient_timer(self, generation: int, duration: float) -> None:
        timer: threading.Timer

        def clear() -> None:
            try:
                self._clear_generation(generation)
            finally:
                with self._lock:
                    self._timers.discard(timer)

        timer = threading.Timer(duration, clear)
        timer.daemon = True
        with self._lock:
            if self._closed:
                return
            self._timers.add(timer)
        timer.start()

    def _clear_generation(self, generation: int) -> None:
        with self._lock:
            if self._closed or generation != self._transient_generation:
                return
            self._transient = None
            self._transient_generation += 1
            self._schedule_render_locked()

    def _schedule_render_locked(self) -> None:
        if self._closed or self._render_pending:
            return
        self._render_pending = True
        self.dispatch(self._render)

    def _render(self) -> None:
        with self._lock:
            self._render_pending = False
            if self._closed:
                return
            state = self.store.value
            presentation = self._presentation_for(state)
            model_notice = self._pending_model_notice
            self._pending_model_notice = None
            menu_changed = presentation.menu != self._last_menu
            overlay_changed = presentation.overlay != self._last_overlay
            if menu_changed:
                self._last_menu = presentation.menu
            if overlay_changed:
                self._last_overlay = presentation.overlay
            if menu_changed:
                self.menu_bar.update(
                    icon=presentation.menu.icon,
                    text=presentation.menu.text,
                    model_path=presentation.menu.model_path,
                    provider=presentation.menu.provider,
                    postprocess=presentation.menu.postprocess,
                    postprocess_state=presentation.menu.postprocess_state,
                    postprocess_detail=presentation.menu.postprocess_detail,
                    input_device=presentation.menu.input_device,
                    queue_depth=presentation.menu.queue_depth,
                )
            if overlay_changed:
                self.overlay.update(presentation.overlay)
            if model_notice is not None and self.model_notification is not None:
                if model_notice.error:
                    self.model_notification.show_error(
                        model_notice.detail,
                        duration=self.error_indicator_seconds,
                    )
                else:
                    self.model_notification.show_ready(
                        duration=self.ready_notification_seconds,
                    )

    def _presentation_for(self, state: AppState) -> Presentation:
        base = present(
            state,
            self.show_processing_overlay,
            provider=self.stt_provider,
            postprocess_label=self.postprocess_label,
        )
        if _state_has_priority_over_transient(state) or self._transient is None:
            return base
        return Presentation(
            replace(
                base.menu,
                icon=self._transient.menu.icon,
                text=self._transient.menu.text,
            ),
            self._transient.overlay,
        )

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._render_pending = False
            self._transient = None
            self._pending_model_notice = None
            self._transient_generation += 1
            timers = tuple(self._timers)
            self._timers.clear()
            model_notification = self.model_notification
        for timer in timers:
            timer.cancel()
        if model_notification is not None:
            model_notification.close()


def _state_has_priority_over_transient(state: AppState) -> bool:
    return (
        state.activity == ActivityState.STOPPING
        or state.system == SystemState.ERROR
        or state.model == ModelState.ERROR
        or state.recording
        or state.activity != ActivityState.IDLE
    )


def _app_is_ready(state: AppState) -> bool:
    return (
        state.system == SystemState.READY
        and state.model == ModelState.READY
        and state.input_device is not None
    )


def present(
    state: AppState,
    show_processing_overlay: bool = True,
    *,
    provider: str = "",
    postprocess_label: str = "",
) -> Presentation:
    menu = _menu_presentation(state)
    menu = replace(
        menu,
        model_path=state.model_path,
        provider=provider,
        postprocess=postprocess_label,
        postprocess_state=state.postprocess.value,
        postprocess_detail=state.postprocess_message,
        input_device=state.input_device,
        queue_depth=state.queue_depth,
    )
    overlay = _overlay_presentation(state, show_processing_overlay)
    return Presentation(menu, overlay)


def _menu_presentation(state: AppState) -> MenuPresentation:
    if state.activity == ActivityState.STOPPING:
        return MenuPresentation("⚪", "Arresto…")
    if state.system == SystemState.ERROR:
        return MenuPresentation("🔴", state.system_message or "Errore di sistema")
    if state.model == ModelState.ERROR:
        return MenuPresentation("🔴", state.model_message or "Errore caricamento modello")
    if state.recording:
        return MenuPresentation("🔴", "Registrazione…")
    if state.activity == ActivityState.QUEUED:
        ahead = max(0, state.queue_depth - 1)
        return MenuPresentation("🟠", f"In attesa — {ahead} davanti")
    if state.activity == ActivityState.TRANSCRIBING:
        return MenuPresentation("🟣", "Trascrizione…")
    if state.activity == ActivityState.POSTPROCESSING:
        return MenuPresentation("🔵", "Post-processing…")
    if state.activity == ActivityState.INJECTING:
        return MenuPresentation("🔵", "Inserimento testo…")
    if state.model == ModelState.RESOLVING:
        return MenuPresentation("⚪", "Ricerca modello Parakeet…")
    if state.model == ModelState.DOWNLOADING:
        return MenuPresentation("🟠", "Download modello…")
    if state.model == ModelState.LOADING:
        return MenuPresentation("🟠", "Modello in caricamento…")
    if state.model == ModelState.READY:
        if state.postprocess in {PostprocessState.ERROR, PostprocessState.DEGRADED}:
            detail = state.postprocess_message or "testo originale come fallback"
            return MenuPresentation("🟠", f"Pronto — {detail}")
        return MenuPresentation("🟢", "Pronto")
    return MenuPresentation("⚪", "Inizializzazione…")


def _overlay_presentation(
    state: AppState,
    show_processing_overlay: bool,
) -> OverlayPresentation:
    if state.activity == ActivityState.STOPPING:
        return HIDDEN_OVERLAY
    if state.system == SystemState.ERROR:
        return OverlayPresentation(
            OverlayMode.FAILURE,
            OverlayAccent.RED,
            True,
        )
    if state.model == ModelState.ERROR:
        return HIDDEN_OVERLAY
    if state.recording:
        return OverlayPresentation(
            OverlayMode.WAVEFORM,
            OverlayAccent.CORAL,
            True,
        )
    if state.activity == ActivityState.TRANSCRIBING:
        return _processing_overlay(OverlayAccent.PURPLE, show_processing_overlay)
    if state.activity in {ActivityState.POSTPROCESSING, ActivityState.INJECTING}:
        return _processing_overlay(OverlayAccent.CYAN, show_processing_overlay)
    if state.activity == ActivityState.QUEUED:
        return _processing_overlay(OverlayAccent.AMBER, show_processing_overlay)
    return HIDDEN_OVERLAY


def _processing_overlay(
    accent: OverlayAccent,
    visible: bool,
) -> OverlayPresentation:
    return OverlayPresentation(
        OverlayMode.PROCESSING if visible else OverlayMode.HIDDEN,
        accent,
        visible,
    )
