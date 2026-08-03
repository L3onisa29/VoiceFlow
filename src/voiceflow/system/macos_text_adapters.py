from __future__ import annotations

from collections.abc import Callable
from typing import Any

from voiceflow.system.permissions import accessibility_is_trusted
from voiceflow.system.text_ports import FocusTarget

KEY_V = 9
MAX_UNICODE_EVENT_UNITS = 20


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


class MacOSFocusAccess:
    def __init__(
        self,
        *,
        accessibility_check: Callable[[], bool] = accessibility_is_trusted,
        application_services: Any | None = None,
        cf_equal: Callable[[Any, Any], object] | None = None,
    ) -> None:
        self._accessibility_check = accessibility_check
        self._application_services = application_services
        self._cf_equal = cf_equal

    def trusted(self) -> bool:
        return bool(self._accessibility_check())

    def capture(self) -> FocusTarget:
        services = self._application_services
        if services is None:
            import ApplicationServices as services

        system = services.AXUIElementCreateSystemWide()
        error, element = services.AXUIElementCopyAttributeValue(
            system,
            services.kAXFocusedUIElementAttribute,
            None,
        )
        if error != services.kAXErrorSuccess or element is None:
            raise RuntimeError("No accessible text destination is currently focused")
        pid_error, pid = services.AXUIElementGetPid(element, None)
        if pid_error != services.kAXErrorSuccess:
            raise RuntimeError("Unable to identify the focused application")
        return FocusTarget(int(pid), element)

    def same(self, left: FocusTarget, right: FocusTarget) -> bool:
        if left.pid != right.pid:
            return False
        cf_equal = self._cf_equal
        if cf_equal is None:
            from CoreFoundation import CFEqual as cf_equal

        return bool(cf_equal(left.element, right.element))


class MacOSClipboard:
    def __init__(
        self,
        *,
        pasteboard: Any | None = None,
        string_type: Any | None = None,
    ) -> None:
        if pasteboard is None:
            from AppKit import NSPasteboard, NSPasteboardTypeString

            pasteboard = NSPasteboard.generalPasteboard()
            if string_type is None:
                string_type = NSPasteboardTypeString
        elif string_type is None:
            from AppKit import NSPasteboardTypeString

            string_type = NSPasteboardTypeString
        self._pasteboard = pasteboard
        self._string_type = string_type

    def write(self, text: str) -> int | None:
        self._pasteboard.clearContents()
        written = self._pasteboard.setString_forType_(text, self._string_type)
        if written is False:
            raise RuntimeError("Unable to write transcription to the clipboard")
        change_count = getattr(self._pasteboard, "changeCount", None)
        return int(change_count()) if change_count is not None else None

    def read(self) -> str | None:
        value = self._pasteboard.stringForType_(self._string_type)
        return None if value is None else str(value)

    def change_count(self) -> int:
        return int(self._pasteboard.changeCount())

    def clear(self) -> None:
        self._pasteboard.clearContents()


class MacOSKeyboardEmitter:
    def __init__(self, *, quartz: Any | None = None) -> None:
        if quartz is None:
            import Quartz as quartz

        self._quartz = quartz

    def paste(self) -> None:
        quartz = self._quartz
        source = quartz.CGEventSourceCreate(quartz.kCGEventSourceStateHIDSystemState)
        if source is None:
            raise RuntimeError("Unable to create the Cmd+V event source")
        down = quartz.CGEventCreateKeyboardEvent(source, KEY_V, True)
        up = quartz.CGEventCreateKeyboardEvent(source, KEY_V, False)
        if down is None or up is None:
            raise RuntimeError("Unable to create the Cmd+V keyboard event")
        quartz.CGEventSetFlags(down, quartz.kCGEventFlagMaskCommand)
        quartz.CGEventSetFlags(up, quartz.kCGEventFlagMaskCommand)
        quartz.CGEventPost(quartz.kCGHIDEventTap, down)
        quartz.CGEventPost(quartz.kCGHIDEventTap, up)

    def type_unicode(self, text: str) -> None:
        quartz = self._quartz
        source = quartz.CGEventSourceCreate(quartz.kCGEventSourceStateHIDSystemState)
        if source is None:
            raise RuntimeError("Unable to create the Unicode event source")
        for chunk in _unicode_chunks(text):
            down = quartz.CGEventCreateKeyboardEvent(source, 0, True)
            up = quartz.CGEventCreateKeyboardEvent(source, 0, False)
            if down is None or up is None:
                raise RuntimeError("Unable to create the Unicode keyboard event")
            utf16_length = len(chunk.encode("utf-16-le")) // 2
            quartz.CGEventKeyboardSetUnicodeString(down, utf16_length, chunk)
            quartz.CGEventKeyboardSetUnicodeString(up, utf16_length, chunk)
            quartz.CGEventPost(quartz.kCGHIDEventTap, down)
            quartz.CGEventPost(quartz.kCGHIDEventTap, up)
