from __future__ import annotations

import copy
import json
import logging
from collections.abc import Iterable, Mapping
from enum import Enum
from logging.handlers import QueueHandler, QueueListener
from pathlib import Path
from queue import Empty, Full, Queue

MAX_DIAGNOSTIC_VALUE_CHARACTERS = 240
DEFAULT_LOG_QUEUE_CAPACITY = 1024


class _DeferredQueueHandler(QueueHandler):
    """Enqueue records without formatting them on the producing thread."""

    def __init__(self, event_queue: Queue[logging.LogRecord], /) -> None:
        super().__init__(event_queue)
        self.dropped_records = 0

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return copy.copy(record)

    def enqueue(self, record: logging.LogRecord) -> None:
        try:
            self.queue.put_nowait(record)
            return
        except Full:
            self.dropped_records += 1
        if record.levelno < logging.WARNING:
            return
        try:
            self.queue.get_nowait()
        except Empty:
            return
        try:
            self.queue.put_nowait(record)
        except Full:
            self.dropped_records += 1


class _NonBlockingQueueListener(QueueListener):
    def enqueue_sentinel(self) -> None:
        while True:
            try:
                self.queue.put_nowait(self._sentinel)
                return
            except Full:
                try:
                    self.queue.get_nowait()
                except Empty:
                    continue


class BackgroundLogWriter:
    """Move terminal and file writes off VoiceBud's latency-sensitive threads."""

    def __init__(
        self,
        handlers: Iterable[logging.Handler],
        *,
        queue_capacity: int = DEFAULT_LOG_QUEUE_CAPACITY,
    ) -> None:
        if queue_capacity < 1:
            raise ValueError("queue_capacity must be positive")
        self._handlers = tuple(handlers)
        self._queue: Queue[logging.LogRecord] = Queue(maxsize=queue_capacity)
        self.handler = _DeferredQueueHandler(self._queue)
        self._listener = _NonBlockingQueueListener(self._queue, *self._handlers)
        self._started = False

    def start(self) -> None:
        if self._started:
            return
        self._listener.start()
        self._started = True

    def close(self) -> None:
        if not self._started:
            return
        self._listener.stop()
        self._started = False
        for handler in self._handlers:
            handler.flush()
            handler.close()


def log_event(
    logger: logging.Logger,
    level: int,
    event: str,
    message: str,
    *,
    context: Mapping[str, object] | None = None,
    exc_info: bool = False,
    **fields: object,
) -> None:
    """Write a human-readable event with stable, grep-friendly diagnostic fields."""
    if not logger.isEnabledFor(level):
        return
    values: dict[str, object] = {}
    if context is not None:
        values.update(context)
    values.update(fields)
    suffix = " ".join(
        f"{key}={_encode_value(value)}" for key, value in values.items() if value is not None
    )
    rendered = f"{message} | event={event}"
    if suffix:
        rendered = f"{rendered} {suffix}"
    logger.log(level, rendered, exc_info=exc_info)


def exception_fields(exc: BaseException) -> dict[str, object]:
    """Return useful exception metadata without serializing arbitrary object state."""
    fields: dict[str, object] = {
        "error_type": type(exc).__name__,
    }
    reason = _safe_reason(exc)
    if reason:
        fields["error_reason"] = reason
    cause = exc.__cause__
    if cause is not None:
        fields["cause_type"] = type(cause).__name__
        errno = getattr(cause, "errno", None)
        if isinstance(errno, int):
            fields["cause_errno"] = errno
    return fields


def _safe_reason(exc: BaseException) -> str:
    return _bounded_text(str(exc))


def _encode_value(value: object) -> str:
    if isinstance(value, Enum):
        value = value.value
    elif isinstance(value, Path):
        value = str(value)
    elif isinstance(value, float):
        value = round(value, 4)
    elif not isinstance(value, (str, int, bool)):
        value = str(value)
    if isinstance(value, str):
        value = _bounded_text(value)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _bounded_text(value: str) -> str:
    value = value.replace("\r", " ").replace("\n", " ").strip()
    if len(value) > MAX_DIAGNOSTIC_VALUE_CHARACTERS:
        return value[: MAX_DIAGNOSTIC_VALUE_CHARACTERS - 1] + "…"
    return value
