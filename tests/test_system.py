from __future__ import annotations

import threading
import time
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voiceflow.audio.quality import capture_rejection_reason
from voiceflow.audio.recorder import Recorder, resolve_input_device
from voiceflow.config import AppConfig
from voiceflow.system.hotkey import PushToTalk, hotkey_label
from voiceflow.system.permissions import check_permissions, microphone_is_authorized
from voiceflow.system.text_injector import (
    FocusChangedError,
    FocusTarget,
    InjectionDestination,
    TextInjector,
)


class FakeStream:
    def __init__(self, **kwargs: Any) -> None:
        self.callback = kwargs["callback"]
        self.finished_callback = kwargs["finished_callback"]
        self.started = 0
        self.stopped = 0
        self.closed = 0
        self.closed_event = threading.Event()
        self.active = False

    def start(self) -> None:
        self.started += 1
        self.active = True

    def stop(self) -> None:
        self.stopped += 1
        self.active = False

    def close(self) -> None:
        self.closed += 1
        self.closed_event.set()


def test_recorder_opens_the_microphone_only_while_recording() -> None:
    streams: list[FakeStream] = []

    def input_stream(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        query_devices=lambda *args, **kwargs: (
            {"name": "Test Mic", "max_input_channels": 1}
            if kwargs.get("kind") == "input"
            else [{"name": "Test Mic", "max_input_channels": 1}]
        ),
    )
    recorder = Recorder(
        sample_rate=4,
        channels=1,
        blocksize=2,
        device="Test Mic",
        sounddevice_module=sounddevice,
    )
    assert not recorder.active
    assert streams == []

    assert recorder.start()
    stream = streams[0]
    assert recorder.active
    assert not recorder.start()
    stream.callback(np.array([[1.0], [1.0]], dtype=np.float32), 2, None, None)
    assert recorder.stop().tolist() == [1.0, 1.0]
    assert recorder.last_duration == 0.5
    assert recorder.last_rms == 1.0
    assert recorder.last_peak == 1.0
    assert not recorder.active
    assert stream.closed_event.wait(timeout=0.5)
    assert (stream.started, stream.stopped, stream.closed) == (1, 1, 1)

    assert recorder.start()
    second_stream = streams[1]
    assert second_stream is not stream
    second_stream.callback(np.array([[0.5], [0.5]], dtype=np.float32), 2, None, None)
    assert recorder.stop().tolist() == [0.5, 0.5]
    assert recorder.stop().size == 0
    recorder.close()
    recorder.close()
    assert not recorder.active
    assert second_stream.closed_event.wait(timeout=0.5)
    assert (second_stream.started, second_stream.stopped, second_stream.closed) == (1, 1, 1)


def test_recorder_reports_an_unexpected_stream_stop() -> None:
    statuses: list[str] = []
    streams: list[FakeStream] = []

    def input_stream(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        query_devices=lambda *args, **kwargs: {"name": "Default", "max_input_channels": 1},
    )
    recorder = Recorder(sounddevice_module=sounddevice, on_status=statuses.append)
    assert recorder.start()
    streams[0].callback(np.ones((2, 1), dtype=np.float32), 2, None, "input overflow")
    assert statuses == []
    streams[0].active = False
    streams[0].finished_callback()

    assert not recorder.active
    assert statuses == ["Audio input stream stopped unexpectedly"]
    recorder.close()


def test_input_device_resolution_is_exact() -> None:
    sounddevice = SimpleNamespace(
        query_devices=lambda *args, **kwargs: [
            {"name": "USB Audio", "max_input_channels": 2},
            {"name": "MacBook Pro Microphone", "max_input_channels": 1},
        ]
    )
    assert resolve_input_device(sounddevice, "MacBook Pro Microphone") == (
        1,
        "MacBook Pro Microphone",
    )
    with pytest.raises(RuntimeError, match="not found"):
        resolve_input_device(sounddevice, "Missing Mic")


