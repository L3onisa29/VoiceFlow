from __future__ import annotations

import logging
import threading
from typing import Any

import numpy as np

from voiceflow.audio.release import AudioReleaseCoordinator

LOGGER = logging.getLogger(__name__)


def resolve_input_device(sounddevice_module: Any, requested: str | int | None) -> tuple[Any, str]:
    if requested is None:
        device = sounddevice_module.query_devices(kind="input")
        return None, str(device["name"])
    if isinstance(requested, int):
        device = sounddevice_module.query_devices(requested, kind="input")
        return requested, str(device["name"])

    devices = sounddevice_module.query_devices()
    matches = [
        (index, device)
        for index, device in enumerate(devices)
        if int(device["max_input_channels"]) > 0
        and str(device["name"]).casefold() == requested.casefold()
    ]
    if not matches:
        available = ", ".join(
            str(device["name"]) for device in devices if int(device["max_input_channels"]) > 0
        )
        raise RuntimeError(
            f"Input device {requested!r} not found. Available input devices: {available or 'none'}"
        )
    if len(matches) > 1:
        indices = ", ".join(str(index) for index, _device in matches)
        raise RuntimeError(
            f"Input device name {requested!r} is ambiguous; use one of these indices: {indices}"
        )
    index, device = matches[0]
    return index, str(device["name"])


