from __future__ import annotations

import threading
from enum import StrEnum
from typing import Protocol

import numpy as np

from voiceflow.integrations import DictationDestination, T3CodeDestination


class _ReservationPipeline(Protocol):
    def try_reserve(self) -> bool: ...

    def cancel_reservation(self) -> None: ...

    def submit_reserved(
        self,
        audio: np.ndarray,
        *,
        destination: DictationDestination,
        sample_rate: int,
        app_bundle_id: str | None,
    ) -> int: ...


class _T3Bridge(Protocol):
    def abandon_recording(self, recording_id: str) -> None: ...


class _LeaseState(StrEnum):
    OPEN = "open"
    COMMITTED = "committed"
    ABORTED = "aborted"


class RecordingLease:
    def __init__(self, pipeline: _ReservationPipeline, t3_bridge: _T3Bridge) -> None:
        self._pipeline = pipeline
        self._t3_bridge = t3_bridge
        self._lock = threading.Lock()
        self._state = _LeaseState.OPEN
        self._owns_pipeline_reservation = True
        self._t3_recording_id: str | None = None

    @classmethod
    def try_reserve(
        cls,
        pipeline: _ReservationPipeline,
        t3_bridge: _T3Bridge,
    ) -> RecordingLease | None:
        if not pipeline.try_reserve():
            return None
        return cls(pipeline, t3_bridge)

    def attach_t3(self, recording_id: str) -> None:
        with self._lock:
            self._require_open()
            if self._t3_recording_id is not None:
                raise RuntimeError("Recording lease already has a T3 reservation")
            self._t3_recording_id = recording_id

    def commit_submission(
        self,
        audio: np.ndarray,
        *,
        destination: DictationDestination,
        sample_rate: int,
        app_bundle_id: str | None,
    ) -> int:
        t3_to_abandon: str | None = None
        try:
            with self._lock:
                self._require_open()
                self._validate_destination(destination)
                self._owns_pipeline_reservation = False
                try:
                    sequence_id = self._pipeline.submit_reserved(
                        audio,
                        destination=destination,
                        sample_rate=sample_rate,
                        app_bundle_id=app_bundle_id,
                    )
                except BaseException:
                    self._state = _LeaseState.ABORTED
                    t3_to_abandon = self._take_t3()
                    raise
                self._state = _LeaseState.COMMITTED
                self._t3_recording_id = None
                return sequence_id
        except BaseException as exc:
            if t3_to_abandon is not None:
                try:
                    self._t3_bridge.abandon_recording(t3_to_abandon)
                except BaseException as cleanup_error:
                    exc.add_note(f"T3 cleanup also failed: {cleanup_error!r}")
            raise

    def abort(self) -> None:
        with self._lock:
            if self._state != _LeaseState.OPEN:
                return
            self._state = _LeaseState.ABORTED
            cancel_pipeline = self._owns_pipeline_reservation
            self._owns_pipeline_reservation = False
            t3_to_abandon = self._take_t3()

        cleanup_error: BaseException | None = None
        if cancel_pipeline:
            try:
                self._pipeline.cancel_reservation()
            except BaseException as exc:
                cleanup_error = exc
        if t3_to_abandon is not None:
            try:
                self._t3_bridge.abandon_recording(t3_to_abandon)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
                else:
                    cleanup_error.add_note(f"T3 cleanup also failed: {exc!r}")
        if cleanup_error is not None:
            raise cleanup_error

    def close(self) -> None:
        self.abort()

    def _require_open(self) -> None:
        if self._state != _LeaseState.OPEN:
            raise RuntimeError("Recording lease is no longer open")

    def _validate_destination(self, destination: DictationDestination) -> None:
        if isinstance(destination, T3CodeDestination):
            if self._t3_recording_id is None:
                raise ValueError("T3 destination has no attached reservation")
            if destination.recording_id != self._t3_recording_id:
                raise ValueError("T3 destination does not match the attached reservation")
        elif self._t3_recording_id is not None:
            raise ValueError("Submission destination does not match the attached T3 reservation")

    def _take_t3(self) -> str | None:
        recording_id = self._t3_recording_id
        self._t3_recording_id = None
        return recording_id
