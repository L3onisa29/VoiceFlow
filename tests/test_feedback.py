from __future__ import annotations

import json
import os
import threading
from concurrent.futures import ThreadPoolExecutor
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


@pytest.mark.parametrize("consumed_prefix", [".voicebud-audio-", ".manifest-"])
def test_consumed_temporary_cleanup_failure_cannot_invalidate_committed_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    consumed_prefix: str,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    consumed: set[Path] = set()

    def track_replacement(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if source.name.startswith(consumed_prefix):
            consumed.add(source)

    def fail_consumed_cleanup(path: Path, missing_ok: bool = False) -> None:
        if path in consumed:
            raise OSError("post-commit temporary cleanup failed")
        original_unlink(path, missing_ok=missing_ok)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", track_replacement)
    monkeypatch.setattr(Path, "unlink", fail_consumed_cleanup)

    output = buffer.save_confirmed(
        "sample",
        tmp_path,
        reference="sample",
        confirmed=True,
    )

    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    manifest = (tmp_path / "manifest.jsonl").read_text(encoding="utf-8")
    assert [json.loads(line) for line in manifest.splitlines()] == [
        {
            "id": "sample",
            "audio": "sample.wav",
            "reference": "sample",
            "required_terms": [],
        }
    ]
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert buffer.get("sample") is None


def test_wav_permission_failure_cleans_temp_and_retains_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"existing"}\n', encoding="utf-8")
    os.chmod(manifest, 0o644)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    failed_descriptor: int | None = None

    def fail_permission(descriptor: int) -> None:
        nonlocal failed_descriptor
        failed_descriptor = descriptor
        raise OSError("permission hardening failed")

    monkeypatch.setattr(feedback_corpus, "restrict_open_file", fail_permission)

    with pytest.raises(OSError, match="permission hardening failed"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not (tmp_path / "sample.wav").exists()
    assert manifest.read_text(encoding="utf-8") == '{"id":"existing"}\n'
    assert os.stat(manifest).st_mode & 0o777 == 0o644
    assert buffer.get("sample") is not None
    assert failed_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(failed_descriptor)


def test_manifest_permission_failure_rolls_back_wav_and_retains_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"existing"}\n', encoding="utf-8")
    os.chmod(manifest, 0o644)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_restrict = feedback_corpus.restrict_open_file
    calls = 0
    failed_descriptor: int | None = None

    def fail_second_permission(descriptor: int) -> None:
        nonlocal calls, failed_descriptor
        calls += 1
        if calls == 2:
            failed_descriptor = descriptor
            raise OSError("manifest hardening failed")
        original_restrict(descriptor)

    monkeypatch.setattr(feedback_corpus, "restrict_open_file", fail_second_permission)

    with pytest.raises(OSError, match="manifest hardening failed"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert calls == 2
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not (tmp_path / "sample.wav").exists()
    assert manifest.read_text(encoding="utf-8") == '{"id":"existing"}\n'
    assert os.stat(manifest).st_mode & 0o777 == 0o644
    assert buffer.get("sample") is not None
    assert failed_descriptor is not None
    with pytest.raises(OSError):
        os.fstat(failed_descriptor)


def test_successful_manifest_replacement_hardens_broad_existing_file(
    tmp_path: Path,
) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"existing"}\n', encoding="utf-8")
    os.chmod(manifest, 0o644)
    previous_inode = os.stat(manifest).st_ino
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert os.stat(manifest).st_mode & 0o777 == 0o600
    assert os.stat(manifest).st_ino != previous_inode
    assert [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()] == [
        {"id": "existing"},
        {
            "id": "sample",
            "audio": "sample.wav",
            "reference": "sample",
            "required_terms": [],
        },
    ]


@pytest.mark.parametrize("dangling", [False, True])
def test_feedback_rejects_manifest_symlink_and_rolls_back_wav(
    tmp_path: Path,
    dangling: bool,
) -> None:
    external = tmp_path / "external-manifest"
    if not dangling:
        external.write_text('{"id":"external"}\n', encoding="utf-8")
        os.chmod(external, 0o644)
    manifest = tmp_path / "manifest.jsonl"
    manifest.symlink_to(external)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    with pytest.raises(OSError):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert manifest.is_symlink()
    assert not (tmp_path / "sample.wav").exists()
    assert buffer.get("sample") is not None
    if dangling:
        assert not external.exists()
    else:
        assert external.read_text(encoding="utf-8") == '{"id":"external"}\n'
        assert os.stat(external).st_mode & 0o777 == 0o644


@pytest.mark.parametrize("dangling", [False, True])
def test_feedback_rejects_output_symlink_without_touching_referent(
    tmp_path: Path,
    dangling: bool,
) -> None:
    external = tmp_path / "external-audio"
    if not dangling:
        external.write_bytes(b"external")
        os.chmod(external, 0o644)
    output = tmp_path / "sample.wav"
    output.symlink_to(external)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    with pytest.raises(OSError):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert output.is_symlink()
    assert buffer.get("sample") is not None
    if dangling:
        assert not external.exists()
    else:
        assert external.read_bytes() == b"external"
        assert os.stat(external).st_mode & 0o777 == 0o644


