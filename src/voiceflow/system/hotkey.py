from __future__ import annotations

from typing import Any


class PushToTalk:
    def __init__(
        self,
        key_name: str,
        on_press: Any,
        on_release: Any,
        mode: str = "hold",
        *,
        keyboard_module: Any | None = None,
    ) -> None:
        if keyboard_module is None:
            from pynput import keyboard as keyboard_module

        self._keyboard = keyboard_module
        key_map = {
            "alt": keyboard_module.Key.alt,
            "alt_r": keyboard_module.Key.alt_r,
            "ctrl": keyboard_module.Key.ctrl,
            "ctrl_r": keyboard_module.Key.ctrl_r,
            "cmd": keyboard_module.Key.cmd,
            "cmd_r": keyboard_module.Key.cmd_r,
            "shift": keyboard_module.Key.shift,
            "f13": keyboard_module.Key.f13,
        }
        names = [name.strip() for name in key_name.split("+")]
        unknown = [name for name in names if name not in key_map]
        if unknown:
            raise ValueError(f"Unknown hotkey(s) {unknown}. Choose from: {list(key_map)}")
        if mode not in {"hold", "toggle"}:
            raise ValueError("Hotkey mode must be 'hold' or 'toggle'")
        self.chord = {key_map[name] for name in names}
        self.mode = mode
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self._down: set[Any] = set()
        self._chord_held = False
        self._recording = False
        self._enabled = True
        self._listener = keyboard_module.Listener(on_press=self._press, on_release=self._release)

    def _press(self, key: Any) -> None:
        if not self._enabled or key not in self.chord:
            return
        self._down.add(key)
        if self._chord_held or self._down != self.chord:
            return
        self._chord_held = True
        if self.mode == "toggle" and self._recording:
            self._recording = False
            self.on_release_cb()
        else:
            self._recording = True
            self.on_press_cb()

    def _release(self, key: Any) -> None:
        if key not in self.chord:
            return
        self._down.discard(key)
        if self._chord_held and self._down != self.chord:
            self._chord_held = False
            if self.mode == "hold" and self._recording:
                self._recording = False
                self.on_release_cb()

    def start(self) -> None:
        self._listener.start()

    def stop(self) -> None:
        self._enabled = False
        self._recording = False
        self._down.clear()
        self._listener.stop()

