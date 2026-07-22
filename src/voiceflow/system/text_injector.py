from __future__ import annotations

import time
from typing import Any

KEY_V = 9


class TextInjector:
    def __init__(
        self,
        method: str = "paste",
        restore_clipboard: bool = True,
        *,
        quartz: Any | None = None,
        pasteboard: Any | None = None,
        string_type: Any | None = None,
    ) -> None:
        if quartz is None or pasteboard is None:
            import Quartz
            from AppKit import NSPasteboard, NSPasteboardTypeString

            quartz = Quartz
            pasteboard = NSPasteboard.generalPasteboard()
            string_type = NSPasteboardTypeString
        self.method = method
        self.restore_clipboard = restore_clipboard
        self.quartz = quartz
        self.pasteboard = pasteboard
        self.string_type = string_type

    def _get_clipboard(self) -> str | None:
        return self.pasteboard.stringForType_(self.string_type)

    def _set_clipboard(self, text: str) -> None:
        self.pasteboard.clearContents()
        self.pasteboard.setString_forType_(text, self.string_type)

    def _press_cmd_v(self) -> None:
        q = self.quartz
        source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        down = q.CGEventCreateKeyboardEvent(source, KEY_V, True)
        up = q.CGEventCreateKeyboardEvent(source, KEY_V, False)
        q.CGEventSetFlags(down, q.kCGEventFlagMaskCommand)
        q.CGEventSetFlags(up, q.kCGEventFlagMaskCommand)
        q.CGEventPost(q.kCGHIDEventTap, down)
        q.CGEventPost(q.kCGHIDEventTap, up)

    def _type_unicode(self, text: str) -> None:
        q = self.quartz
        source = q.CGEventSourceCreate(q.kCGEventSourceStateHIDSystemState)
        for character in text:
            down = q.CGEventCreateKeyboardEvent(source, 0, True)
            q.CGEventKeyboardSetUnicodeString(down, len(character), character)
            q.CGEventPost(q.kCGHIDEventTap, down)
            up = q.CGEventCreateKeyboardEvent(source, 0, False)
            q.CGEventKeyboardSetUnicodeString(up, len(character), character)
            q.CGEventPost(q.kCGHIDEventTap, up)
            time.sleep(0.002)

    def inject(self, text: str) -> None:
        if not text:
            return
        if self.method == "type":
            self._type_unicode(text)
            return
        old = self._get_clipboard() if self.restore_clipboard else None
        self._set_clipboard(text)
        time.sleep(0.05)
        self._press_cmd_v()
        if old is not None:
            time.sleep(0.25)
            self._set_clipboard(old)

