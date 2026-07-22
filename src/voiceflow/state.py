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
    RECORDING = auto()
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


@dataclass(frozen=True, slots=True)
class AppState:
    model: ModelState = ModelState.NOT_LOADED
    activity: ActivityState = ActivityState.IDLE
    postprocess: PostprocessState = PostprocessState.CHECKING
    message: str | None = None
    model_path: str | None = None
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

    def update(self, **changes: object) -> AppState:
        with self._lock:
            self._state = replace(self._state, **changes)
            return self._state