def test_feedback_preserves_preexisting_custom_directory_mode(tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    buffer.save_confirmed("sample", directory, reference="sample", confirmed=True)

    assert os.stat(directory).st_mode & 0o777 == 0o755


def test_multiple_jobs_remain_available_after_later_submissions() -> None:
    buffer = RecentAudioBuffer(capacity=3)
    for sequence_id in range(1, 4):
        buffer.store(
            str(sequence_id),
            np.array([sequence_id], dtype=np.float32),
            16000,
        )

    assert buffer.get("1") is not None
    assert buffer.get("2") is not None
    assert buffer.get("3") is not None


def test_pipeline_capacity_keeps_last_completed_with_active_and_queued_jobs() -> None:
    now = [0.0]
    buffer = RecentAudioBuffer.for_pipeline(
        10,
        max_pending=2,
        clock=lambda: now[0],
    )
    assert buffer.capacity == 3

    buffer.store("1", np.array([1], dtype=np.float32), 16000)  # last completed
    buffer.store("2", np.array([2], dtype=np.float32), 16000)  # active
    buffer.store("3", np.array([3], dtype=np.float32), 16000)  # queued

    now[0] = 9.999
    assert buffer.get("1") is not None
    assert buffer.get("2") is not None
    assert buffer.get("3") is not None

    now[0] = 10
    assert buffer.get("1") is None


def test_capacity_evicts_oldest_entry_and_expiration_prunes_all_entries() -> None:
    now = [0.0]
    buffer = RecentAudioBuffer(10, capacity=2, clock=lambda: now[0])
    buffer.store("one", np.array([1], dtype=np.float32), 16000)
    buffer.store("two", np.array([2], dtype=np.float32), 16000)
    assert buffer.get("one") is not None

    buffer.store("three", np.array([3], dtype=np.float32), 16000)
    assert buffer.get("one") is None
    assert buffer.get("two") is not None
    assert buffer.get("three") is not None

    now[0] = 10
    assert buffer.get("two") is None
    assert buffer.get("three") is None


def test_concurrent_stores_and_reads_are_isolated_by_sequence_id() -> None:
    worker_count = 8
    buffer = RecentAudioBuffer(capacity=worker_count)
    stored = threading.Barrier(worker_count)

    def round_trip(sequence_id: int) -> None:
        buffer.store(
            str(sequence_id),
            np.array([sequence_id], dtype=np.float32),
            16000 + sequence_id,
        )
        stored.wait()
        item = buffer.get(str(sequence_id))
        assert item is not None
        audio, sample_rate = item
        assert audio.tolist() == [sequence_id]
        assert sample_rate == 16000 + sequence_id

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(round_trip, sequence_id) for sequence_id in range(worker_count)]
        for future in futures:
            future.result()


def test_concurrent_confirmations_serialize_manifest_read_modify_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    buffer = RecentAudioBuffer(capacity=2)
    buffer.store("first", np.array([0.1], dtype=np.float32), 16000)
    buffer.store("second", np.array([0.2], dtype=np.float32), 16000)
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("", encoding="utf-8")

    first_read = threading.Event()
    second_read = threading.Event()
    second_replaced = threading.Event()
    save_context = threading.local()
    import voiceflow.storage.feedback_corpus as feedback_corpus

    original_read_text = feedback_corpus.read_managed_text_file
    original_replace = os.replace

    def interleaved_read_text(path: Path) -> str:
        contents = original_read_text(path)
        if path != manifest:
            return contents
        save_name = getattr(save_context, "name", None)
        if save_name == "first":
            first_read.set()
            if second_read.wait(timeout=0.2):
                assert second_replaced.wait(timeout=1)
        elif save_name == "second":
            second_read.set()
        return contents

    def tracked_replace(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if (
            Path(destination).name == manifest.name
            and getattr(save_context, "name", None) == "second"
        ):
            second_replaced.set()

    monkeypatch.setattr(feedback_corpus, "read_managed_text_file", interleaved_read_text)
    monkeypatch.setattr(os, "replace", tracked_replace)

    def save(name: str) -> Path | None:
        save_context.name = name
        return buffer.save_confirmed(
            name,
            tmp_path,
            reference=f"reference-{name}",
            confirmed=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        first = executor.submit(save, "first")
        assert first_read.wait(timeout=1)
        second = executor.submit(save, "second")
        outputs = [first.result(), second.result()]

    assert all(output is not None and output.is_file() for output in outputs)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert {record["id"] for record in records} == {"first", "second"}
    assert {record["audio"] for record in records} == {"first.wav", "second.wav"}
