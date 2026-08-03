from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger(__name__)

MAXIMUM_NATIVE_RELEASES = 2
SATURATION_MESSAGE = "Audio input releases are still pending; retry the input"


class AudioReleaseCoordinator:
    """Bound ownership and background release of native audio streams."""

    def __init__(
        self,
        *,
        on_saturated: Callable[[str], None] | None = None,
    ) -> None:
        self._on_saturated = on_saturated
        self._condition = threading.Condition()
        self._slots: set[object] = set()
        self._releasing: set[object] = set()
        self._closed = False

    @property
    def pending(self) -> int:
        """Return the number of stream slots that are currently owned."""
        with self._condition:
            return len(self._slots)

    @property
    def saturated(self) -> bool:
        with self._condition:
            return len(self._slots) >= MAXIMUM_NATIVE_RELEASES

    def reserve(self) -> object | None:
        """Reserve a slot without waiting, before a native stream is created."""
        with self._condition:
            if self._closed:
                return None
            if len(self._slots) < MAXIMUM_NATIVE_RELEASES:
                slot = object()
                self._slots.add(slot)
                return slot
            callback = self._on_saturated
        if callback is not None:
            try:
                callback(SATURATION_MESSAGE)
            except Exception:
                LOGGER.exception("Audio release saturation callback failed")
        return None

    def discard(self, slot: object) -> None:
        """Return a reservation when no native stream was created."""
        with self._condition:
            if slot in self._slots:
                self._slots.remove(slot)
                self._condition.notify_all()

    def release(
        self,
        slot: object,
        stream: Any,
        *,
        unexpectedly_stopped: bool = False,
    ) -> None:
        """Release a native stream on one tracked daemon thread."""
        with self._condition:
            if slot not in self._slots or slot in self._releasing:
                return
            self._releasing.add(slot)

            def release_native() -> None:
                try:
                    if not unexpectedly_stopped:
                        try:
                            abort = getattr(stream, "abort", None)
                            if abort is not None:
                                abort()
                            else:
                                stream.stop()
                        except Exception:
                            LOGGER.exception("Failed to abort audio input stream")
                    try:
                        stream.close()
                    except Exception:
                        LOGGER.exception("Failed to close audio input stream")
                finally:
                    self._finish_release(slot)

            thread = threading.Thread(
                target=release_native,
                name="voiceflow-audio-release",
                daemon=True,
            )
            try:
                thread.start()
            except Exception:
                self._releasing.remove(slot)
                self._slots.remove(slot)
                self._condition.notify_all()
                raise

    def close(self, timeout: float) -> None:
        """Reject new reservations and wait at most ``timeout`` seconds for owned slots."""
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        deadline = time.monotonic() + timeout
        with self._condition:
            self._closed = True
            while self._slots:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return
                self._condition.wait(remaining)

    def _finish_release(self, slot: object) -> None:
        with self._condition:
            self._releasing.discard(slot)
            self._slots.discard(slot)
            self._condition.notify_all()
