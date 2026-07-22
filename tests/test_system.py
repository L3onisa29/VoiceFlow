from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np

from voiceflow.audio.recorder import Recorder
from voiceflow.system.hotkey import PushToTalk
from voiceflow.system.text_injector import TextInjector


class FakeStream:
    def __init__(self, **kwargs: Any) -> None:
        self.callback = kwargs["callback"]
        self.started = 0
        self.stopped = 0
        self.closed = 0

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1

    def close(self) -> None:
        self.closed += 1


def test_recorder_lifecycle_and_preroll_are_idempotent() -> None:
    streams: list[FakeStream] = []

    def input_stream(**kwargs: Any) -> FakeStream:
        stream = FakeStream(**kwargs)
        streams.append(stream)
        return stream

    recorder = Recorder(
        sample_rate=4,
        channels=1,
        preroll_ms=500,
        blocksize=2,
        sounddevice_module=SimpleNamespace(InputStream=input_stream),
    )
    recorder.start_stream()
    recorder.start_stream()
    stream = streams[0]
    stream.callback(np.array([[0.5], [0.5]], dtype=np.float32), 2, None, None)
    assert recorder.start()
    assert not recorder.start()
    stream.callback(np.array([[1.0], [1.0]], dtype=np.float32), 2, None, None)
    assert recorder.stop().tolist() == [0.5, 0.5, 1.0, 1.0]
    assert recorder.stop().size == 0
    recorder.close()
    recorder.close()
    assert (stream.started, stream.stopped, stream.closed) == (1, 1, 1)


class FakeListener:
    def __init__(self, **callbacks: Any) -> None:
        self.callbacks = callbacks
        self.started = False
        self.stopped = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.stopped = True


def keyboard_module() -> Any:
    keys = SimpleNamespace(
        alt="alt",
        alt_r="alt_r",
        ctrl="ctrl",
        ctrl_r="ctrl_r",
        cmd="cmd",
        cmd_r="cmd_r",
        shift="shift",
        f13="f13",
    )
    return SimpleNamespace(Key=keys, Listener=lambda **kwargs: FakeListener(**kwargs))


def test_toggle_hotkey_fires_once_per_physical_chord() -> None:
    events: list[str] = []
    hotkey = PushToTalk(
        "ctrl+shift",
        lambda: events.append("start"),
        lambda: events.append("stop"),
        mode="toggle",
        keyboard_module=keyboard_module(),
    )
    hotkey.start()
    hotkey._press("ctrl")
    hotkey._press("shift")
    hotkey._press("shift")
    hotkey._release("shift")
    hotkey._press("shift")
    assert events == ["start", "stop"]
    hotkey.stop()
    hotkey._release("shift")
    hotkey._press("shift")
    assert events == ["start", "stop"]


class Pasteboard:
    def __init__(self) -> None:
        self.value = "original"

    def stringForType_(self, _kind: object) -> str:
        return self.value

    def clearContents(self) -> None:
        self.value = ""

    def setString_forType_(self, text: str, _kind: object) -> None:
        self.value = text


def test_paste_injection_restores_clipboard(monkeypatch: Any) -> None:
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
    monkeypatch.setattr("voiceflow.system.text_injector.time.sleep", lambda _seconds: None)
    injector = TextInjector(
        quartz=quartz,
        pasteboard=pasteboard,
        string_type="string",
        restore_clipboard=True,
    )
    injector.inject("new text")
    assert pasteboard.value == "original"
    assert len([event for event in events if event[0] == "post"]) == 2