def test_default_device_and_duplicate_name_handling() -> None:
    default = {"name": "System Default", "max_input_channels": 1}
    devices = [
        {"name": "USB Audio", "max_input_channels": 1},
        {"name": "USB Audio", "max_input_channels": 1},
    ]
    sounddevice = SimpleNamespace(
        query_devices=lambda *args, **kwargs: default if kwargs.get("kind") else devices
    )
    assert resolve_input_device(sounddevice, None) == (None, "System Default")
    with pytest.raises(RuntimeError, match="ambiguous"):
        resolve_input_device(sounddevice, "USB Audio")


def test_zero_preroll_and_maximum_duration_are_enforced() -> None:
    streams: list[FakeStream] = []
    limit_events: list[str] = []

    def input_stream(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        query_devices=lambda *args, **kwargs: {"name": "Default", "max_input_channels": 1},
    )
    recorder = Recorder(
        sample_rate=4,
        blocksize=2,
        maximum_recording_seconds=0.5,
        sounddevice_module=sounddevice,
        on_max_duration=lambda: limit_events.append("limit"),
    )
    assert recorder.start()
    stream = streams[0]
    stream.callback(np.ones((4, 1), dtype=np.float32), 4, None, None)
    stream.callback(np.ones((4, 1), dtype=np.float32), 4, None, None)
    assert recorder.stop().tolist() == [1.0, 1.0]
    assert limit_events == ["limit"]


def test_wall_clock_limit_fires_when_audio_callbacks_stall() -> None:
    limit_reached = threading.Event()
    sounddevice = SimpleNamespace(
        InputStream=lambda **kwargs: FakeStream(**kwargs),
        query_devices=lambda *args, **kwargs: {"name": "Default", "max_input_channels": 1},
    )
    recorder = Recorder(
        maximum_recording_seconds=0.02,
        sounddevice_module=sounddevice,
        on_max_duration=limit_reached.set,
    )

    assert recorder.start()
    assert limit_reached.wait(timeout=0.5)
    recorder.close()


def test_stopping_recorder_never_waits_for_blocked_native_audio_release() -> None:
    release_native_call = threading.Event()
    native_call_started = threading.Event()
    native_call_finished = threading.Event()

    class BlockingStream(FakeStream):
        def abort(self) -> None:
            native_call_started.set()
            release_native_call.wait(timeout=2)
            self.active = False

        def close(self) -> None:
            super().close()
            native_call_finished.set()

    streams: list[BlockingStream] = []

    def input_stream(**kwargs: Any) -> BlockingStream:
        stream = BlockingStream(**kwargs)
        streams.append(stream)
        return stream

    sounddevice = SimpleNamespace(
        InputStream=input_stream,
        query_devices=lambda *args, **kwargs: {"name": "Default", "max_input_channels": 1},
    )
    recorder = Recorder(sounddevice_module=sounddevice)
    assert recorder.start()
    streams[0].callback(np.ones((2, 1), dtype=np.float32), 2, None, None)

    stopped_audio: list[np.ndarray] = []
    stop_thread = threading.Thread(
        target=lambda: stopped_audio.append(recorder.stop()),
        daemon=True,
    )
    stop_thread.start()
    stop_thread.join(timeout=0.2)

    assert not stop_thread.is_alive()
    assert stopped_audio[0].tolist() == [1.0, 1.0]
    assert native_call_started.wait(timeout=0.5)
    release_native_call.set()
    assert native_call_finished.wait(timeout=0.5)


def test_capture_quality_reports_short_and_silent_audio(app_config: AppConfig) -> None:
    config = app_config.audio
    assert (
        capture_rejection_reason(
            duration_seconds=0.01,
            rms=1,
            device_name=config.device,
            config=config,
        )
        == "Registrazione troppo breve"
    )
    assert "Nessun segnale" in str(
        capture_rejection_reason(
            duration_seconds=1,
            rms=0,
            device_name=config.device,
            config=config,
        )
    )
    assert (
        capture_rejection_reason(
            duration_seconds=1,
            rms=1,
            device_name=config.device,
            config=config,
        )
        is None
    )