class Recorder:
    """Microphone capture whose input stream exists only while recording."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        blocksize: int = 320,
        device: str | int | None = None,
        maximum_recording_seconds: float = 120,
        *,
        sounddevice_module: Any | None = None,
        on_max_duration: Any | None = None,
        on_status: Any | None = None,
        release_coordinator: AudioReleaseCoordinator | None = None,
    ) -> None:
        if sounddevice_module is None:
            import sounddevice as sounddevice_module

        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.device, self.device_name = resolve_input_device(sounddevice_module, device)
        self._sounddevice = sounddevice_module
        self._maximum_samples = int(sample_rate * maximum_recording_seconds)
        self._maximum_recording_seconds = maximum_recording_seconds
        self._on_max_duration = on_max_duration
        self._on_status = on_status
        self._release_coordinator = release_coordinator or AudioReleaseCoordinator()
        self._owns_release_coordinator = release_coordinator is None
        self._chunks: list[np.ndarray] = []
        self._active_samples = 0
        self._active_square_sum = 0.0
        self._active_peak = 0.0
        self._limit_notified = False
        self._recording = False
        self._closed = False
        self._stream: Any | None = None
        self._stream_token: object | None = None
        self._stream_release_slot: object | None = None
        self._duration_timer: threading.Timer | None = None
        self.level = 0.0
        self.last_rms = 0.0
        self.last_peak = 0.0
        self.last_duration = 0.0
        self._lock = threading.RLock()

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    @property
    def active(self) -> bool:
        with self._lock:
            stream = self._stream
            return bool(
                not self._closed and stream is not None and getattr(stream, "active", False)
            )

    def _finished_callback(self, token: object) -> None:
        with self._lock:
            if self._closed or token is not self._stream_token:
                return
            stream = self._stream
            release_slot = self._stream_release_slot
            self._stream = None
            self._stream_token = None
            self._stream_release_slot = None
            self._recording = False
            timer = self._duration_timer
            self._duration_timer = None
            self.level = 0.0
            chunks, self._chunks = self._chunks, []
        if timer is not None:
            timer.cancel()
        self._zero_chunks(chunks)
        self._release_stream(release_slot, stream, unexpectedly_stopped=True)
        if self._on_status is not None:
            self._on_status("Audio input stream stopped unexpectedly")

    def _duration_limit_reached(self, token: object) -> None:
        with self._lock:
            if (
                self._closed
                or not self._recording
                or token is not self._stream_token
                or self._limit_notified
            ):
                return
            self._limit_notified = True
        if self._on_max_duration is not None:
            self._on_max_duration()

    def _callback(
        self,
        token: object,
        indata: np.ndarray,
        _frames: int,
        _time_info: object,
        status: object,
    ) -> None:
        if status:
            LOGGER.warning("Audio input status: %s", status)
        block = indata.copy().reshape(-1)
        notify_limit = False
        with self._lock:
            if not self._recording or token is not self._stream_token:
                return
            remaining = self._maximum_samples - self._active_samples
            if remaining > 0:
                active = block[:remaining]
                self._chunks.append(active.copy())
                self._active_samples += active.size
                self._active_square_sum += float(np.dot(active, active))
                if active.size:
                    self._active_peak = max(self._active_peak, float(np.max(np.abs(active))))
                    self.level = float(np.sqrt(np.mean(active**2)))
            if self._active_samples >= self._maximum_samples and not self._limit_notified:
                self._limit_notified = True
                notify_limit = True
        if notify_limit and self._on_max_duration is not None:
            self._on_max_duration()

    def start(self) -> bool:
        with self._lock:
            if self._closed or self._recording:
                return False
            release_slot = self._release_coordinator.reserve()
            if release_slot is None:
                return False
            self._chunks = []
            self.last_duration = 0.0
            self.last_rms = 0.0
            self.last_peak = 0.0
            self._active_samples = 0
            self._active_square_sum = 0.0
            self._active_peak = 0.0
            self._limit_notified = False
            token = object()
            try:
                stream = self._sounddevice.InputStream(
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype="float32",
                    blocksize=self.blocksize,
                    device=self.device,
                    callback=lambda indata, frames, time_info, status: self._callback(
                        token,
                        indata,
                        frames,
                        time_info,
                        status,
                    ),
                    finished_callback=lambda: self._finished_callback(token),
                )
            except Exception:
                self._release_coordinator.discard(release_slot)
                raise
            self._stream = stream
            self._stream_token = token
            self._stream_release_slot = release_slot
            self._recording = True
            timer = threading.Timer(
                self._maximum_recording_seconds,
                self._duration_limit_reached,
                args=(token,),
            )
            timer.name = "voiceflow-recording-limit"
            timer.daemon = True
            self._duration_timer = timer
        try:
            stream.start()
        except Exception:
            with self._lock:
                if token is self._stream_token:
                    self._stream = None
                    self._stream_token = None
                    self._stream_release_slot = None
                    self._recording = False
                    self._duration_timer = None
                    self.level = 0.0
            timer.cancel()
            self._release_stream(release_slot, stream, unexpectedly_stopped=True)
            raise
        else:
            timer.start()
            return True

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            self._recording = False
            stream = self._stream
            release_slot = self._stream_release_slot
            self._stream = None
            self._stream_token = None
            self._stream_release_slot = None
            timer = self._duration_timer
            self._duration_timer = None
            self.level = 0.0
            chunks, self._chunks = self._chunks, []
            active_samples = self._active_samples
            active_square_sum = self._active_square_sum
            active_peak = self._active_peak
            self.last_duration = active_samples / self.sample_rate
            self.last_rms = (
                float(np.sqrt(active_square_sum / active_samples)) if active_samples else 0.0
            )
            self.last_peak = active_peak
        if timer is not None:
            timer.cancel()
        self._release_stream(release_slot, stream)
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        try:
            return np.concatenate(chunks).astype(np.float32, copy=False).reshape(-1)
        finally:
            self._zero_chunks(chunks)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._recording = False
            stream = self._stream
            release_slot = self._stream_release_slot
            self._stream = None
            self._stream_token = None
            self._stream_release_slot = None
            timer = self._duration_timer
            self._duration_timer = None
            self.level = 0.0
            chunks, self._chunks = self._chunks, []
        if timer is not None:
            timer.cancel()
        self._zero_chunks(chunks)
        self._release_stream(release_slot, stream)
        if self._owns_release_coordinator:
            self._release_coordinator.close(timeout=0)

    @staticmethod
    def _zero_chunks(chunks: list[np.ndarray]) -> None:
        for chunk in chunks:
            chunk.fill(0)

    def _release_stream(
        self,
        release_slot: object | None,
        stream: Any | None,
        *,
        unexpectedly_stopped: bool = False,
    ) -> None:
        if release_slot is None:
            return
        if stream is None:
            self._release_coordinator.discard(release_slot)
            return
        self._release_coordinator.release(
            release_slot,
            stream,
            unexpectedly_stopped=unexpectedly_stopped,
        )
