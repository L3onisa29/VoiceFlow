from __future__ import annotations

import argparse
import hashlib
import json
import re
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from voiceflow.benchmarking import percentile, read_wav
from voiceflow.config import load_config
from voiceflow.stt.parakeet import ParakeetTranscriber


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    identifier: str
    audio_path: Path
    reference: str
    required_terms: tuple[str, ...] = ()


def _edit_distance(left: Sequence[str], right: Sequence[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, *, characters: bool = False) -> float:
    if characters:
        expected = list(reference.casefold())
        actual = list(hypothesis.casefold())
    else:
        expected = reference.casefold().split()
        actual = hypothesis.casefold().split()
    return _edit_distance(expected, actual) / max(1, len(expected))


def load_manifest(path: Path) -> tuple[ManifestEntry, ...]:
    entries: list[ManifestEntry] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        payload = json.loads(line)
        if not isinstance(payload, Mapping):
            raise ValueError(f"Manifest line {line_number} must be an object")
        identifier = payload.get("id")
        audio = payload.get("audio")
        reference = payload.get("reference")
        required_terms = payload.get("required_terms", [])
        if (
            not isinstance(identifier, str)
            or not identifier
            or not isinstance(audio, str)
            or not isinstance(reference, str)
            or not isinstance(required_terms, list)
            or not all(isinstance(term, str) and term for term in required_terms)
        ):
            raise ValueError(f"Manifest line {line_number} has invalid fields")
        entries.append(
            ManifestEntry(
                identifier,
                (path.parent / audio).resolve(),
                reference,
                tuple(required_terms),
            )
        )
    if not entries:
        raise ValueError("Quality benchmark manifest is empty")
    return tuple(entries)


def corpus_hash(entries: Iterable[ManifestEntry]) -> str:
    digest = hashlib.sha256()
    for entry in sorted(entries, key=lambda item: item.identifier):
        digest.update(
            json.dumps(
                {
                    "id": entry.identifier,
                    "reference": entry.reference,
                    "required_terms": entry.required_terms,
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        )
        digest.update(entry.audio_path.read_bytes())
    return digest.hexdigest()


def evaluate(
    entries: Sequence[ManifestEntry],
    transcribe: Callable[[np.ndarray, int], str],
    *,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    word_errors: list[float] = []
    character_errors: list[float] = []
    timings: list[float] = []
    empty_count = 0
    required_total = 0
    required_hits = 0
    hits_by_entry: dict[str, list[str]] = {}
    for entry in entries:
        audio, sample_rate = read_wav(entry.audio_path)
        started = time.perf_counter()
        hypothesis = transcribe(audio, sample_rate).strip()
        timings.append(time.perf_counter() - started)
        word_errors.append(error_rate(entry.reference, hypothesis))
        character_errors.append(error_rate(entry.reference, hypothesis, characters=True))
        empty_count += not hypothesis
        hits = [
            term
            for term in entry.required_terms
            if re.search(
                rf"(?<!\w){re.escape(term)}(?!\w)",
                hypothesis,
                re.IGNORECASE | re.UNICODE,
            )
        ]
        hits_by_entry[entry.identifier] = hits
        required_total += len(entry.required_terms)
        required_hits += len(hits)
    return {
        "corpus_hash": corpus_hash(entries),
        "metadata": dict(metadata),
        "configuration_fingerprint": hashlib.sha256(
            json.dumps(metadata, sort_keys=True, default=str).encode()
        ).hexdigest(),
        "utterances": len(entries),
        "wer": statistics.mean(word_errors),
        "cer": statistics.mean(character_errors),
        "required_term_recall": required_hits / max(1, required_total),
        "required_term_hits": hits_by_entry,
        "empty_rate": empty_count / len(entries),
        "latency_p50_seconds": percentile(timings, 0.50),
        "latency_p95_seconds": percentile(timings, 0.95),
    }


def compare_reports(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    error_rate_points: float = 0.015,
    p95_multiplier: float = 1.20,
) -> tuple[bool, tuple[str, ...]]:
    if current.get("corpus_hash") != baseline.get("corpus_hash") or current.get(
        "configuration_fingerprint"
    ) != baseline.get("configuration_fingerprint"):
        return False, ("baseline_incompatible",)
    failures: list[str] = []
    for metric in ("wer", "cer"):
        if float(current[metric]) > float(baseline[metric]) + error_rate_points:
            failures.append(metric)
    baseline_hits = baseline.get("required_term_hits", {})
    current_hits = current.get("required_term_hits", {})
    if isinstance(baseline_hits, Mapping) and isinstance(current_hits, Mapping):
        for identifier, terms in baseline_hits.items():
            if not set(terms).issubset(set(current_hits.get(identifier, []))):
                failures.append(f"required_term:{identifier}")
    if float(current["latency_p95_seconds"]) > (
        float(baseline["latency_p95_seconds"]) * p95_multiplier
    ):
        failures.append("latency_p95_seconds")
    return True, tuple(failures)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate VoiceBud transcription quality")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--config")
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--json-output", type=Path)
    parser.add_argument("--enforce-thresholds", action="store_true")
    args = parser.parse_args(argv)
    entries = load_manifest(args.manifest)
    config = load_config(args.config)
    transcriber = ParakeetTranscriber(config.stt)
    resolution = transcriber.load()
    report = evaluate(
        entries,
        lambda audio, rate: transcriber.transcribe(audio, sample_rate=rate).text,
        metadata={
            "model": config.stt.model,
            "quantization": config.stt.quantization,
            "provider": config.stt.provider,
            "language": config.stt.language,
            "vad": config.stt.vad.model if config.stt.vad.enabled else None,
            "model_path": str(resolution.path),
        },
    )
    compatible = None
    regressions: tuple[str, ...] = ()
    if args.baseline:
        baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
        compatible, regressions = compare_reports(report, baseline)
        report["baseline_compatible"] = compatible
        report["regressions"] = regressions
    output = json.dumps(report, indent=2, ensure_ascii=False)
    print(output)
    if args.json_output:
        args.json_output.write_text(output + "\n", encoding="utf-8")
    return int(args.enforce_thresholds and compatible is True and bool(regressions))


if __name__ == "__main__":
    raise SystemExit(main())
