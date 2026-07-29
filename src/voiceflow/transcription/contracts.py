from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class TranscriptToken:
    text: str
    start_seconds: float | None = None
    log_probability: float | None = None


@dataclass(frozen=True, slots=True)
class TranscriptSegment:
    text: str
    start_seconds: float
    end_seconds: float
    tokens: tuple[TranscriptToken, ...] = ()


@dataclass(frozen=True, slots=True)
class TranscriptionResult:
    text: str
    segments: tuple[TranscriptSegment, ...]
    audio_duration_seconds: float
    inference_seconds: float
    language: str | None = None
    language_source: Literal["configured", "automatic_unknown"] = "automatic_unknown"
    confidence_proxy: float | None = None

    @classmethod
    def empty(
        cls,
        *,
        audio_duration_seconds: float = 0,
        inference_seconds: float = 0,
        language: str | None = None,
    ) -> TranscriptionResult:
        return cls(
            text="",
            segments=(),
            audio_duration_seconds=audio_duration_seconds,
            inference_seconds=inference_seconds,
            language=language,
            language_source="configured" if language else "automatic_unknown",
        )
