from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

import voiceflow.system.macos_text_adapters as macos_text_adapters
from voiceflow.system.macos_text_adapters import (
    MacOSClipboard,
    MacOSFocusAccess,
    MacOSKeyboardEmitter,
)
from voiceflow.system.text_injector import (
    FocusChangedError,
    InjectionDestination,
    TextInjector,
)
from voiceflow.system.text_ports import FocusTarget

ROOT = Path(__file__).resolve().parents[1]


@dataclass
class FakeFocusAccess:
    current: FocusTarget
    is_trusted: bool = True
    same_result: bool = True

    def __post_init__(self) -> None:
        self.captures = 0
        self.comparisons: list[tuple[FocusTarget, FocusTarget]] = []

    def trusted(self) -> bool:
        return self.is_trusted

    def capture(self) -> FocusTarget:
        self.captures += 1
        return self.current

    def same(self, left: FocusTarget, right: FocusTarget) -> bool:
        self.comparisons.append((left, right))
        return self.same_result


class FakeClipboard:
    def __init__(self) -> None:
        self.text: str | None = None
        self.count = 0
        self.clears = 0

    def write(self, text: str) -> int:
        self.text = text
        self.count += 1
        return self.count

    def read(self) -> str | None:
        return self.text

    def change_count(self) -> int:
        return self.count

    def clear(self) -> None:
        self.text = None
        self.count += 1
        self.clears += 1

    def replace_as_user(self, text: str) -> None:
        self.text = text
        self.count += 1


class FakeKeyboard:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    def paste(self) -> None:
        self.events.append(("paste", None))

    def type_unicode(self, text: str) -> None:
        self.events.append(("type", text))


class FakeTimer:
    created: list[FakeTimer] = []

    def __init__(self, delay: float, callback: Callable[[], None]) -> None:
        self.delay = delay
        self.callback = callback
        self.daemon = False
        self.started = 0
        self.cancelled = 0
        self.created.append(self)

    def start(self) -> None:
        self.started += 1

    def cancel(self) -> None:
        self.cancelled += 1

    def fire(self) -> None:
        self.callback()


def make_injector(
    *,
    trusted: bool = True,
    same: bool = True,
    method: str = "paste",
    clear_after: float | None = None,
) -> tuple[TextInjector, FakeFocusAccess, FakeClipboard, FakeKeyboard]:
    target = FocusTarget(7, "field")
    focus = FakeFocusAccess(target, is_trusted=trusted, same_result=same)
    clipboard = FakeClipboard()
    keyboard = FakeKeyboard()
    injector = TextInjector(
        method=method,
        focus_access=focus,
        clipboard=clipboard,
        keyboard=keyboard,
        clipboard_clear_after_seconds=clear_after,
        dispatch=lambda function, *args: function(*args),
    )
    return injector, focus, clipboard, keyboard


def test_capture_destination_uses_focus_access_trust_boundary() -> None:
    injector, focus, _clipboard, _keyboard = make_injector()
    assert injector.capture_destination() == InjectionDestination.focused(focus.current)
    assert focus.captures == 1

    untrusted, denied_focus, _clipboard, _keyboard = make_injector(trusted=False)
    with pytest.raises(PermissionError, match="Accessibility"):
        untrusted.capture_destination()
    assert denied_focus.captures == 0


def test_paste_policy_writes_clipboard_and_emits_paste_for_same_focus() -> None:
    injector, focus, clipboard, keyboard = make_injector()
    injector.inject("hello", destination=InjectionDestination.focused(focus.current))
    assert clipboard.text == "hello"
    assert keyboard.events == [("paste", None)]
    assert focus.comparisons == [(focus.current, focus.current)]


def test_changed_focus_keeps_clipboard_and_does_not_emit_keyboard_events() -> None:
    injector, focus, clipboard, keyboard = make_injector(same=False)
    destination = InjectionDestination.focused(FocusTarget(8, "old-field"))
    with pytest.raises(FocusChangedError, match="left on the clipboard"):
        injector.inject("safe copy", destination=destination)
    assert clipboard.text == "safe copy"
    assert keyboard.events == []


def test_unicode_policy_passes_emoji_and_non_bmp_text_to_emitter() -> None:
    injector, focus, clipboard, keyboard = make_injector(method="type")
    text = "hello 🙂 U0001d11e"
    injector.inject(text, destination=InjectionDestination.focused(focus.current))
    assert keyboard.events == [("type", text)]
    assert clipboard.text is None


def test_clipboard_only_policy_never_captures_focus_or_emits_keyboard() -> None:
    injector, focus, clipboard, keyboard = make_injector()
    injector.inject("fallback", destination=InjectionDestination.clipboard_only())
    assert clipboard.text == "fallback"
    assert focus.captures == 0
    assert focus.comparisons == []
    assert keyboard.events == []


def test_clipboard_clear_uses_change_count_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeTimer.created.clear()
    monkeypatch.setattr("voiceflow.system.text_injector.threading.Timer", FakeTimer)
    injector, focus, clipboard, _keyboard = make_injector(clear_after=3)

    injector.inject("temporary", destination=InjectionDestination.focused(focus.current))
    first = FakeTimer.created[-1]
    assert first.delay == 3
    first.fire()
    assert clipboard.text is None
    assert clipboard.clears == 1

    injector.inject("second", destination=InjectionDestination.focused(focus.current))
    second = FakeTimer.created[-1]
    clipboard.replace_as_user("user value")
    second.fire()
    assert clipboard.text == "user value"
    assert clipboard.clears == 1


