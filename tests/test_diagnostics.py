from __future__ import annotations

import logging
import threading
import time

from voiceflow.diagnostics import BackgroundLogWriter, log_event


class _BlockingHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.allow_write = threading.Event()
        self.writer_thread: str | None = None

    def emit(self, _record: logging.LogRecord) -> None:
        self.writer_thread = threading.current_thread().name
        self.entered.set()
        self.allow_write.wait(timeout=1)


def test_background_writer_never_waits_for_slow_output() -> None:
    output = _BlockingHandler()
    writer = BackgroundLogWriter([output])
    logger = logging.getLogger("voiceflow.tests.nonblocking")
    logger.handlers = [writer.handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    writer.start()
    try:
        started = time.perf_counter()
        logger.info("diagnostic event")
        elapsed = time.perf_counter() - started

        assert elapsed < 0.1
        assert output.entered.wait(timeout=1)
        assert output.writer_thread != threading.current_thread().name
    finally:
        output.allow_write.set()
        writer.close()
        logger.handlers = []


def test_disabled_event_does_not_format_diagnostic_fields() -> None:
    class MustNotRender:
        def __str__(self) -> str:
            raise AssertionError("disabled diagnostic field was formatted")

    logger = logging.getLogger("voiceflow.tests.disabled")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)

    log_event(
        logger,
        logging.DEBUG,
        "dictation.debug",
        "Debug detail",
        expensive=MustNotRender(),
    )


def test_full_diagnostic_queue_drops_records_without_blocking() -> None:
    writer = BackgroundLogWriter([logging.NullHandler()], queue_capacity=1)
    logger = logging.getLogger("voiceflow.tests.full_queue")
    logger.handlers = [writer.handler]
    logger.propagate = False
    logger.setLevel(logging.DEBUG)
    try:
        logger.debug("first")
        started = time.perf_counter()
        logger.debug("dropped")

        assert time.perf_counter() - started < 0.1
        assert writer.handler.dropped_records == 1
    finally:
        writer.start()
        writer.close()
        logger.handlers = []
