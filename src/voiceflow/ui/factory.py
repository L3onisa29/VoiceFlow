from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field

from voiceflow.ui.contracts import (
    ApplicationViewBundle,
    MenuActions,
    MenuView,
    ModelNotificationView,
    OverlayView,
)

MenuFactory = Callable[..., MenuView]
OverlayFactory = Callable[[Callable[[], float]], OverlayView]
ModelNotificationFactory = Callable[[], ModelNotificationView]


@dataclass(slots=True)
class _NativeApplicationViewBundle:
    menu: MenuView
    overlay: OverlayView
    model_notification: ModelNotificationView
    _closed: bool = field(default=False, init=False)
    _close_lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def close(self) -> None:
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.model_notification.close()
        finally:
            self.overlay.close()


class NativeViewFactory:
    """Lazily constructs the AppKit-backed application views."""

    def __init__(
        self,
        *,
        menu_factory: MenuFactory | None = None,
        overlay_factory: OverlayFactory | None = None,
        model_notification_factory: ModelNotificationFactory | None = None,
    ) -> None:
        self._menu_factory = menu_factory
        self._overlay_factory = overlay_factory
        self._model_notification_factory = model_notification_factory

    def create(
        self,
        actions: MenuActions,
        *,
        level_fn: Callable[[], float],
    ) -> ApplicationViewBundle:
        menu_factory = self._menu_factory
        if menu_factory is None:
            from voiceflow.ui.menu_bar import MenuBar

            menu_factory = MenuBar
        overlay_factory = self._overlay_factory
        if overlay_factory is None:
            from voiceflow.ui.overlay import Overlay

            overlay_factory = Overlay
        model_notification_factory = self._model_notification_factory
        if model_notification_factory is None:
            from voiceflow.ui.model_notification import ModelNotification

            model_notification_factory = ModelNotification

        menu = menu_factory(
            on_retry_input=actions.retry_input,
            on_retry_model=actions.retry_model,
            on_retry_postprocess=actions.retry_postprocess,
            on_copy_last=actions.copy_last,
            on_copy_raw=actions.copy_raw,
            on_correct_last=actions.correct_last,
            on_add_term=actions.add_term,
            on_clear_history=actions.clear_history,
            on_deep_next=actions.deep_next,
            on_quit=actions.quit,
        )
        overlay = overlay_factory(level_fn)
        try:
            model_notification = model_notification_factory()
        except BaseException:
            with suppress(BaseException):
                overlay.close()
            raise
        return _NativeApplicationViewBundle(menu, overlay, model_notification)
