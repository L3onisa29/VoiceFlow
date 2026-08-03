from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import wave
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import numpy as np

from voiceflow.storage.permissions import (
    ensure_private_directory,
    read_managed_text_file,
    replace_private_file,
    restrict_file_permissions,
    restrict_open_file,
)


@dataclass(frozen=True, slots=True)
class RecentAudio:
    identifier: str
    sample_rate: int
    expires_at: float


class RecentAudioBuffer:
    @classmethod
    def for_pipeline(
        cls,
        ttl_seconds: int,
        *,
        max_pending: int,
        clock: Callable[[], float] = time.monotonic,
    ) -> RecentAudioBuffer:
        return cls(
            ttl_seconds,
            capacity=max_pending + 1,
            clock=clock,
        )

    def __init__(
        self,
        ttl_seconds: int = 300,
        *,
        capacity: int = 4,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self._clock = clock
        self._lock = threading.RLock()
        self._manifest_lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[RecentAudio, np.ndarray]] = OrderedDict()

    def store(self, identifier: str, audio: np.ndarray, sample_rate: int) -> None:
        with self._lock:
            now = self._clock()
            self._prune_expired(now)
            self._discard(identifier)
            while len(self._entries) >= self.capacity:
                _identifier, (_metadata, evicted_audio) = self._entries.popitem(last=False)
                evicted_audio.fill(0)
            self._entries[identifier] = (
                RecentAudio(
                    identifier=identifier,
                    sample_rate=sample_rate,
                    expires_at=now + self.ttl_seconds,
                ),
                np.asarray(audio, dtype=np.float32).reshape(-1).copy(),
            )

    def get(self, identifier: str) -> tuple[np.ndarray, int] | None:
        with self._lock:
            self._prune_expired(self._clock())
            entry = self._entries.get(identifier)
            if entry is None:
                return None
            metadata, audio = entry
            return audio.copy(), metadata.sample_rate

    def clear(self) -> None:
        with self._lock:
            for _metadata, audio in self._entries.values():
                audio.fill(0)
            self._entries.clear()

    def _prune_expired(self, now: float) -> None:
        expired = [
            identifier
            for identifier, (metadata, _audio) in self._entries.items()
            if now >= metadata.expires_at
        ]
        for identifier in expired:
            self._discard(identifier)

    def _discard(self, identifier: str) -> None:
        entry = self._entries.pop(identifier, None)
        if entry is not None:
            _metadata, audio = entry
            audio.fill(0)

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
            self._prune_expired(self._clock())
            entry = self._entries.get(identifier)
            if entry is None:
                raise ValueError("Recent audio is missing or expired")
            metadata, buffered_audio = entry
            audio = buffered_audio.copy()
            sample_rate = metadata.sample_rate
        ensure_private_directory(directory)
        output = directory / f"{identifier or uuid4()}.wav"
        if restrict_file_permissions(output):
            output = directory / f"{identifier}-{uuid4()}.wav"
        pcm = (np.clip(audio, -1, 1) * np.iinfo(np.int16).max).astype(np.int16).tobytes()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".voicebud-audio-", suffix=".wav", dir=directory
        )
        temporary = Path(temporary_name)
        temporary_owned = True
        try:
            try:
                restrict_open_file(descriptor)
                with (
                    os.fdopen(descriptor, "w+b", closefd=False) as temporary_file,
                    wave.open(temporary_file, "wb") as wav,
                ):
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sample_rate)
                    wav.writeframes(pcm)
            finally:
                os.close(descriptor)
            replace_private_file(temporary, output)
            temporary_owned = False
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
            if temporary_owned:
                temporary.unlink(missing_ok=True)
        with self._lock:
            current = self._entries.get(identifier)
            if current is not None and current[0] is metadata:
                self._discard(identifier)
        return output

    def _append_manifest(self, path: Path, record: dict[str, object]) -> None:
        # This lock owns the complete filesystem read-modify-replace transaction.
        # save_confirmed never holds the in-memory buffer lock while acquiring it.
        with self._manifest_lock:
            existing = read_managed_text_file(path)
            descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
            temporary = Path(temporary_name)
            temporary_owned = True
            try:
                try:
                    restrict_open_file(descriptor)
                    with os.fdopen(
                        descriptor,
                        "w",
                        encoding="utf-8",
                        closefd=False,
                    ) as temporary_file:
                        temporary_file.write(
                            existing + json.dumps(record, ensure_ascii=False) + "\n"
                        )
                finally:
                    os.close(descriptor)
                replace_private_file(temporary, path)
                temporary_owned = False
            finally:
                if temporary_owned:
                    temporary.unlink(missing_ok=True)
