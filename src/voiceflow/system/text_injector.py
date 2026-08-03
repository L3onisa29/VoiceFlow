from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass

from voiceflow.system.text_ports import (
    ClipboardPort,
    FocusAccess,
    FocusTarget,
    KeyboardEmitter,
)


@dataclass(frozen=True, slots=True)
class InjectionDestination:
    focus_target: FocusTarget | None

    @classmethod
    def focused(cls, target: FocusTarget) -> InjectionDestination:
        return cls(focus_target=target)

    @classmethod
    def clipboard_only(cls) -> InjectionDestination:
        return cls(focus_target=None)

    @property
    def uses_clipboard_only(self) -> bool:
        return self.focus_target is None


class FocusChangedError(RuntimeError):
    pass


class _LegacyFocusAccess:
    def __init__(
        self,
        native: FocusAccess,
        *,
        accessibility_check: Callable[[], bool] | None,
        focus_provider: Callable[[], FocusTarget] | None,
        focus_equal: Callable[[FocusTarget, FocusTarget], bool] | None,
    ) -> None:
        self._native = native
        self._accessibility_check = accessibility_check
        self._focus_provider = focus_provider
        self._focus_equal = focus_equal

    def trusted(self) -> bool:
        if self._accessibility_check is not None:
            return bool(self._accessibility_check())
        return self._native.trusted()

    def capture(self) -> FocusTarget:
        if self._focus_provider is not None:
            return self._focus_provider()
        return self._native.capture()

    def same(self, left: FocusTarget, right: FocusTarget) -> bool:
        if self._focus_equal is not None:
            return bool(self._focus_equal(left, right))
        return self._native.same(left, right)


class TextInjector:
    def __init__(
        self,
        method: str = "paste",
        *,
        focus_access: FocusAccess | None = None,
        clipboard: ClipboardPort | None = None,
        keyboard: KeyboardEmitter | None = None,
        quartz: object | None = None,
        pasteboard: object | None = None,
        string_type: object | None = None,
        accessibility_check: Callable[[], bool] | None = None,
        focus_provider: Callable[[], FocusTarget] | None = None,
        focus_equal: Callable[[FocusTarget, FocusTarget], bool] | None = None,
        clipboard_clear_after_seconds: float | None = 60,
        dispatch: Callable[..., None] | None = None,
    ) -> None:
        if focus_access is None or clipboard is None or keyboard is None:
            from voiceflow.system.macos_text_adapters import (
                MacOSClipboard,
                MacOSFocusAccess,
                MacOSKeyboardEmitter,
            )

            if focus_access is None:
                native_focus = MacOSFocusAccess()
                if (
                    accessibility_check is not None
                    or focus_provider is not None
                    or focus_equal is not None
                ):
                    focus_access = _LegacyFocusAccess(
                        native_focus,
                        accessibility_check=accessibility_check,
                        focus_provider=focus_provider,
                        focus_equal=focus_equal,
                    )
                else:
                    focus_access = native_focus
            if clipboard is None:
                clipboard = MacOSClipboard(
                    pasteboard=pasteboard,
                    string_type=string_type,
                )
            if keyboard is None:
                keyboard = MacOSKeyboardEmitter(quartz=quartz)
        self.method = method
        self._focus_access = focus_access
        self._clipboard = clipboard
        self._keyboard = keyboard
        self.clipboard_clear_after_seconds = clipboard_clear_after_seconds
        if dispatch is None:
            from PyObjCTools import AppHelper

            dispatch = AppHelper.callAfter
        self._dispatch = dispatch
        self._timers: set[threading.Timer] = set()
        self._lock = threading.RLock()
        self._closed = False

    def capture_destination(self) -> InjectionDestination:
        if not self._focus_access.trusted():
            raise PermissionError("Accessibility permission is required for text insertion")
        return InjectionDestination.focused(self._focus_access.capture())

    def copy_to_clipboard(self, text: str) -> None:
        if text:
            self._clipboard.write(text)

    def _schedule_clipboard_clear(self, text: str, change_count: int | None) -> None:
        if self.clipboard_clear_after_seconds is None or change_count is None:
            return
        timer: threading.Timer

        def dispatch_clear() -> None:
            try:
                with self._lock:
                    if self._closed:
                        return
                self._dispatch(self._clear_clipboard_if_unchanged, text, change_count)
            finally:
                with self._lock:
                    self._timers.discard(timer)

        timer = threading.Timer(self.clipboard_clear_after_seconds, dispatch_clear)
        timer.daemon = True
        with self._lock:
            if self._closed:
                return
            self._timers.add(timer)
        timer.start()

    def _clear_clipboard_if_unchanged(self, text: str, change_count: int) -> None:
        with self._lock:
            if self._closed:
                return
        if self._clipboard.change_count() != change_count:
            return
        if self._clipboard.read() != text:
            return
        self._clipboard.clear()

    def inject(self, text: str, *, destination: InjectionDestination) -> None:
        if not text:
            return
        if not self._focus_access.trusted():
            raise PermissionError("Accessibility permission is required for text insertion")
        if destination.uses_clipboard_only:
            change_count = self._clipboard.write(text)
            self._schedule_clipboard_clear(text, change_count)
            return
        target = destination.focus_target
        if target is None:
            raise RuntimeError("Focused injection destination is missing its target")
        current_target = self._focus_access.capture()
        if not self._focus_access.same(target, current_target):
            change_count = self._clipboard.write(text)
            self._schedule_clipboard_clear(text, change_count)
            raise FocusChangedError(
                "Text destination changed; transcription was left on the clipboard"
            )
        if self.method == "type":
            self._keyboard.type_unicode(text)
            return
        change_count = self._clipboard.write(text)
        try:
            self._keyboard.paste()
        finally:
            self._schedule_clipboard_clear(text, change_count)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            timers = tuple(self._timers)
            self._timers.clear()
        for timer in timers:
            timer.cancel()
