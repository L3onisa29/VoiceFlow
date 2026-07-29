from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import wave
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np


@dataclass(frozen=True, slots=True)
class RecentAudio:
    identifier: str
    sample_rate: int
    expires_at: float


class RecentAudioBuffer:
    def __init__(
        self, ttl_seconds: int = 300, *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = clock
        self._lock = threading.RLock()
        self._metadata: RecentAudio | None = None
        self._audio: np.ndarray | None = None

    def store(self, identifier: str, audio: np.ndarray, sample_rate: int) -> None:
        with self._lock:
            self.clear()
            self._audio = np.asarray(audio, dtype=np.float32).reshape(-1).copy()
            self._metadata = RecentAudio(
                identifier=identifier,
                sample_rate=sample_rate,
                expires_at=self._clock() + self.ttl_seconds,
            )

    def get(self, identifier: str) -> tuple[np.ndarray, int] | None:
        with self._lock:
            if (
                self._metadata is None
                or self._audio is None
                or self._metadata.identifier != identifier
                or self._clock() >= self._metadata.expires_at
            ):
                self.clear()
                return None
            return self._audio.copy(), self._metadata.sample_rate

    def clear(self) -> None:
        with self._lock:
            if self._audio is not None:
                self._audio.fill(0)
            self._audio = None
            self._metadata = None

    def save_confirmed(
        self,
        identifier: str,
        directory: Path,
        *,
        reference: str,
        required_terms: tuple[str, ...] = (),
        confirmed: bool,
    ) -> Path | None:
        if not confirmed:
            return None
        with self._lock:
            item = self.get(identifier)
            if item is None:
                raise ValueError("Recent audio is missing or expired")
            audio, sample_rate = item
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
        output = directory / f"{identifier or uuid4()}.wav"
        if output.exists():
            output = directory / f"{identifier}-{uuid4()}.wav"
        pcm = (np.clip(audio, -1, 1) * np.iinfo(np.int16).max).astype(np.int16).tobytes()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".voicebud-audio-", suffix=".wav", dir=directory
        )
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            with wave.open(str(temporary), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(sample_rate)
                wav.writeframes(pcm)
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
            try:
                self._append_manifest(
                    directory / "manifest.jsonl",
                    {
                        "id": identifier,
                        "audio": output.name,
                        "reference": reference,
                        "required_terms": required_terms,
                    },
                )
            except Exception:
                output.unlink(missing_ok=True)
                raise
        finally:
            temporary.unlink(missing_ok=True)
        with self._lock:
            if self._metadata is not None and self._metadata.identifier == identifier:
                self.clear()
        return output

    @staticmethod
    def _append_manifest(path: Path, record: dict[str, object]) -> None:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
        os.close(descriptor)
        temporary = Path(temporary_name)
        try:
            temporary.write_text(
                existing + json.dumps(record, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)