class FakeListener:
    def __init__(self, **callbacks: Any) -> None:
        self.callbacks = callbacks
        self.started = False
        self.stopped = False
        self.waited = False
        self.joined = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True

    def wait(self) -> None:
        self.waited = True

    def join(self, *, timeout: float) -> None:
        assert timeout > 0
        self.joined = True


def keyboard_module() -> Any:
    keys = SimpleNamespace(
        alt="alt",
        alt_r="alt_r",
        ctrl="ctrl",
        ctrl_r="ctrl_r",
        cmd="cmd",
        cmd_r="cmd_r",
        shift="shift",
        space="space",
        f12="f12",
        f13="f13",
    )
    return SimpleNamespace(Key=keys, Listener=lambda **kwargs: FakeListener(**kwargs))


def macos_keyboard_module() -> Any:
    class Key:
        def __init__(self, virtual_keycode: int) -> None:
            self.value = SimpleNamespace(vk=virtual_keycode)

    keys = SimpleNamespace(
        alt=Key(58),
        alt_r=Key(61),
        ctrl=Key(59),
        ctrl_r=Key(62),
        cmd=Key(55),
        cmd_r=Key(54),
        shift=Key(56),
        space=Key(49),
        f12=Key(111),
        f13=Key(105),
    )
    return SimpleNamespace(Key=keys, Listener=lambda **kwargs: FakeListener(**kwargs))


def quartz_module() -> Any:
    return SimpleNamespace(
        kCGEventKeyDown=10,
        kCGEventKeyUp=11,
        kCGKeyboardEventKeycode=9,
        CGEventGetIntegerValueField=lambda event, _field: event.virtual_keycode,
    )


def test_toggle_hotkey_fires_once_per_physical_chord() -> None:
    events: list[str] = []

    def event(name: str) -> bool:
        events.append(name)
        return True

    hotkey = PushToTalk(
        "f13",
        lambda: event("start"),
        lambda: event("stop"),
        mode="toggle",
        keyboard_module=keyboard_module(),
    )
    hotkey.start()
    assert hotkey.alive
    hotkey._press("f13")
    hotkey._press("f13")
    hotkey._release("f13")
    hotkey._press("f13")
    assert events == ["start", "stop"]
    hotkey.stop()
    assert not hotkey.alive
    hotkey._release("f13")
    hotkey._press("f13")
    assert events == ["start", "stop"]


def test_toggle_hotkey_uses_shared_recording_state_after_local_drift() -> None:
    events: list[str] = []
    recording = False

    def start() -> bool:
        nonlocal recording
        events.append("start")
        recording = True
        return True

    def stop() -> bool:
        nonlocal recording
        events.append("stop")
        recording = False
        return True

    hotkey = PushToTalk(
        "f13",
        start,
        stop,
        mode="toggle",
        keyboard_module=keyboard_module(),
        is_recording=lambda: recording,
    )
    hotkey._press("f13")
    hotkey._release("f13")

    # A display/context transition must not turn the next toggle into a new start,
    # even if the listener's local bookkeeping has lost the active state.
    hotkey.mark_recording_stopped()
    hotkey._press("f13")

    assert events == ["start", "stop"]
    assert not recording
    hotkey.stop()


def test_rejected_hotkey_start_does_not_drift_to_stop() -> None:
    events: list[str] = []

    def reject_start() -> bool:
        events.append("rejected")
        return False

    hotkey = PushToTalk(
        "f13",
        reject_start,
        lambda: events.append("stop") or True,
        mode="toggle",
        keyboard_module=keyboard_module(),
    )
    for _ in range(2):
        hotkey._press("f13")
        hotkey._release("f13")
    assert events == ["rejected", "rejected"]
    hotkey.stop()


def test_modifier_only_hotkey_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-modifier"):
        PushToTalk(
            "ctrl+shift",
            lambda: True,
            lambda: True,
            keyboard_module=keyboard_module(),
        )


