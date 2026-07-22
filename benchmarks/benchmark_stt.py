from __future__ import annotations

import argparse
import json
import resource
import statistics
import time
import wave
from pathlib import Path

import numpy as np

from voiceflow.config import load_config
from voiceflow.stt.parakeet import ParakeetTranscriber


def read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as wav:
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError("Benchmark WAV must be mono 16-bit PCM")
        sample_rate = wav.getframerate()
        frames = wav.readframes(wav.getnframes())
    return np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0, sample_rate


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, round((len(ordered) - 1) * fraction))
    return ordered[index]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Parakeet ONNX on CPU")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--config")
    parser.add_argument("--iterations", type=int, default=5)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--enforce-thresholds", action="store_true")
    args = parser.parse_args()
    if args.iterations < 2:
        parser.error("--iterations must be at least 2")
    audio, sample_rate = read_wav(args.audio)
    config = load_config(args.config)
    transcriber = ParakeetTranscriber(config.stt)
    started = time.perf_counter()
    resolution = transcriber.load()
    load_seconds = time.perf_counter() - started
    timings: list[float] = []
    for _ in range(args.iterations):
        started = time.perf_counter()
        transcriber.transcribe(audio, sample_rate=sample_rate)
        timings.append(time.perf_counter() - started)
    warm = timings[1:]
    report = {
        "model": config.stt.model,
        "model_path": str(resolution.path),
        "provider": config.stt.provider,
        "audio_seconds": len(audio) / sample_rate,
        "load_seconds": load_seconds,
        "cold_seconds": timings[0],
        "warm_median_seconds": statistics.median(warm),
        "warm_p95_seconds": percentile(warm, 0.95),
        "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    output = json.dumps(report, indent=2)
    print(output)
    if args.json_output:
        args.json_output.write_text(output + "\n", encoding="utf-8")
    if args.enforce_thresholds:
        failed = (
            report["load_seconds"] > 3.5
            or report["cold_seconds"] > 1.0
            or report["warm_median_seconds"] > 0.6
            or report["peak_rss_bytes"] > 1.5 * 1024**3
        )
        return int(failed)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

