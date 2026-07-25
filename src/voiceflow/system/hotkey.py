from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

LOGGER = logging.getLogger(__name__)

HOTKEY_LABELS = {
    "alt": "Alt/Option",
    "alt_r": "Right Alt/Option",
    "ctrl": "Control",
    "ctrl_r": "Right Control",
    "cmd": "Command",
    "cmd_r": "Right Command",
    "shift": "Shift",
    "space": "Space",
}


def hotkey_label(key_name: str) -> str:
    names = (name.strip().casefold() for name in key_name.split("+"))
    return " + ".join(
        HOTKEY_LABELS.get(name, name.upper() if name.startswith("f") else name.title())
        for name in names
        if name
    )


class PushToTalk:
    def __init__(
        self,
        key_name: str,
        on_press: Callable[[], bool],
        on_release: Callable[[], bool],
        mode: str = "hold",
        *,
        keyboard_module: Any | None = None,
        is_recording: Callable[[], bool] | None = None,
        listener_stop_timeout_seconds: float = 1,
    ) -> None:
        if keyboard_module is None:
            from pynput import keyboard as keyboard_module

        self._keyboard = keyboard_module
        key_map: dict[str, Any] = {
            "alt": keyboard_module.Key.alt,
            "alt_r": keyboard_module.Key.alt_r,
            "ctrl": keyboard_module.Key.ctrl,
            "ctrl_r": keyboard_module.Key.ctrl_r,
            "cmd": keyboard_module.Key.cmd,
            "cmd_r": keyboard_module.Key.cmd_r,
            "shift": keyboard_module.Key.shift,
            "space": keyboard_module.Key.space,
        }
        key_map.update(
            {
                f"f{number}": key
                for number in range(1, 21)
                if (key := getattr(keyboard_module.Key, f"f{number}", None)) is not None
            }
        )
        names = [name.strip() for name in key_name.split("+")]
        unknown = [name for name in names if name not in key_map]
        if unknown:
            raise ValueError(f"Unknown hotkey(s) {unknown}. Choose from: {list(key_map)}")
        if not any(name == "space" or name.startswith("f") for name in names):
            raise ValueError("Hotkey must contain a supported non-modifier key")
        if mode not in {"hold", "toggle"}:
            raise ValueError("Hotkey mode must be 'hold' or 'toggle'")
        self.chord = {key_map[name] for name in names}
        self.mode = mode
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self.is_recording_cb = is_recording
        self.listener_stop_timeout_seconds = listener_stop_timeout_seconds
        self._down: set[Any] = set()
        self._chord_held = False
        self._recording = False
        self._enabled = True
        self._started = False
        self._lock = threading.RLock()
        self._listener = keyboard_module.Listener(on_press=self._press, on_release=self._release)

    def _press(self, key: Any) -> None:
        with self._lock:
            if not self._enabled or key not in self.chord:
                return
            self._down.add(key)
            if self._chord_held or self._down != self.chord:
                return
            self._chord_held = True
            try:
                recording = (
                    self.is_recording_cb()
                    if self.mode == "toggle" and self.is_recording_cb is not None
                    else self._recording
                )
                if self.mode == "toggle" and recording:
                    if self.on_release_cb():
                        self._recording = False
                elif self.on_press_cb():
                    self._recording = True
            except Exception:
                LOGGER.exception("Hotkey press callback failed")

    def _release(self, key: Any) -> None:
        with self._lock:
            if key not in self.chord:
                return
            self._down.discard(key)
            if self._chord_held and self._down != self.chord:
                self._chord_held = False
                if self.mode == "hold" and self._recording:
                    try:
                        if self.on_release_cb():
                            self._recording = False
                    except Exception:
                        LOGGER.exception("Hotkey release callback failed")

    def start(self) -> None:
        self._listener.start()
        wait = getattr(self._listener, "wait", None)
        if wait is not None:
            wait()
        with self._lock:
            self._started = True

    @property
    def alive(self) -> bool:
        with self._lock:
            if not self._enabled or not self._started:
                return False
            is_alive = getattr(self._listener, "is_alive", None)
            if callable(is_alive) and not is_alive():
                return False
            return bool(getattr(self._listener, "running", True))

    def mark_recording_stopped(self) -> None:
        with self._lock:
            self._recording = False

    def stop(self) -> None:
        with self._lock:
            self._enabled = False
            self._started = False
            self._recording = False
            self._down.clear()
        self._listener.stop()
        join = getattr(self._listener, "join", None)
        if join is not None and threading.current_thread() is not self._listener:
            join(timeout=self.listener_stop_timeout_seconds)
