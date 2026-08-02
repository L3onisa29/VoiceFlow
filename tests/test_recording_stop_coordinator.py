from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest

from voiceflow.config import AudioConfig
from voiceflow.integrations import T3CodeDestination
from voiceflow.system.text_injector import FocusTarget, InjectionDestination


class Lease:
    def __init__(self, *, failure: BaseException | None = None) -> None:
        self.failure = failure
        self.aborts = 0
        self.commits: list[tuple[np.ndarray, dict[str, Any]]] = []

    def abort(self) -> None:
        self.aborts += 1

    def commit_submission(self, audio: np.ndarray, **kwargs: Any) -> int:
        self.commits.append((audio, kwargs))
        if self.failure is not None:
            raise self.failure
        return 17


def recording_types() -> SimpleNamespace:
    from voiceflow.recording import (
        AccessibilityAtStop,
        CapturedSubmission,
        RecordingStopCoordinator,
        StopDisposition,
    )

    return SimpleNamespace(
        accessibility=AccessibilityAtStop,
        captured=CapturedSubmission,
        coordinator=RecordingStopCoordinator,
        disposition=StopDisposition,
    )


def captured(destination: Any, *, duration: float = 1.0, rms: float = 0.5) -> Any:
    types = recording_types()
    return types.captured(
        audio=np.ones(160, dtype=np.float32),
        sample_rate=16_000,
        duration_seconds=duration,
        rms=rms,
        peak=0.8,
        recording_destination=destination,
    )


def coordinator(
    *,
    capture_destination: Any = InjectionDestination.clipboard_only,
    resolve_bundle_id: Any = None,
    store_recent_audio: Any = None,
    submission_allowed: Any = lambda: True,
) -> Any:
    types = recording_types()
    return types.coordinator(
        audio_config=AudioConfig(),
        device_name=lambda: "Test microphone",
        capture_destination=capture_destination,
        resolve_bundle_id=resolve_bundle_id,
        store_recent_audio=store_recent_audio,
        submission_allowed=submission_allowed,
    )


def test_t3_destination_and_sequence_are_preserved_without_accessibility_capture() -> None:
    types = recording_types()
    destination = T3CodeDestination("recording-1")
    lease = Lease()
    result = coordinator(capture_destination=pytest.fail).submit(
        captured(destination),
        lease,
    )

    assert result.disposition == types.disposition.SUBMITTED
    assert result.sequence_id == 17
    assert result.destination == destination
    assert result.app_bundle_id == "com.t3tools.t3code"
    assert not result.clipboard_fallback
    assert lease.commits[0][1]["destination"] == destination


def test_accessibility_destination_is_captured_at_stop_and_bundle_is_resolved() -> None:
    types = recording_types()
    destination = InjectionDestination.focused(FocusTarget(42, "field"))
    capture_calls = 0
    resolved: list[int] = []

    def capture_destination() -> InjectionDestination:
        nonlocal capture_calls
        capture_calls += 1
        return destination

    result = coordinator(
        capture_destination=capture_destination,
        resolve_bundle_id=lambda pid: resolved.append(pid) or "com.example.editor",
    ).submit(captured(types.accessibility.REQUIRED), Lease())

    assert result.disposition == types.disposition.SUBMITTED
    assert result.destination == destination
    assert result.app_bundle_id == "com.example.editor"
    assert capture_calls == 1
    assert resolved == [42]


def test_accessibility_failure_uses_clipboard_without_affecting_t3() -> None:
    types = recording_types()

    def unavailable() -> InjectionDestination:
        raise RuntimeError("focus unavailable")

    result = coordinator(capture_destination=unavailable).submit(
        captured(types.accessibility.REQUIRED),
        Lease(),
    )

    assert result.disposition == types.disposition.SUBMITTED
    assert isinstance(result.destination, InjectionDestination)
    assert result.destination.uses_clipboard_only
    assert result.clipboard_fallback


def test_quality_rejection_aborts_without_resolving_destination() -> None:
    types = recording_types()
    lease = Lease()
    result = coordinator(capture_destination=pytest.fail).submit(
        captured(types.accessibility.REQUIRED, duration=0.01),
        lease,
    )

    assert result.disposition == types.disposition.REJECTED
    assert result.sequence_id is None
    assert result.message == "Registrazione troppo breve"
    assert lease.aborts == 1
    assert lease.commits == []


def test_pipeline_shutdown_race_is_discarded() -> None:
    types = recording_types()
    lease = Lease(failure=RuntimeError("Pipeline is shutting down"))
    result = coordinator().submit(
        captured(InjectionDestination.clipboard_only()),
        lease,
    )

    assert result.disposition == types.disposition.DISCARDED
    assert result.sequence_id is None
    assert len(lease.commits) == 1


def test_application_shutdown_before_commit_aborts_the_lease() -> None:
    types = recording_types()
    lease = Lease()
    result = coordinator(submission_allowed=lambda: False).submit(
        captured(InjectionDestination.clipboard_only()),
        lease,
    )

    assert result.disposition == types.disposition.DISCARDED
    assert lease.aborts == 1
    assert lease.commits == []


def test_submission_failure_is_reported() -> None:
    types = recording_types()
    result = coordinator().submit(
        captured(InjectionDestination.clipboard_only()),
        Lease(failure=ValueError("queue failed")),
    )

    assert result.disposition == types.disposition.FAILED
    assert result.message == "Registrazione non accodata"


def test_feedback_failure_is_isolated_after_submission() -> None:
    types = recording_types()
    stored: list[tuple[str, int]] = []

    def store(identifier: str, _audio: np.ndarray, sample_rate: int) -> None:
        stored.append((identifier, sample_rate))
        raise OSError("feedback unavailable")

    result = coordinator(store_recent_audio=store).submit(
        captured(InjectionDestination.clipboard_only()),
        Lease(),
    )

    assert result.disposition == types.disposition.SUBMITTED
    assert result.sequence_id == 17
    assert stored == [("17", 16_000)]


def test_bundle_resolution_failure_does_not_block_submission() -> None:
    types = recording_types()
    destination = InjectionDestination.focused(FocusTarget(42, "field"))

    def fail_resolution(_pid: int) -> str:
        raise OSError("resolver unavailable")

    result = coordinator(resolve_bundle_id=fail_resolution).submit(
        captured(destination),
        Lease(),
    )

    assert result.disposition == types.disposition.SUBMITTED
    assert result.destination == destination
    assert result.app_bundle_id is None
