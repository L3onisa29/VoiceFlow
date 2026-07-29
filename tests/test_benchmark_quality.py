from __future__ import annotations

import json
import wave
from pathlib import Path
from types import SimpleNamespace

from benchmarks import benchmark_quality
from benchmarks.benchmark_quality import compare_reports, error_rate


def test_quality_error_rates() -> None:
    assert error_rate("one two", "one two") == 0
    assert error_rate("one two", "one") == 0.5
    assert error_rate("abc", "adc", characters=True) == 1 / 3


def test_baseline_requires_compatible_corpus_and_preserves_terms() -> None:
    baseline = {
        "corpus_hash": "same",
        "configuration_fingerprint": "config",
        "wer": 0.1,
        "cer": 0.05,
        "required_term_hits": {"one": ["VoiceBud"]},
        "latency_p95_seconds": 1,
    }
    current = {
        **baseline,
        "wer": 0.114,
        "required_term_hits": {"one": []},
        "latency_p95_seconds": 1.19,
    }
    compatible, regressions = compare_reports(current, baseline)
    assert compatible
    assert regressions == ("required_term:one",)
    assert compare_reports({**current, "corpus_hash": "new"}, baseline) == (
        False,
        ("baseline_incompatible",),
    )


def test_quality_cli_with_fake_transcriber(tmp_path: Path, monkeypatch, capsys) -> None:
    audio = tmp_path / "sample.wav"
    with wave.open(str(audio), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b"\0\0" * 160)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(
            {
                "id": "sample",
                "audio": audio.name,
                "reference": "hello world",
                "required_terms": ["world"],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stt = SimpleNamespace(
        model="fake",
        quantization="int8",
        provider="CPUExecutionProvider",
        language="en",
        vad=SimpleNamespace(enabled=True, model="silero"),
    )
    monkeypatch.setattr(
        benchmark_quality,
        "load_config",
        lambda *_args, **_kwargs: SimpleNamespace(stt=stt),
    )

    class Transcriber:
        def __init__(self, _config) -> None:
            pass

        def load(self):
            return SimpleNamespace(path=tmp_path / "model")

        def transcribe(self, *_args, **_kwargs):
            return SimpleNamespace(text="hello world")

    monkeypatch.setattr(benchmark_quality, "ParakeetTranscriber", Transcriber)
    assert benchmark_quality.main([str(manifest), "--enforce-thresholds"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["wer"] == 0
    assert report["required_term_recall"] == 1
