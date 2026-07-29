from __future__ import annotations

import math
from types import SimpleNamespace

from voiceflow.stt.parakeet import _structured_result


def test_direct_timestamped_result_handles_incomplete_metadata() -> None:
    result = _structured_result(
        SimpleNamespace(
            text="hello world",
            tokens=["hello", "world"],
            timestamps=[(0.1, 0.2)],
            logprobs=[-0.2, float("nan")],
        ),
        audio_duration_seconds=1,
        inference_seconds=0.2,
        language="en",
        uses_vad=False,
    )
    assert result.text == "hello world"
    assert result.language_source == "configured"
    assert result.segments[0].tokens[1].start_seconds is None
    assert result.confidence_proxy == math.exp(-0.2)


def test_vad_token_timestamps_are_made_absolute() -> None:
    result = _structured_result(
        [
            SimpleNamespace(
                start=4,
                end=6,
                text="one",
                tokens=["one"],
                timestamps=[0.25],
                logprobs=[-1],
            ),
            SimpleNamespace(start=7, end=8, text="two"),
        ],
        audio_duration_seconds=10,
        inference_seconds=0.2,
        language=None,
        uses_vad=True,
    )
    assert result.text == "one two"
    assert result.segments[0].tokens[0].start_seconds == 4.25
    assert result.segments[1].tokens == ()
    assert result.language_source == "automatic_unknown"
