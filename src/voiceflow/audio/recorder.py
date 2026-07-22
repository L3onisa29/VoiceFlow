from __future__ import annotations

import collections
import threading
from typing import Any

import numpy as np


class Recorder:
    """Continuous microphone capture with a pre-roll ring buffer."""

    def __init__(
        self,
        sample_rate: int = 16000,
        channels: int = 1,
        preroll_ms: int = 500,
        blocksize: int = 320,
        *,
        sounddevice_module: Any | None = None,
    ) -> None:
        if sounddevice_module is None:
            import sounddevice as sounddevice_module

        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        preroll_blocks = max(1, int(sample_rate * preroll_ms / 1000 / blocksize))
        self._preroll: collections.deque[np.ndarray] = collections.deque(
            maxlen=preroll_blocks
        )
        self._chunks: list[np.ndarray] = []
        self._recording = False
        self._started = False
        self._closed = False
        self.level = 0.0
        self._lock = threading.RLock()
        self._stream = sounddevice_module.InputStream(
            samplerate=sample_rate,
            channels=channels,
            dtype="float32",
            blocksize=blocksize,
            callback=self._callback,
        )

    @property
    def recording(self) -> bool:
        with self._lock:
            return self._recording

    def _callback(
        self, indata: np.ndarray, _frames: int, _time_info: object, _status: object
    ) -> None:
        block = indata.copy()
        with self._lock:
            if self._recording:
                self._chunks.append(block)
                self.level = float(np.sqrt(np.mean(block**2)))
            else:
                self._preroll.append(block)

    def start_stream(self) -> None:
        with self._lock:
            if self._closed:
                raise RuntimeError("Recorder is closed")
            if self._started:
                return
            self._stream.start()
            self._started = True

    def start(self) -> bool:
        with self._lock:
            if self._closed or not self._started or self._recording:
                return False
            self._chunks = list(self._preroll)
            self._preroll.clear()
            self._recording = True
            return True

    def stop(self) -> np.ndarray:
        with self._lock:
            if not self._recording:
                return np.zeros(0, dtype=np.float32)
            self._recording = False
            self.level = 0.0
            chunks, self._chunks = self._chunks, []
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32, copy=False).reshape(-1)

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            started = self._started
            self._started = False
            self._recording = False
        if started:
            self._stream.stop()
        self._stream.close()
