from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum, auto
from threading import RLock


class ModelState(StrEnum):
    NOT_LOADED = auto()
    RESOLVING = auto()
    DOWNLOADING = auto()
    LOADING = auto()
    READY = auto()
    ERROR = auto()


class ActivityState(StrEnum):
    IDLE = auto()
    QUEUED = auto()
    TRANSCRIBING = auto()
    POSTPROCESSING = auto()
    INJECTING = auto()
    STOPPING = auto()


class PostprocessState(StrEnum):
    CHECKING = auto()
    READY = auto()
    RUNNING = auto()
    DEGRADED = auto()
    ERROR = auto()


class SystemState(StrEnum):
    CHECKING = auto()
    READY = auto()
    ERROR = auto()


@dataclass(frozen=True, slots=True)
class AppState:
    system: SystemState = SystemState.CHECKING
    model: ModelState = ModelState.NOT_LOADED
    activity: ActivityState = ActivityState.IDLE
    recording: bool = False
    postprocess: PostprocessState = PostprocessState.CHECKING
    system_message: str | None = None
    model_message: str | None = None
    postprocess_message: str | None = None
    model_path: str | None = None
    input_device: str | None = None
    queue_depth: int = 0


class StateStore:
    """Thread-safe state store. Rendering is delegated to StatusController."""

    def __init__(self) -> None:
        self._state = AppState()
        self._lock = RLock()

    @property
    def value(self) -> AppState:
        with self._lock:
            return self._state

    def update_if_changed(self, **changes: object) -> tuple[AppState, bool]:
        """Apply changes and report whether they produced a new state."""
        with self._lock:
            updated = replace(self._state, **changes)
            if updated == self._state:
                return self._state, False
            self._state = updated
            return self._state, True