def test_function_key_with_modifiers_is_supported() -> None:
    hotkey = PushToTalk(
        "ctrl+shift+f12",
        lambda: True,
        lambda: True,
        keyboard_module=keyboard_module(),
    )
    assert hotkey.chord == {"ctrl", "shift", "f12"}


def test_option_space_is_supported() -> None:
    hotkey = PushToTalk(
        "alt+space",
        lambda: True,
        lambda: True,
        keyboard_module=keyboard_module(),
    )
    assert hotkey.chord == {"alt", "space"}


def test_option_space_toggle_is_consumed_without_reaching_the_frontmost_app() -> None:
    keyboard = macos_keyboard_module()
    quartz = quartz_module()
    events: list[str] = []
    hotkey = PushToTalk(
        "alt+space",
        lambda: events.append("start") or True,
        lambda: events.append("stop") or True,
        mode="toggle",
        keyboard_module=keyboard,
        quartz_module=quartz,
        callback_dispatcher=lambda action: action(),
    )
    listener = hotkey._listener
    intercept = listener.callbacks["darwin_intercept"]
    space_event = SimpleNamespace(virtual_keycode=49)
    letter_event = SimpleNamespace(virtual_keycode=0)

    # First complete press starts recording and consumes Space down/up.
    listener.callbacks["on_press"](keyboard.Key.alt)
    listener.callbacks["on_press"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyDown, space_event) is None
    listener.callbacks["on_release"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyUp, space_event) is None
    listener.callbacks["on_release"](keyboard.Key.alt)

    # Recording remains active until the second complete press, which is also consumed.
    listener.callbacks["on_press"](keyboard.Key.alt)
    listener.callbacks["on_press"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyDown, space_event) is None
    listener.callbacks["on_release"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyUp, space_event) is None
    listener.callbacks["on_release"](keyboard.Key.alt)

    assert events == ["start", "stop"]
    assert intercept(quartz.kCGEventKeyDown, letter_event) is letter_event


def test_consumed_space_key_up_stays_consumed_if_option_is_released_first() -> None:
    keyboard = macos_keyboard_module()
    quartz = quartz_module()
    hotkey = PushToTalk(
        "alt+space",
        lambda: True,
        lambda: True,
        mode="toggle",
        keyboard_module=keyboard,
        quartz_module=quartz,
        callback_dispatcher=lambda action: action(),
    )
    listener = hotkey._listener
    intercept = listener.callbacks["darwin_intercept"]
    space_event = SimpleNamespace(virtual_keycode=49)

    listener.callbacks["on_press"](keyboard.Key.alt)
    listener.callbacks["on_press"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyDown, space_event) is None
    listener.callbacks["on_release"](keyboard.Key.alt)
    listener.callbacks["on_release"](keyboard.Key.space)

    assert intercept(quartz.kCGEventKeyUp, space_event) is None


def test_space_without_option_is_not_consumed() -> None:
    keyboard = macos_keyboard_module()
    quartz = quartz_module()
    hotkey = PushToTalk(
        "alt+space",
        lambda: True,
        lambda: True,
        mode="toggle",
        keyboard_module=keyboard,
        quartz_module=quartz,
        callback_dispatcher=lambda action: action(),
    )
    listener = hotkey._listener
    intercept = listener.callbacks["darwin_intercept"]
    space_event = SimpleNamespace(virtual_keycode=49)

    listener.callbacks["on_press"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyDown, space_event) is space_event
    listener.callbacks["on_release"](keyboard.Key.space)
    assert intercept(quartz.kCGEventKeyUp, space_event) is space_event


