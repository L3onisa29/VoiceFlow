from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest

from voiceflow.audio.recorder import Recorder
from voiceflow.audio.release import SATURATION_MESSAGE, AudioReleaseCoordinator


class NativeStream:
    def __init__(
        self,
        *,
        abort_gate: threading.Event | None = None,
        close_gate: threading.Event | None = None,
        **kwargs: Any,
    ) -> None:
        self.callback = kwargs.get("callback")
        self.finished_callback = kwargs.get("finished_callback")
        self.abort_gate = abort_gate
        self.close_gate = close_gate
        self.abort_entered = threading.Event()
        self.close_entered = threading.Event()
        self.closed = threading.Event()
        self.calls: list[str] = []
        self.active = False

    def start(self) -> None:
        self.calls.append("start")
        self.active = True

    def abort(self) -> None:
        self.calls.append("abort")
        self.abort_entered.set()
        if self.abort_gate is not None:
            self.abort_gate.wait()
        self.active = False

    def stop(self) -> None:
        self.calls.append("stop")
        self.active = False

    def close(self) -> None:
        self.calls.append("close")
        self.close_entered.set()
        if self.close_gate is not None:
            self.close_gate.wait()
        self.closed.set()


def sounddevice_with(factory: Any) -> Any:
    return SimpleNamespace(
        InputStream=factory,
        query_devices=lambda *args, **kwargs: {"name": "Default", "max_input_channels": 1},
    )


def wait_for_pending(coordinator: AudioReleaseCoordinator, expected: int) -> None:
    with coordinator._condition:
        assert coordinator._condition.wait_for(
            lambda: len(coordinator._slots) == expected,
            timeout=1,
        )


def test_normal_native_release_is_ordered_and_tracked() -> None:
    coordinator = AudioReleaseCoordinator()
    stream = NativeStream()
    slot = coordinator.reserve()
    assert slot is not None
    assert coordinator.pending == 1

    coordinator.release(slot, stream)
    coordinator.close(timeout=1)

    assert stream.calls == ["abort", "close"]
    assert coordinator.pending == 0
    assert not coordinator.saturated
    assert coordinator._threads == set()


def test_release_falls_back_to_stop_and_still_closes() -> None:
    coordinator = AudioReleaseCoordinator()
    stream = SimpleNamespace(stop=lambda: calls.append("stop"), close=lambda: calls.append("close"))
    calls: list[str] = []
    slot = coordinator.reserve()
    assert slot is not None

    coordinator.release(slot, stream)
    coordinator.close(timeout=1)

    assert calls == ["stop", "close"]


def test_blocked_abort_and_close_keep_exactly_two_slots_and_threads() -> None:
    abort_gate = threading.Event()
    close_gate = threading.Event()
    streams = [
        NativeStream(abort_gate=abort_gate),
        NativeStream(close_gate=close_gate),
    ]
    coordinator = AudioReleaseCoordinator()
    first_slot = coordinator.reserve()
    second_slot = coordinator.reserve()
    assert first_slot is not None and second_slot is not None

    coordinator.release(first_slot, streams[0])
    assert streams[0].abort_entered.wait(timeout=1)
    coordinator.release(second_slot, streams[1], unexpectedly_stopped=True)
    assert streams[1].close_entered.wait(timeout=1)

    assert coordinator.pending == 2
    assert coordinator.saturated
    assert len(coordinator._threads) == 2
    assert all(thread.daemon for thread in coordinator._threads)

    abort_gate.set()
    close_gate.set()
    coordinator.close(timeout=1)
    assert coordinator.pending == 0


def test_saturation_rejects_third_recording_without_stream_or_thread() -> None:
    release_gate = threading.Event()
    streams: list[NativeStream] = []
    messages: list[str] = []

    def input_stream(**kwargs: Any) -> NativeStream:
        stream = NativeStream(abort_gate=release_gate, **kwargs)
        streams.append(stream)
        return stream

    coordinator = AudioReleaseCoordinator(on_saturated=messages.append)
    recorder = Recorder(
        sounddevice_module=sounddevice_with(input_stream),
        release_coordinator=coordinator,
    )
    assert recorder.start()
    recorder.stop()
    assert streams[0].abort_entered.wait(timeout=1)
    assert recorder.start()
    recorder.stop()
    assert streams[1].abort_entered.wait(timeout=1)
    release_threads = set(coordinator._threads)

    assert not recorder.start()

    assert len(streams) == 2
    assert coordinator._threads == release_threads
    assert len(release_threads) == 2
    assert coordinator.pending == 2
    assert coordinator.saturated
    assert messages == [SATURATION_MESSAGE]

    release_gate.set()
    coordinator.close(timeout=1)
    recorder.close()


def test_completed_release_returns_slot_for_another_stream() -> None:
    coordinator = AudioReleaseCoordinator()
    first = coordinator.reserve()
    second = coordinator.reserve()
    assert first is not None and second is not None
    coordinator.release(first, NativeStream())
    wait_for_pending(coordinator, 1)

    replacement = coordinator.reserve()

    assert replacement is not None
    assert coordinator.pending == 2
    coordinator.discard(second)
    coordinator.discard(replacement)
    assert coordinator.pending == 0


def test_input_stream_construction_failure_immediately_returns_slot() -> None:
    coordinator = AudioReleaseCoordinator()
    recorder = Recorder(
        sounddevice_module=sounddevice_with(
            lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("construction failed"))
        ),
        release_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="construction failed"):
        recorder.start()

    assert coordinator.pending == 0
    assert coordinator._threads == set()