def test_close_cancels_pending_timer_and_is_idempotent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    FakeTimer.created.clear()
    monkeypatch.setattr("voiceflow.system.text_injector.threading.Timer", FakeTimer)
    injector, focus, clipboard, _keyboard = make_injector(clear_after=3)
    injector.inject("temporary", destination=InjectionDestination.focused(focus.current))
    timer = FakeTimer.created[-1]

    injector.close()
    injector.close()
    timer.fire()

    assert timer.cancelled == 1
    assert clipboard.text == "temporary"


def test_native_clipboard_reports_write_failure_and_keyboard_source_failures() -> None:
    pasteboard = SimpleNamespace(
        clearContents=lambda: None,
        setString_forType_=lambda _text, _kind: False,
        stringForType_=lambda _kind: None,
        changeCount=lambda: 4,
    )
    with pytest.raises(RuntimeError, match="write transcription"):
        MacOSClipboard(pasteboard=pasteboard, string_type="string").write("text")

    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        CGEventSourceCreate=lambda _state: None,
    )
    keyboard = MacOSKeyboardEmitter(quartz=quartz)
    with pytest.raises(RuntimeError, match=r"Cmd\+V event source"):
        keyboard.paste()
    with pytest.raises(RuntimeError, match="Unicode event source"):
        keyboard.type_unicode("🙂")


def test_native_focus_adapter_preserves_capture_errors_and_pid_equality() -> None:
    failed_services = SimpleNamespace(
        AXUIElementCreateSystemWide=lambda: "system",
        AXUIElementCopyAttributeValue=lambda _system, _attribute, _default: (1, None),
        AXUIElementGetPid=lambda _element, _default: (0, 7),
        kAXErrorSuccess=0,
        kAXFocusedUIElementAttribute="focused",
    )
    focus = MacOSFocusAccess(
        accessibility_check=lambda: True,
        application_services=failed_services,
        cf_equal=lambda left, right: left == right,
    )
    with pytest.raises(RuntimeError, match="No accessible text destination"):
        focus.capture()
    assert not focus.same(FocusTarget(1, "field"), FocusTarget(2, "field"))


def test_legacy_injection_parameters_remain_supported() -> None:
    events: list[tuple[object, ...]] = []
    target = FocusTarget(7, "field")
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda state: ("source", state),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventSetFlags=lambda event, flags: events.append(("flags", event, flags)),
        CGEventPost=lambda tap, event: events.append(("post", tap, event)),
    )

    class Pasteboard:
        def __init__(self) -> None:
            self.text: str | None = None
            self.count = 0

        def clearContents(self) -> None:
            self.text = None
            self.count += 1

        def setString_forType_(self, text: str, _kind: object) -> bool:
            self.text = text
            self.count += 1
            return True

        def stringForType_(self, _kind: object) -> str | None:
            return self.text

        def changeCount(self) -> int:
            return self.count

    pasteboard = Pasteboard()
    injector = TextInjector(
        quartz=quartz,
        pasteboard=pasteboard,
        string_type="string",
        accessibility_check=lambda: True,
        focus_provider=lambda: target,
        focus_equal=lambda left, right: left == right,
        clipboard_clear_after_seconds=None,
    )
    injector.inject("legacy", destination=InjectionDestination.focused(target))
    assert pasteboard.text == "legacy"
    assert len([event for event in events if event[0] == "post"]) == 2


def test_default_native_wiring_builds_the_three_adapters(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    focus = FakeFocusAccess(FocusTarget(7, "field"))
    clipboard = FakeClipboard()
    keyboard = FakeKeyboard()
    monkeypatch.setattr(macos_text_adapters, "MacOSFocusAccess", lambda: focus)
    monkeypatch.setattr(
        macos_text_adapters,
        "MacOSClipboard",
        lambda *, pasteboard, string_type: clipboard,
    )
    monkeypatch.setattr(
        macos_text_adapters,
        "MacOSKeyboardEmitter",
        lambda *, quartz: keyboard,
    )

    injector = TextInjector(
        clipboard_clear_after_seconds=None,
        dispatch=lambda _function, *_args: None,
    )
    destination = injector.capture_destination()
    injector.inject("native wiring", destination=destination)

    assert clipboard.text == "native wiring"
    assert keyboard.events == [("paste", None)]


def test_pure_imports_do_not_create_or_import_appkit() -> None:
    source = """
import builtins
import json

seen = []
original_import = builtins.__import__

def checked_import(name, *args, **kwargs):
    if name == "AppKit" or name.startswith("AppKit."):
        seen.append(name)
        raise AssertionError("AppKit imported during pure module import")
    return original_import(name, *args, **kwargs)

builtins.__import__ = checked_import
import voiceflow.system.text_ports
import voiceflow.system.macos_text_adapters
import voiceflow.system.text_injector
print(json.dumps(seen))
"""
    completed = subprocess.run(
        [sys.executable, "-c", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert completed.stdout.strip() == "[]"