def test_macos_hotkey_dispatches_application_work_after_consuming_the_key() -> None:
    keyboard = macos_keyboard_module()
    quartz = quartz_module()
    pending: list[Callable[[], None]] = []
    events: list[str] = []
    hotkey = PushToTalk(
        "alt+space",
        lambda: events.append("start") or True,
        lambda: events.append("stop") or True,
        mode="toggle",
        keyboard_module=keyboard,
        quartz_module=quartz,
        callback_dispatcher=pending.append,
    )
    listener = hotkey._listener
    intercept = listener.callbacks["darwin_intercept"]
    space_event = SimpleNamespace(virtual_keycode=49)

    listener.callbacks["on_press"](keyboard.Key.alt)
    listener.callbacks["on_press"](keyboard.Key.space)

    assert events == []
    assert len(pending) == 1
    assert intercept(quartz.kCGEventKeyDown, space_event) is None

    pending.pop()()

    assert events == ["start"]


def test_macos_hold_release_waits_for_a_pending_start() -> None:
    keyboard = macos_keyboard_module()
    quartz = quartz_module()
    pending: list[Callable[[], None]] = []
    events: list[str] = []
    hotkey = PushToTalk(
        "alt+space",
        lambda: events.append("start") or True,
        lambda: events.append("stop") or True,
        mode="hold",
        keyboard_module=keyboard,
        quartz_module=quartz,
        callback_dispatcher=pending.append,
    )
    listener = hotkey._listener

    listener.callbacks["on_press"](keyboard.Key.alt)
    listener.callbacks["on_press"](keyboard.Key.space)
    listener.callbacks["on_release"](keyboard.Key.space)

    assert len(pending) == 2
    pending.pop(0)()
    pending.pop(0)()

    assert events == ["start", "stop"]


def test_hotkey_label_uses_mac_friendly_names() -> None:
    assert hotkey_label("alt+space") == "Alt/Option + Space"


class Pasteboard:
    def __init__(self) -> None:
        self.value = "original"
        self.changes = 0

    def stringForType_(self, _kind: object) -> str:
        return self.value

    def clearContents(self) -> None:
        self.value = ""
        self.changes += 1

    def setString_forType_(self, text: str, _kind: object) -> bool:
        self.value = text
        self.changes += 1
        return True

    def changeCount(self) -> int:
        return self.changes


def test_paste_injection_keeps_latest_transcription() -> None:
    events: list[tuple[Any, ...]] = []
    target = FocusTarget(1, "field")
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda value: ("source", value),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventSetFlags=lambda event, flags: events.append(("flags", event, flags)),
        CGEventPost=lambda tap, event: events.append(("post", tap, event)),
    )
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
    injector.inject(
        "new text",
        destination=InjectionDestination.focused(target),
    )
    assert pasteboard.value == "new text"
    assert len([event for event in events if event[0] == "post"]) == 2


def test_injection_requires_accessibility() -> None:
    injector = TextInjector(
        quartz=SimpleNamespace(),
        pasteboard=Pasteboard(),
        string_type="string",
        accessibility_check=lambda: False,
    )
    with pytest.raises(PermissionError, match="Accessibility"):
        injector.inject(
            "text",
            destination=InjectionDestination.focused(FocusTarget(1, "field")),
        )


def test_focus_change_keeps_text_on_clipboard_without_posting_events() -> None:
    events: list[tuple[Any, ...]] = []
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda value: ("source", value),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventSetFlags=lambda event, flags: events.append(("flags", event, flags)),
        CGEventPost=lambda tap, event: events.append(("post", tap, event)),
    )
    pasteboard = Pasteboard()
    current = FocusTarget(2, "new-field")
    injector = TextInjector(
        quartz=quartz,
        pasteboard=pasteboard,
        string_type="string",
        accessibility_check=lambda: True,
        focus_provider=lambda: current,
        focus_equal=lambda left, right: left == right,
        clipboard_clear_after_seconds=None,
    )
    with pytest.raises(FocusChangedError, match="clipboard"):
        injector.inject(
            "safe copy",
            destination=InjectionDestination.focused(FocusTarget(1, "old-field")),
        )
    assert pasteboard.value == "safe copy"
    assert events == []


