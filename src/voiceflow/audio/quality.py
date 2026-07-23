from __future__ import annotations

from voiceflow.config import AudioConfig


def capture_rejection_reason(
    *,
    duration_seconds: float,
    rms: float,
    device_name: str,
    config: AudioConfig,
) -> str | None:
    if duration_seconds < config.minimum_recording_ms / 1000:
        return "Registrazione troppo breve"
    if rms < config.silence_rms_threshold:
        return f"Nessun segnale da {device_name}"
    return None
