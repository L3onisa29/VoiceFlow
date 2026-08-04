from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from voiceflow.ui.status_controller import OverlayPresentation


@dataclass(frozen=True, slots=True)
class MenuActions:
    retry_input: Callable[[], None]
    retry_model: Callable[[], None]
    retry_postprocess: Callable[[], None]
    copy_last: Callable[[], None]
    copy_raw: Callable[[], None]
    correct_last: Callable[[], None]
    add_term: Callable[[], None]
    clear_history: Callable[[], None]
    deep_next: Callable[[], None]
    quit: Callable[[], None]


class MenuView(Protocol):
    def update(
        self,
        *,
        icon: str,
        text: str,
        model_path: str | None,
        provider: str,
        postprocess: str,
        postprocess_state: str,
        postprocess_detail: str | None,
        input_device: str | None,
        queue_depth: int,
    ) -> None: ...


class OverlayView(Protocol):
    def update(self, presentation: OverlayPresentation) -> None: ...

    def close(self) -> None: ...


class ModelNotificationView(Protocol):
    def show_ready(self, *, duration: float) -> None: ...

    def show_error(self, detail: str | None, *, duration: float) -> None: ...

    def close(self) -> None: ...


class ApplicationViewBundle(Protocol):
    menu: MenuView
    overlay: OverlayView
    model_notification: ModelNotificationView

    def close(self) -> None: ...
