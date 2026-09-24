from __future__ import annotations

import logging
import sys
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
        cancel_key_name: str | None = None,
        on_cancel: Callable[[], bool] | None = None,
        keyboard_module: Any | None = None,
        quartz_module: Any | None = None,
        is_recording: Callable[[], bool] | None = None,
        callback_dispatcher: Callable[[Callable[[], None]], None] | None = None,
        listener_stop_timeout_seconds: float = 1,
    ) -> None:
        use_native_keyboard = keyboard_module is None
        if keyboard_module is None:
            from pynput import keyboard as keyboard_module

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

        def parse_chord(value: str) -> tuple[list[str], frozenset[Any]]:
            names = [name.strip() for name in value.split("+")]
            unknown = [name for name in names if name not in key_map]
            if unknown:
                raise ValueError(f"Unknown hotkey(s) {unknown}. Choose from: {list(key_map)}")
            if not any(name == "space" or name.startswith("f") for name in names):
                raise ValueError("Hotkey must contain a supported non-modifier key")
            return names, frozenset(key_map[name] for name in names)

        names, chord = parse_chord(key_name)
        cancel_names: list[str] = []
        cancel_chord: frozenset[Any] | None = None
        if cancel_key_name is not None:
            if on_cancel is None:
                raise ValueError("Cancel hotkey requires an on_cancel callback")
            cancel_names, cancel_chord = parse_chord(cancel_key_name)
            if cancel_chord == chord:
                raise ValueError("Cancel hotkey must differ from the recording hotkey")
        if mode not in {"hold", "toggle"}:
            raise ValueError("Hotkey mode must be 'hold' or 'toggle'")
        self.chord = set(chord)
        self.cancel_chord = set(cancel_chord) if cancel_chord is not None else None
        self._all_keys = set(chord | (cancel_chord or frozenset()))
        self.mode = mode
        self.on_press_cb = on_press
        self.on_release_cb = on_release
        self.on_cancel_cb = on_cancel
        self.is_recording_cb = is_recording
        self.callback_dispatcher = callback_dispatcher
        self.listener_stop_timeout_seconds = listener_stop_timeout_seconds
        self._down: set[Any] = set()
        self._held_chord: frozenset[Any] | None = None
        self._recording = False
        self._enabled = True
        self._started = False
        self._lock = threading.RLock()
        self._action_lock = threading.Lock()
        self._quartz: Any | None = None
        self._trigger_virtual_keycodes = {
            virtual_keycode
            for name in [*names, *cancel_names]
            if name == "space" or name.startswith("f")
            if (
                virtual_keycode := getattr(
                    getattr(key_map[name], "value", None),
                    "vk",
                    None,
                )
            )
            is not None
        }
        self._suppressed_virtual_keycodes: set[int] = set()

        listener_options: dict[str, Any] = {
            "on_press": self._press,
            "on_release": self._release,
        }
        if quartz_module is not None or (use_native_keyboard and sys.platform == "darwin"):
            if quartz_module is None:
                import Quartz as quartz_module

            self._quartz = quartz_module
            listener_options["darwin_intercept"] = self._intercept_darwin
        self._listener = keyboard_module.Listener(**listener_options)

    def _press(self, key: Any) -> None:
        action: Callable[[], None] | None = None
        with self._lock:
            if not self._enabled or key not in self._all_keys:
                return
            self._down.add(key)
            if self._held_chord is not None:
                return
            pressed = frozenset(self._down)
            if self.cancel_chord is not None and pressed == frozenset(self.cancel_chord):
                # Do not steal the cancel chord from the frontmost application
                # unless this listener has successfully started a recording.
                if not self._recording:
                    return
                self._held_chord = pressed
                action = self._apply_cancel
            elif pressed == frozenset(self.chord):
                self._held_chord = pressed
                action = self._apply_press
        if action is not None:
            self._dispatch_action(action)

    def _release(self, key: Any) -> None:
        action: Callable[[], None] | None = None
        with self._lock:
            if key not in self._all_keys:
                return
            self._down.discard(key)
            held_chord = self._held_chord
            if held_chord is not None and not held_chord.issubset(self._down):
                self._held_chord = None
                if self.mode == "hold" and held_chord == frozenset(self.chord):
                    action = self._apply_hold_release
        if action is not None:
            self._dispatch_action(action)

    def _dispatch_action(self, action: Callable[[], None]) -> None:
        """Keep application work outside the latency-sensitive macOS event tap."""
        if self._quartz is None:
            action()
            return
        if self.callback_dispatcher is not None:
            self.callback_dispatcher(action)
            return
        threading.Thread(
            target=action,
            name="voiceflow-hotkey-action",
            daemon=True,
        ).start()

    def _apply_press(self) -> None:
        with self._action_lock:
            with self._lock:
                if not self._enabled:
                    return
            try:
                recording = self._recording_now()
                if self.mode == "toggle" and recording:
                    succeeded = self.on_release_cb()
                    next_recording = False
                else:
                    succeeded = self.on_press_cb()
                    next_recording = True
            except Exception:
                LOGGER.exception("Hotkey press callback failed")
                return
            if succeeded:
                with self._lock:
                    self._recording = next_recording

    def _recording_now(self) -> bool:
        if self.is_recording_cb is not None:
            return self.is_recording_cb()
        return self._recording

    def _apply_cancel(self) -> None:
        with self._action_lock:
            with self._lock:
                if not self._enabled or self.on_cancel_cb is None:
                    return
            try:
                succeeded = self.on_cancel_cb()
            except Exception:
                LOGGER.exception("Cancel hotkey callback failed")
                return
            if succeeded:
                with self._lock:
                    self._recording = False

    def _apply_hold_release(self) -> None:
        with self._action_lock:
            with self._lock:
                if not self._enabled or not self._recording:
                    return
            try:
                succeeded = self.on_release_cb()
            except Exception:
                LOGGER.exception("Hotkey release callback failed")
                return
            if succeeded:
                with self._lock:
                    self._recording = False

    def _intercept_darwin(self, event_type: Any, event: Any) -> Any | None:
        """Consume the configured trigger key when it completes the hotkey chord."""
        quartz = self._quartz
        if quartz is None or event_type not in {
            quartz.kCGEventKeyDown,
            quartz.kCGEventKeyUp,
        }:
            return event

        virtual_keycode = int(
            quartz.CGEventGetIntegerValueField(
                event,
                quartz.kCGKeyboardEventKeycode,
            )
        )
        if virtual_keycode not in self._trigger_virtual_keycodes:
            return event

        with self._lock:
            if event_type == quartz.kCGEventKeyDown and self._held_chord is not None:
                self._suppressed_virtual_keycodes.add(virtual_keycode)
                return None
            if (
                event_type == quartz.kCGEventKeyUp
                and virtual_keycode in self._suppressed_virtual_keycodes
            ):
                self._suppressed_virtual_keycodes.discard(virtual_keycode)
                return None
        return event

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
            self._held_chord = None
            self._suppressed_virtual_keycodes.clear()
        self._listener.stop()
        join = getattr(self._listener, "join", None)
        if join is not None and threading.current_thread() is not self._listener:
            join(timeout=self.listener_stop_timeout_seconds)