def test_stream_start_failure_releases_slot_after_native_cleanup() -> None:
    coordinator = AudioReleaseCoordinator()

    class FailingStartStream(NativeStream):
        def start(self) -> None:
            raise RuntimeError("start failed")

    stream = FailingStartStream()
    recorder = Recorder(
        sounddevice_module=sounddevice_with(lambda **kwargs: stream),
        release_coordinator=coordinator,
    )

    with pytest.raises(RuntimeError, match="start failed"):
        recorder.start()
    coordinator.close(timeout=1)

    assert stream.calls == ["close"]
    assert coordinator.pending == 0


def test_unexpected_finish_closes_without_abort_and_notifies_once() -> None:
    statuses: list[str] = []
    streams: list[NativeStream] = []
    coordinator = AudioReleaseCoordinator()

    def input_stream(**kwargs: Any) -> NativeStream:
        stream = NativeStream(**kwargs)
        streams.append(stream)
        return stream

    recorder = Recorder(
        sounddevice_module=sounddevice_with(input_stream),
        release_coordinator=coordinator,
        on_status=statuses.append,
    )
    assert recorder.start()

    streams[0].finished_callback()
    coordinator.close(timeout=1)

    assert streams[0].calls == ["start", "close"]
    assert statuses == ["Audio input stream stopped unexpectedly"]
    assert not recorder.recording


def test_close_is_idempotent_and_does_not_wait_past_zero_timeout() -> None:
    gate = threading.Event()
    stream = NativeStream(abort_gate=gate)
    coordinator = AudioReleaseCoordinator()
    slot = coordinator.reserve()
    assert slot is not None
    coordinator.release(slot, stream)
    assert stream.abort_entered.wait(timeout=1)
    calls_returned = threading.Event()

    def close_twice() -> None:
        coordinator.close(timeout=0)
        coordinator.close(timeout=0)
        calls_returned.set()

    worker = threading.Thread(target=close_twice, daemon=True)
    worker.start()
    assert calls_returned.wait(timeout=1)
    assert coordinator.pending == 1
    assert coordinator.reserve() is None

    gate.set()
    worker.join(timeout=1)
    wait_for_pending(coordinator, 0)


def test_close_waits_for_a_release_only_until_it_completes() -> None:
    gate = threading.Event()
    stream = NativeStream(close_gate=gate)
    coordinator = AudioReleaseCoordinator()
    slot = coordinator.reserve()
    assert slot is not None
    coordinator.release(slot, stream, unexpectedly_stopped=True)
    assert stream.close_entered.wait(timeout=1)
    close_returned = threading.Event()
    closer = threading.Thread(
        target=lambda: (coordinator.close(timeout=1), close_returned.set()),
        daemon=True,
    )
    closer.start()

    assert not close_returned.is_set()
    gate.set()
    assert close_returned.wait(timeout=1)
    closer.join(timeout=1)
    assert coordinator.pending == 0


def test_duplicate_or_stale_release_requests_do_not_spawn_threads() -> None:
    gate = threading.Event()
    stream = NativeStream(abort_gate=gate)
    coordinator = AudioReleaseCoordinator()
    slot = coordinator.reserve()
    assert slot is not None
    coordinator.release(slot, stream)
    assert stream.abort_entered.wait(timeout=1)
    threads = set(coordinator._threads)

    coordinator.release(slot, stream)
    coordinator.release(object(), NativeStream())
    coordinator.discard(object())

    assert coordinator._threads == threads
    assert len(threads) == 1
    gate.set()
    coordinator.close(timeout=1)


def test_native_release_failures_are_logged_and_return_the_slot(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FailingStream:
        def abort(self) -> None:
            raise RuntimeError("abort failed")

        def close(self) -> None:
            raise RuntimeError("close failed")

    coordinator = AudioReleaseCoordinator()
    slot = coordinator.reserve()
    assert slot is not None

    coordinator.release(slot, FailingStream())
    coordinator.close(timeout=1)

    assert coordinator.pending == 0
    assert "Failed to abort audio input stream" in caplog.text
    assert "Failed to close audio input stream" in caplog.text


def test_thread_start_failure_returns_the_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class BrokenThread:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("thread failed")

    coordinator = AudioReleaseCoordinator()
    slot = coordinator.reserve()
    assert slot is not None
    monkeypatch.setattr("voiceflow.audio.release.threading.Thread", BrokenThread)

    with pytest.raises(RuntimeError, match="thread failed"):
        coordinator.release(slot, NativeStream())

    assert coordinator.pending == 0
    assert coordinator._threads == set()


def test_saturation_callback_failure_is_isolated(caplog: pytest.LogCaptureFixture) -> None:
    coordinator = AudioReleaseCoordinator(
        on_saturated=lambda _message: (_ for _ in ()).throw(RuntimeError("callback failed"))
    )
    slots = [coordinator.reserve(), coordinator.reserve()]
    assert all(slot is not None for slot in slots)

    assert coordinator.reserve() is None

    assert "Audio release saturation callback failed" in caplog.text
    for slot in slots:
        assert slot is not None
        coordinator.discard(slot)


def test_negative_close_timeout_is_rejected_without_closing_coordinator() -> None:
    coordinator = AudioReleaseCoordinator()

    with pytest.raises(ValueError, match="non-negative"):
        coordinator.close(timeout=-1)

    slot = coordinator.reserve()
    assert slot is not None
    coordinator.discard(slot)