def test_clipboard_only_destination_never_posts_events() -> None:
    events: list[tuple[Any, ...]] = []
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda value: ("source", value),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventSetFlags=lambda event, flags: events.append(("flags", event, flags)),
        CGEventPost=lambda tap, event: events.append(("post", tap, event)),
    )
    pasteboard = Pasteboard()
    injector = TextInjector(
        quartz=quartz,
        pasteboard=pasteboard,
        string_type="string",
        accessibility_check=lambda: True,
        clipboard_clear_after_seconds=None,
    )

    injector.inject(
        "clipboard fallback",
        destination=InjectionDestination.clipboard_only(),
    )

    assert pasteboard.value == "clipboard fallback"
    assert events == []


def test_clipboard_is_cleared_only_if_transcription_is_still_current() -> None:
    target = FocusTarget(1, "field")
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGEventFlagMaskCommand=2,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda value: ("source", value),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventSetFlags=lambda _event, _flags: None,
        CGEventPost=lambda _tap, _event: None,
    )
    pasteboard = Pasteboard()
    injector = TextInjector(
        quartz=quartz,
        pasteboard=pasteboard,
        string_type="string",
        accessibility_check=lambda: True,
        focus_provider=lambda: target,
        focus_equal=lambda left, right: left == right,
        clipboard_clear_after_seconds=0.01,
        dispatch=lambda function, *args: function(*args),
    )
    destination = InjectionDestination.focused(target)
    injector.inject("temporary transcript", destination=destination)
    deadline = time.monotonic() + 0.2
    while pasteboard.value and time.monotonic() < deadline:
        time.sleep(0.005)
    assert pasteboard.value == ""

    injector.inject("second transcript", destination=destination)
    pasteboard.clearContents()
    pasteboard.setString_forType_("user value", "string")
    time.sleep(0.03)
    assert pasteboard.value == "user value"
    injector.close()


def test_unicode_typing_is_chunked_without_splitting_non_bmp_characters() -> None:
    unicode_events: list[tuple[int, str]] = []
    target = FocusTarget(1, "field")
    quartz = SimpleNamespace(
        kCGEventSourceStateHIDSystemState=1,
        kCGHIDEventTap=3,
        CGEventSourceCreate=lambda value: ("source", value),
        CGEventCreateKeyboardEvent=lambda source, key, down: (source, key, down),
        CGEventKeyboardSetUnicodeString=lambda _event, length, text: unicode_events.append(
            (length, text)
        ),
        CGEventPost=lambda _tap, _event: None,
    )
    injector = TextInjector(
        method="type",
        quartz=quartz,
        pasteboard=Pasteboard(),
        string_type="string",
        accessibility_check=lambda: True,
        focus_provider=lambda: target,
        focus_equal=lambda left, right: left == right,
    )
    text = "a" * 25 + "🙂" + "b" * 25
    injector.inject(
        text,
        destination=InjectionDestination.focused(target),
    )
    down_events = unicode_events[::2]
    assert "".join(chunk for _length, chunk in down_events) == text
    assert all(length <= 20 for length, _chunk in down_events)


def test_permission_status_lists_missing_permissions() -> None:
    status = check_permissions(
        quartz=SimpleNamespace(CGPreflightListenEventAccess=lambda: False),
        accessibility_check=lambda: True,
        microphone_check=lambda: True,
    )
    assert not status.ready
    assert status.missing == ("Input Monitoring",)


def test_microphone_permission_request_and_status() -> None:
    decisions: list[bool] = []

    class Device:
        @staticmethod
        def authorizationStatusForMediaType_(_media_type: object) -> int:
            return 0

        @staticmethod
        def requestAccessForMediaType_completionHandler_(
            _media_type: object,
            callback: Any,
        ) -> None:
            callback(True)

    avfoundation = SimpleNamespace(
        AVCaptureDevice=Device,
        AVMediaTypeAudio="audio",
        AVAuthorizationStatusAuthorized=3,
        AVAuthorizationStatusNotDetermined=0,
    )
    assert not microphone_is_authorized(
        request=True,
        callback=decisions.append,
        avfoundation=avfoundation,
    )
    assert decisions == [True]
