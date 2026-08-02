from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np

from voiceflow.integrations import DictationDestination


class AccessibilityAtStop(StrEnum):
    REQUIRED = "accessibility_at_stop"


class RecordingPhase(StrEnum):
    IDLE = "idle"
    STARTING = "starting"
    RECORDING = "recording"
    STOPPING = "stopping"


@dataclass(frozen=True, slots=True)
class CapturedSubmission:
    audio: np.ndarray
    sample_rate: int
    duration_seconds: float
    rms: float
    peak: float
    recording_destination: DictationDestination | AccessibilityAtStop


class StopDisposition(StrEnum):
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    DISCARDED = "discarded"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StopResult:
    disposition: StopDisposition
    sequence_id: int | None
    destination: DictationDestination | None
    app_bundle_id: str | None
    message: str | None
    clipboard_fallback: bool = False
