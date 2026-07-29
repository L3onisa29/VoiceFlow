from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from voiceflow.system.permissions import accessibility_is_trusted

KEY_V = 9
MAX_UNICODE_EVENT_UNITS = 20


@dataclass(frozen=True, slots=True)
class FocusTarget:
    pid: int
    element: Any


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


def _focused_accessibility_element() -> FocusTarget:
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateSystemWide,
        AXUIElementGetPid,
        kAXErrorSuccess,
        kAXFocusedUIElementAttribute,
    )

    system = AXUIElementCreateSystemWide()
    error, element = AXUIElementCopyAttributeValue(
        system,
        kAXFocusedUIElementAttribute,
        None,
    )
    if error != kAXErrorSuccess or element is None:
        raise RuntimeError("No accessible text destination is currently focused")
    pid_error, pid = AXUIElementGetPid(element, None)
    if pid_error != kAXErrorSuccess:
        raise RuntimeError("Unable to identify the focused application")
    return FocusTarget(int(pid), element)


def _same_focus(left: FocusTarget, right: FocusTarget) -> bool:
    if left.pid != right.pid:
        return False
    from CoreFoundation import CFEqual

    return bool(CFEqual(left.element, right.element))


def _unicode_chunks(text: str, max_units: int = MAX_UNICODE_EVENT_UNITS) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_units = 0
    for character in text:
        units = len(character.encode("utf-16-le")) // 2
        if current and current_units + units > max_units:
            chunks.append("".join(current))
            current = []
            current_units = 0
        current.append(character)
        current_units += units
    if current:
        chunks.append("".join(current))
    return chunks


class TextInjector:
    def __init__(
        self,
        method: str = "paste",
        *,
        quartz: Any | None = None,
        pasteboard: Any | None = None,
        string_type: Any | None = None,
        accessibility_check: Callable[[], bool] = accessibility_is_trusted,
        focus_provider: Callable[[], FocusTarget] = _focused_accessibility_element,
        focus_equal: Callable[[FocusTarget, FocusTarget], bool] = _same_focus,
        clipboard_clear_after_seconds: float | None = 60,
        dispatch: Callable[..., None] | None = None,
    ) -> None:
        if quartz is None or pasteboard is None:
            import Quartz
            from AppKit import NSPasteboard, NSPasteboardTypeString

            quartz = Quartz
            pasteboard = NSPasteboard.generalPasteboard()
            string_type = NSPasteboardTypeString
        self.method = method
        self.quartz = quartz
        self.pasteboard = pasteboard
        self.string_type = string_type
        self._accessibility_check = accessibility_check
        self._focus_provider = focus_provider
        self._focus_equal = focus_equal
        self.clipboard_clear_after_seconds = clipboard_clear_after_seconds
        if dispatch is None:
            from PyObjCTools import AppHelper

            dispatch = AppHelper.callAfter
        self._dispatch = dispatch
        self._timers: set[threading.Timer] = set()
        self._lock = threading.RLock()
        self._closed = False

    def capture_destination(self) -> InjectionDestination:
        if not self._accessibility_check():
            raise PermissionError("Accessibility permission is required for text insertion")
        return InjectionDestination.focused(self._focus_provider())

    def copy_to_clipboard(self, text: str) -> None:
        if text:
            self._set_clipboard(text)

    def _set_clipboard(self, text: str) -> int | None:
        self.pasteboard.clearContents()
        written = self.pasteboard.setString_forType_(text, self.string_type)
        if written is False:
            raise RuntimeError("Unable to write transcription to the clipboard")
        change_count = getattr(self.pasteboard, "changeCount", None)
        return int(change_count()) if change_count is not None else None

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
        if self.pasteboard.changeCount() != change_count:
            return
        if self.pasteboard.stringForType_(self.string_type) != text:
            return
        self.pasteboard.clearContents()

    def _press_cmd_v(self) -> None:
        q = self.quartz
        source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        if source is None:
            raise RuntimeError("Unable to create the Cmd+V event source")
        down = q.CGEventCreateKeyboardEvent(source, KEY_V, True)
        up = q.CGEventCreateKeyboardEvent(source, KEY_V, False)
        if down is None or up is None:
            raise RuntimeError("Unable to create the Cmd+V keyboard event")
        q.CGEventSetFlags(down, q.kCGEventFlagMaskCommand)
        q.CGEventSetFlags(up, q.kCGEventFlagMaskCommand)
        q.CGEventPost(q.kCGHIDEventTap, down)
        q.CGEventPost(q.kCGHIDEventTap, up)

    def _type_unicode(self, text: str) -> None:
        q = self.quartz
        source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        if source is None:
            raise RuntimeError("Unable to create the Unicode event source")
        for chunk in _unicode_chunks(text):
            down = q.CGEventCreateKeyboardEvent(source, 0, True)
            up = q.CGEventCreateKeyboardEvent(source, 0, False)
            if down is None or up is None:
                raise RuntimeError("Unable to create the Unicode keyboard event")
            utf16_length = len(chunk.encode("utf-16-le")) // 2
            q.CGEventKeyboardSetUnicodeString(down, utf16_length, chunk)
            q.CGEventKeyboardSetUnicodeString(up, utf16_length, chunk)
            q.CGEventPost(q.kCGHIDEventTap, down)
            q.CGEventPost(q.kCGHIDEventTap, up)

    def inject(self, text: str, *, destination: InjectionDestination) -> None:
        if not text:
            return
        if not self._accessibility_check():
            raise PermissionError("Accessibility permission is required for text insertion")
        if destination.uses_clipboard_only:
            change_count = self._set_clipboard(text)
            self._schedule_clipboard_clear(text, change_count)
            return
        target = destination.focus_target
        if target is None:
            raise RuntimeError("Focused injection destination is missing its target")
        current_target = self._focus_provider()
        if not self._focus_equal(target, current_target):
            change_count = self._set_clipboard(text)
            self._schedule_clipboard_clear(text, change_count)
            raise FocusChangedError(
                "Text destination changed; transcription was left on the clipboard"
            )
        if self.method == "type":
            self._type_unicode(text)
            return
        change_count = self._set_clipboard(text)
        try:
            self._press_cmd_v()
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
