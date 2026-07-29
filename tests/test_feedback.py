from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from voiceflow.feedback import RecentAudioBuffer


def test_audio_stays_in_ram_until_confirmation_and_expires(tmp_path: Path) -> None:
    now = [0.0]
    buffer = RecentAudioBuffer(300, clock=lambda: now[0])
    source = np.array([0.2, -0.2], dtype=np.float32)
    buffer.store("one", source, 16000)
    source.fill(1)
    assert np.allclose(buffer.get("one")[0], [0.2, -0.2])  # type: ignore[index]
    assert (
        buffer.save_confirmed(
            "one",
            tmp_path / "corpus",
            reference="test",
            confirmed=False,
        )
        is None
    )
    assert not (tmp_path / "corpus").exists()
    now[0] = 301
    assert buffer.get("one") is None
    with pytest.raises(ValueError):
        buffer.save_confirmed(
            "one",
            tmp_path / "corpus",
            reference="test",
            confirmed=True,
        )


def test_confirmed_audio_and_manifest_are_written_atomically(tmp_path: Path) -> None:
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0, 0.5], dtype=np.float32), 16000)
    output = buffer.save_confirmed(
        "sample",
        tmp_path,
        reference="VoiceBud",
        required_terms=("VoiceBud",),
        confirmed=True,
    )
    assert output is not None and output.is_file()
    records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records == [
        {
            "id": "sample",
            "audio": "sample.wav",
            "reference": "VoiceBud",
            "required_terms": ["VoiceBud"],
        }
    ]
    assert buffer.get("sample") is None
