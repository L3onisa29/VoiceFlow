from __future__ import annotations

import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from voiceflow.storage.feedback_corpus import (
    FeedbackCorpusRepository,
    FeedbackSample,
    FileFeedbackCorpusRepository,
)

LOGGER = logging.getLogger(__name__)


class _ExpiryTimer(Protocol):
    daemon: bool

    def start(self) -> None: ...

    def cancel(self) -> None: ...


type _TimerFactory = Callable[[float, Callable[[], None]], _ExpiryTimer]


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
        corpus_directory: Path | None = None,
        timer_factory: _TimerFactory = threading.Timer,
    ) -> RecentAudioBuffer:
        repository = FileFeedbackCorpusRepository()
        if corpus_directory is not None:
            try:
                removed = repository.cleanup_stale_transactions(corpus_directory)
            except Exception:
                LOGGER.exception("Stale feedback transaction cleanup failed")
            else:
                if removed:
                    LOGGER.info("Removed %d stale feedback transaction file(s)", removed)
        return cls(
            ttl_seconds,
            capacity=max_pending + 1,
            clock=clock,
            repository=repository,
            timer_factory=timer_factory,
        )

    def __init__(
        self,
        ttl_seconds: int = 300,
        *,
        capacity: int = 4,
        clock: Callable[[], float] = time.monotonic,
        repository: FeedbackCorpusRepository | None = None,
        timer_factory: _TimerFactory = threading.Timer,
    ) -> None:
        if capacity < 1:
            raise ValueError("capacity must be positive")
        self.ttl_seconds = ttl_seconds
        self.capacity = capacity
        self._clock = clock
        self._repository = repository if repository is not None else FileFeedbackCorpusRepository()
        self._timer_factory = timer_factory
        self._lock = threading.RLock()
        self._entries: OrderedDict[str, tuple[RecentAudio, np.ndarray]] = OrderedDict()
        self._expiry_timer: tuple[object, _ExpiryTimer] | None = None

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
            self._schedule_expiry_locked()

    def get(self, identifier: str) -> tuple[np.ndarray, int] | None:
        with self._lock:
            self._prune_expired(self._clock())
            self._schedule_expiry_locked()
            entry = self._entries.get(identifier)
            if entry is None:
                return None
            metadata, audio = entry
            return audio.copy(), metadata.sample_rate

    def clear(self) -> None:
        with self._lock:
            self._cancel_expiry_locked()
            for _metadata, audio in self._entries.values():
                audio.fill(0)
            self._entries.clear()

    def _schedule_expiry_locked(self) -> None:
        self._cancel_expiry_locked()
        if not self._entries:
            return
        expires_at = min(metadata.expires_at for metadata, _audio in self._entries.values())
        delay = max(0.0, expires_at - self._clock())
        token = object()
        timer = self._timer_factory(delay, lambda: self._expire_due(token))
        timer.daemon = True
        self._expiry_timer = token, timer
        timer.start()

    def _cancel_expiry_locked(self) -> None:
        current = self._expiry_timer
        self._expiry_timer = None
        if current is not None:
            _token, timer = current
            timer.cancel()

    def _expire_due(self, token: object) -> None:
        with self._lock:
            current = self._expiry_timer
            if current is None or current[0] is not token:
                return
            self._expiry_timer = None
            self._prune_expired(self._clock())
            self._schedule_expiry_locked()

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
            self._schedule_expiry_locked()
            entry = self._entries.get(identifier)
            if entry is None:
                raise ValueError("Recent audio is missing or expired")
            metadata, buffered_audio = entry
            audio = buffered_audio.copy()
        output = self._repository.save(
            FeedbackSample(
                identifier=identifier,
                audio=audio,
                sample_rate=metadata.sample_rate,
                reference=reference,
                required_terms=required_terms,
            ),
            directory,
        )
        with self._lock:
            current = self._entries.get(identifier)
            if current is not None and current[0] is metadata:
                self._discard(identifier)
                self._schedule_expiry_locked()
        return output
