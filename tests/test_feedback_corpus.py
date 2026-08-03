from __future__ import annotations

import errno
import io
import json
import os
import threading
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest

from voiceflow.feedback import RecentAudioBuffer
from voiceflow.storage.feedback_corpus import (
    FeedbackCorpusRepository,
    FeedbackSample,
    FileFeedbackCorpusRepository,
)


class RecordingRepository:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[FeedbackSample, Path]] = []

    def save(self, sample: FeedbackSample, directory: Path) -> Path:
        self.calls.append((sample, directory))
        if self.error is not None:
            raise self.error
        return directory / "recorded.wav"


class FalseyRecordingRepository(RecordingRepository):
    def __bool__(self) -> bool:
        return False


class StatefulRequiredTerms(tuple[object, ...]):
    def __new__(
        cls,
        *iterations: tuple[object, ...],
    ) -> StatefulRequiredTerms:
        return super().__new__(cls, iterations[0])

    def __init__(self, *iterations: tuple[object, ...]) -> None:
        self.iterations = iterations
        self.iteration_count = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        iteration = self.iterations[self.iteration_count]
        self.iteration_count += 1
        return iter(iteration)


def _mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def _wav_bytes(audio: np.ndarray, sample_rate: int) -> bytes:
    pcm = (np.clip(audio, -1, 1) * np.iinfo(np.int16).max).astype(np.int16).tobytes()
    contents = io.BytesIO()
    with wave.open(contents, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)
    return contents.getvalue()


def test_buffer_delegates_an_owned_float32_sample_then_zeroizes_after_success(
    tmp_path: Path,
) -> None:
    repository = RecordingRepository()
    assert isinstance(repository, FeedbackCorpusRepository)
    buffer = RecentAudioBuffer(repository=repository)
    source = np.array([[0.25, -0.5]], dtype=np.float64)
    buffer.store("sample", source, 22050)
    retained = buffer._entries["sample"][1]

    output = buffer.save_confirmed(
        "sample",
        tmp_path,
        reference="reference",
        required_terms=("first", "second"),
        confirmed=True,
    )

    assert output == tmp_path / "recorded.wav"
    assert len(repository.calls) == 1
    sample, directory = repository.calls[0]
    assert directory == tmp_path
    assert sample.identifier == "sample"
    assert sample.sample_rate == 22050
    assert sample.reference == "reference"
    assert sample.required_terms == ("first", "second")
    assert sample.audio.dtype == np.float32
    assert sample.audio.shape == (2,)
    assert sample.audio.tolist() == [0.25, -0.5]
    assert not np.shares_memory(sample.audio, source)
    assert not np.shares_memory(sample.audio, retained)
    assert np.all(retained == 0)
    assert buffer.get("sample") is None


def test_buffer_delegates_to_an_explicit_falsey_repository(tmp_path: Path) -> None:
    repository = FalseyRecordingRepository()
    buffer = RecentAudioBuffer(repository=repository)
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    output = buffer.save_confirmed(
        "sample",
        tmp_path,
        reference="reference",
        confirmed=True,
    )

    assert output == tmp_path / "recorded.wav"
    assert len(repository.calls) == 1
    assert repository.calls[0][0].identifier == "sample"
    assert buffer.get("sample") is None


def test_buffer_keeps_audio_available_and_unmodified_when_repository_fails(tmp_path: Path) -> None:
    repository = RecordingRepository(error=OSError("persistence failed"))
    buffer = RecentAudioBuffer(repository=repository)
    buffer.store("sample", np.array([0.25, -0.5], dtype=np.float32), 16000)
    retained = buffer._entries["sample"][1]

    with pytest.raises(OSError, match="persistence failed"):
        buffer.save_confirmed("sample", tmp_path, reference="reference", confirmed=True)

    assert retained.tolist() == [0.25, -0.5]
    current = buffer.get("sample")
    assert current is not None
    assert current[0].tolist() == [0.25, -0.5]
    assert current[1] == 16000


def test_file_repository_preserves_wav_manifest_bytes_and_private_modes(tmp_path: Path) -> None:
    directory = tmp_path / "corpus" / "nested"
    repository = FileFeedbackCorpusRepository()
    audio = np.array([-2, -0.5, 0, 0.5, 2], dtype=np.float32)

    output = repository.save(
        FeedbackSample("sample", audio, 16000, "VoiceBud", ("VoiceBud", "Parakeet")),
        directory,
    )

    assert output == directory / "sample.wav"
    assert output.read_bytes() == _wav_bytes(audio, 16000)
    assert (directory / "manifest.jsonl").read_text(encoding="utf-8") == (
        '{"id": "sample", "audio": "sample.wav", "reference": "VoiceBud", '
        '"required_terms": ["VoiceBud", "Parakeet"]}\n'
    )
    assert _mode(directory.parent) == 0o700
    assert _mode(directory) == 0o700
    assert _mode(output) == 0o600
    assert _mode(directory / "manifest.jsonl") == 0o600
    assert not list(directory.glob(".voicebud-audio-*"))
    assert not list(directory.glob(".manifest-*"))


def test_file_repository_preserves_existing_custom_directory_mode(tmp_path: Path) -> None:
    directory = tmp_path / "custom"
    directory.mkdir(mode=0o755)
    os.chmod(directory, 0o755)

    FileFeedbackCorpusRepository().save(
        FeedbackSample("sample", np.array([0.25], dtype=np.float32), 16000, "sample", ()),
        directory,
    )

    assert _mode(directory) == 0o755


@pytest.mark.parametrize("prior_manifest", [False, True])
@pytest.mark.parametrize("invalid_field", ["reference", "required_terms"])
def test_nonserializable_manifest_record_is_rejected_before_filesystem_mutation(
    tmp_path: Path,
    prior_manifest: bool,
    invalid_field: str,
) -> None:
    directory = tmp_path / "corpus"
    manifest = directory / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    if prior_manifest:
        directory.mkdir()
        manifest.write_bytes(original_manifest)
        os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    arguments: dict[str, object] = {
        "reference": "sample",
        "required_terms": (),
    }
    arguments[invalid_field] = object()

    with pytest.raises(TypeError, match="JSON serializable"):
        buffer.save_confirmed(
            "sample",
            directory,
            confirmed=True,
            **arguments,  # type: ignore[arg-type]
        )

    assert buffer.get("sample") is not None
    assert not (directory / "sample.wav").exists()
    if prior_manifest:
        assert manifest.read_bytes() == original_manifest
        assert _mode(manifest) == 0o640
        assert sorted(path.name for path in directory.iterdir()) == ["manifest.jsonl"]
    else:
        assert not directory.exists()


def test_manifest_record_is_not_serialized_again_when_stateful_terms_become_invalid(
    tmp_path: Path,
) -> None:
    directory = tmp_path / "corpus"
    required_terms = StatefulRequiredTerms(("first",), (object(),))
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    output = buffer.save_confirmed(
        "sample",
        directory,
        reference="sample",
        required_terms=required_terms,
        confirmed=True,
    )

    assert output == directory / "sample.wav"
    assert required_terms.iteration_count == 1
    assert buffer.get("sample") is None
    record = json.loads((directory / "manifest.jsonl").read_text(encoding="utf-8"))
    assert record["required_terms"] == ["first"]


def test_manifest_record_publishes_the_first_serialization_of_stateful_terms(
    tmp_path: Path,
) -> None:
    required_terms = StatefulRequiredTerms(("first",), ("second",))

    FileFeedbackCorpusRepository().save(
        FeedbackSample(
            "sample",
            np.array([0.25], dtype=np.float32),
            16000,
            "sample",
            required_terms,
        ),
        tmp_path,
    )

    assert required_terms.iteration_count == 1
    record = json.loads((tmp_path / "manifest.jsonl").read_text(encoding="utf-8"))
    assert record["required_terms"] == ["first"]


@pytest.mark.parametrize(
    "identifier",
    (
        "",
        ".",
        "..",
        "../escaped",
        "..\\escaped",
        "/absolute",
        "nested/sample",
        "nested\\sample",
        "nul\0byte",
    ),
)
def test_file_repository_rejects_unsafe_identifier_before_filesystem_mutation(
    tmp_path: Path,
    identifier: str,
) -> None:
    directory = tmp_path / "corpus"

    with pytest.raises(ValueError, match="safe filename component"):
        FileFeedbackCorpusRepository().save(
            FeedbackSample(
                identifier,
                np.array([0.25], dtype=np.float32),
                16000,
                "sample",
                (),
            ),
            directory,
        )

    assert not directory.exists()
    assert not (tmp_path / "escaped.wav").exists()


def test_unsafe_identifier_preserves_facade_buffer_and_filesystem(tmp_path: Path) -> None:
    directory = tmp_path / "corpus"
    escaped = tmp_path / "escaped.wav"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("../escaped", np.array([0.25], dtype=np.float32), 16000)

    with pytest.raises(ValueError, match="safe filename component"):
        buffer.save_confirmed(
            "../escaped",
            directory,
            reference="sample",
            confirmed=True,
        )

    assert not directory.exists()
    assert not escaped.exists()
    retained = buffer.get("../escaped")
    assert retained is not None
    assert retained[0].tolist() == [0.25]


def test_file_repository_uses_deterministic_collision_suffix_without_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    original = tmp_path / "sample.wav"
    original.write_bytes(b"existing")
    os.chmod(original, 0o644)
    suffixes = iter(("collision-one", "collision-one", "collision-two"))
    monkeypatch.setattr(feedback_corpus, "uuid4", lambda: next(suffixes))
    repository = FileFeedbackCorpusRepository()
    sample = FeedbackSample("sample", np.array([0.25], dtype=np.float32), 16000, "sample", ())

    first = repository.save(sample, tmp_path)
    second = repository.save(sample, tmp_path)

    assert first == tmp_path / "sample-collision-one.wav"
    assert second == tmp_path / "sample-collision-two.wav"
    assert original.read_bytes() == b"existing"
    assert _mode(original) == 0o600
    assert first.is_file() and second.is_file()
    records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert [record["audio"] for record in records] == [first.name, second.name]


def test_manifest_failure_rolls_back_only_the_transaction_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    original_wav = tmp_path / "sample.wav"
    original_wav.write_bytes(b"existing audio")
    os.chmod(original_wav, 0o600)
    manifest = tmp_path / "manifest.jsonl"
    original_manifest = '{"id":"existing","audio":"sample.wav"}\n'
    manifest.write_text(original_manifest, encoding="utf-8")
    os.chmod(manifest, 0o640)
    monkeypatch.setattr(feedback_corpus, "uuid4", lambda: "new")
    original_replace = feedback_corpus.replace_private_file

    def fail_manifest_replace(source: Path, destination: Path) -> None:
        if destination == manifest:
            raise OSError("manifest replace failed")
        original_replace(source, destination)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", fail_manifest_replace)

    with pytest.raises(OSError, match="manifest replace failed"):
        FileFeedbackCorpusRepository().save(
            FeedbackSample("sample", np.array([0.25], dtype=np.float32), 16000, "sample", ()),
            tmp_path,
        )

    assert original_wav.read_bytes() == b"existing audio"
    assert manifest.read_text(encoding="utf-8") == original_manifest
    assert _mode(manifest) == 0o640
    assert not (tmp_path / "sample-new.wav").exists()
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_post_publication_wav_error_removes_owned_wav_and_retains_buffer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    manifest.write_bytes(original_manifest)
    os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination.name == "sample.wav":
            raise OSError("wav post-publication failure")

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)

    with pytest.raises(OSError, match="wav post-publication failure"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert not (tmp_path / "sample.wav").exists()
    assert manifest.read_bytes() == original_manifest
    assert _mode(manifest) == 0o640
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_wav_reconciliation_never_deletes_a_superseding_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    output = tmp_path / "sample.wav"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file

    def publish_supersede_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == output:
            superseding = tmp_path / ".superseding.wav"
            superseding.write_bytes(b"another transaction")
            os.replace(superseding, destination)
            raise OSError("ambiguous WAV publication")

    monkeypatch.setattr(
        feedback_corpus,
        "replace_private_file",
        publish_supersede_then_fail,
    )

    with pytest.raises(OSError, match="feedback corpus reconciliation failed"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert output.read_bytes() == b"another transaction"
    assert not (tmp_path / "manifest.jsonl").exists()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_owned_wav_removal_restores_a_superseding_entry_swapped_after_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    os.chmod(output, 0o600)
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    original_identity = FileFeedbackCorpusRepository._path_identity
    swapped = False

    def identify_then_supersede(path: Path) -> tuple[int, int] | None:
        nonlocal swapped
        identity = original_identity(path)
        if path == output and not swapped:
            swapped = True
            superseding = tmp_path / ".superseding.wav"
            superseding.write_bytes(b"superseding transaction")
            os.chmod(superseding, 0o640)
            os.replace(superseding, output)
        return identity

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_path_identity",
        identify_then_supersede,
    )

    with pytest.raises(OSError, match="owned-file identity check"):
        FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert output.read_bytes() == b"superseding transaction"
    assert _mode(output) == 0o640
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))


def test_owned_wav_removal_preserves_a_superseding_entry_published_after_quarantine(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    original_move = FileFeedbackCorpusRepository._rename_no_replace

    def quarantine_then_supersede(source: Path, destination: Path) -> None:
        original_move(source, destination)
        if source == output:
            output.write_bytes(b"superseding transaction")
            os.chmod(output, 0o640)

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        quarantine_then_supersede,
    )

    FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert output.read_bytes() == b"superseding transaction"
    assert _mode(output) == 0o640
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))


def test_owned_wav_removal_reconciles_an_ambiguous_quarantine_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    original_move = FileFeedbackCorpusRepository._rename_no_replace
    injected = False

    def move_then_report_failure(source: Path, destination: Path) -> None:
        nonlocal injected
        original_move(source, destination)
        if source == output and not injected:
            injected = True
            raise OSError("ambiguous quarantine publication")

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        move_then_report_failure,
    )

    FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert injected
    assert not output.exists()
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))


def test_owned_wav_removal_preserves_both_entries_on_ambiguous_quarantine_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    collision = tmp_path / ".voiceflow-rollback-collision"

    def collide_before_move(source: Path, destination: Path) -> None:
        assert source == output
        destination.write_bytes(b"unrelated collision")
        os.chmod(destination, 0o640)
        raise OSError(errno.EIO, "ambiguous quarantine publication")

    monkeypatch.setattr(feedback_corpus, "uuid4", lambda: "collision")
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        collide_before_move,
    )

    with pytest.raises(OSError, match="owned-file quarantine publication"):
        FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert output.read_bytes() == b"owned transaction"
    assert collision.read_bytes() == b"unrelated collision"
    assert _mode(collision) == 0o640


def test_owned_wav_removal_reports_exhausted_quarantine_name_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    collision = tmp_path / ".voiceflow-rollback-collision"
    collision.write_bytes(b"unrelated collision")
    monkeypatch.setattr(feedback_corpus, "uuid4", lambda: "collision")

    with pytest.raises(OSError, match="owned-file quarantine collision"):
        FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert output.read_bytes() == b"owned transaction"
    assert collision.read_bytes() == b"unrelated collision"


def test_unowned_quarantine_is_restored_at_path_when_a_new_entry_collides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "sample.wav"
    output.write_bytes(b"owned transaction")
    owned_state = output.stat()
    owned_identity = owned_state.st_dev, owned_state.st_ino
    original_identity = FileFeedbackCorpusRepository._path_identity
    original_move = FileFeedbackCorpusRepository._rename_no_replace
    swapped = False

    def identify_then_supersede(path: Path) -> tuple[int, int] | None:
        nonlocal swapped
        identity = original_identity(path)
        if path == output and not swapped:
            swapped = True
            superseding = tmp_path / ".superseding.wav"
            superseding.write_bytes(b"captured superseding transaction")
            os.chmod(superseding, 0o640)
            os.replace(superseding, output)
        return identity

    def move_then_collide(source: Path, destination: Path) -> None:
        original_move(source, destination)
        if source == output:
            output.write_bytes(b"later colliding transaction")

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_path_identity",
        identify_then_supersede,
    )
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        move_then_collide,
    )

    with pytest.raises(OSError, match="unowned-file quarantine restoration collision"):
        FileFeedbackCorpusRepository._remove_owned_path(output, owned_identity)

    assert output.read_bytes() == b"captured superseding transaction"
    assert _mode(output) == 0o640
    rescued = list(tmp_path.glob(".voiceflow-rollback-*"))
    assert len(rescued) == 1
    assert rescued[0].read_bytes() == b"later colliding transaction"


def test_reconciled_identity_uses_no_follow_descriptor_after_bounded_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "sample.wav"
    output.write_bytes(b"transaction")
    state = output.stat()
    attempts = 0

    def fail_identity(_path: Path) -> tuple[int, int] | None:
        nonlocal attempts
        attempts += 1
        raise OSError("identity unavailable")

    monkeypatch.setattr(FileFeedbackCorpusRepository, "_path_identity", fail_identity)

    assert FileFeedbackCorpusRepository._reconciled_path_identity(output) == (
        state.st_dev,
        state.st_ino,
    )
    assert attempts == 2


def test_reconciled_identity_descriptor_fallback_distinguishes_absence_and_symlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing"
    target = tmp_path / "target"
    target.write_bytes(b"target")
    link = tmp_path / "link"
    link.symlink_to(target)

    def fail_identity(_path: Path) -> tuple[int, int] | None:
        raise OSError("identity unavailable")

    monkeypatch.setattr(FileFeedbackCorpusRepository, "_path_identity", fail_identity)

    assert FileFeedbackCorpusRepository._reconciled_path_identity(missing) is None
    with pytest.raises(OSError):
        FileFeedbackCorpusRepository._reconciled_path_identity(link)


def test_identity_helpers_reject_non_regular_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "directory"
    directory.mkdir()

    with pytest.raises(OSError, match="not a regular file"):
        FileFeedbackCorpusRepository._path_identity(directory)

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_path_identity",
        lambda _path: (_ for _ in ()).throw(OSError("identity unavailable")),
    )
    with pytest.raises(OSError, match="not a regular file"):
        FileFeedbackCorpusRepository._reconciled_path_identity(directory)


def test_restored_manifest_match_requires_the_expected_inode(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_bytes(b"prior\n")
    state = manifest.stat()

    assert not FileFeedbackCorpusRepository._restored_manifest_matches(
        manifest,
        (state.st_dev, state.st_ino + 1),
        b"prior\n",
    )


def test_tracked_replace_rejects_a_success_without_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_bytes(b"transaction")
    state = source.stat()
    replacement = feedback_corpus._ReplacementState(
        destination,
        identity=(state.st_dev, state.st_ino),
    )
    monkeypatch.setattr(feedback_corpus, "replace_private_file", lambda *_args: None)

    with pytest.raises(OSError, match="without publishing"):
        FileFeedbackCorpusRepository._replace_tracked(source, replacement)

    assert source.read_bytes() == b"transaction"
    assert not destination.exists()


def test_manifest_snapshot_rejects_an_entry_that_appears_during_absence_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"

    def publish_manifest(_path: Path) -> str:
        manifest.write_bytes(b"superseding\n")
        return ""

    monkeypatch.setattr(feedback_corpus, "read_managed_text_file", publish_manifest)

    with pytest.raises(OSError, match="appeared while capturing"):
        FileFeedbackCorpusRepository._snapshot_manifest(manifest)


def test_manifest_snapshot_rejects_non_regular_and_changing_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    target = tmp_path / "target"
    target.write_bytes(b"target")
    manifest = tmp_path / "manifest.jsonl"
    manifest.symlink_to(target)
    with pytest.raises(OSError, match="not a regular file"):
        FileFeedbackCorpusRepository._snapshot_manifest(manifest)

    manifest.unlink()
    manifest.write_bytes(b"prior\n")
    original_read = feedback_corpus.read_managed_text_file

    def replace_during_read(path: Path) -> str:
        contents = original_read(path)
        replacement = tmp_path / ".replacement"
        replacement.write_bytes(b"changed\n")
        os.replace(replacement, path)
        return contents

    monkeypatch.setattr(feedback_corpus, "read_managed_text_file", replace_during_read)
    with pytest.raises(OSError, match="changed while capturing"):
        FileFeedbackCorpusRepository._snapshot_manifest(manifest)


def test_atomic_exchange_swaps_both_directory_entries(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    FileFeedbackCorpusRepository._rename_exchange(first, second)

    assert first.read_bytes() == b"second"
    assert second.read_bytes() == b"first"


def test_native_atomic_rename_uses_linux_renameat2_flags(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    calls: list[tuple[object, ...]] = []

    class NativeCall:
        argtypes: object = None
        restype: object = None

        def __call__(self, *arguments: object) -> int:
            calls.append(arguments)
            return 0

    class Library:
        renameat2 = NativeCall()

    monkeypatch.setattr(feedback_corpus.sys, "platform", "linux")
    monkeypatch.setattr(feedback_corpus.ctypes, "CDLL", lambda *_args, **_kwargs: Library())

    FileFeedbackCorpusRepository._native_rename(
        tmp_path / "source",
        tmp_path / "destination",
        darwin_flags=4,
        linux_flags=1,
    )

    assert calls == [
        (
            -100,
            os.fsencode(tmp_path / "source"),
            -100,
            os.fsencode(tmp_path / "destination"),
            1,
        )
    ]


@pytest.mark.parametrize("platform", ["freebsd", "linux-no-renameat2"])
def test_native_atomic_rename_fails_closed_when_unsupported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    monkeypatch.setattr(feedback_corpus.sys, "platform", platform)
    monkeypatch.setattr(feedback_corpus.ctypes, "CDLL", lambda *_args, **_kwargs: object())

    with pytest.raises(OSError) as error:
        FileFeedbackCorpusRepository._native_rename(
            tmp_path / "source",
            tmp_path / "destination",
            darwin_flags=4,
            linux_flags=1,
        )

    assert error.value.errno == errno.ENOTSUP


def test_native_atomic_rename_propagates_native_errno(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    class NativeCall:
        argtypes: object = None
        restype: object = None

        def __call__(self, *_arguments: object) -> int:
            return -1

    class Library:
        renamex_np = NativeCall()

    monkeypatch.setattr(feedback_corpus.ctypes, "CDLL", lambda *_args, **_kwargs: Library())
    monkeypatch.setattr(feedback_corpus.ctypes, "get_errno", lambda: errno.EIO)

    with pytest.raises(OSError) as error:
        FileFeedbackCorpusRepository._native_rename(
            tmp_path / "source",
            tmp_path / "destination",
            darwin_flags=4,
            linux_flags=1,
        )

    assert error.value.errno == errno.EIO


@pytest.mark.parametrize("prior_manifest", [False, True])
def test_post_publication_manifest_error_restores_prior_state_before_wav_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_manifest: bool,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    if prior_manifest:
        manifest.write_bytes(original_manifest)
        os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)

    with pytest.raises(OSError, match="manifest post-publication failure"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert not (tmp_path / "sample.wav").exists()
    if prior_manifest:
        assert manifest.read_bytes() == original_manifest
        assert _mode(manifest) == 0o640
    else:
        assert not manifest.exists()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_transient_manifest_quarantine_unlink_error_completes_rollback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    unlink_injected = False

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def fail_manifest_quarantine_unlink_once(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_injected
        if path.name.startswith(".voiceflow-rollback-") and not unlink_injected:
            unlink_injected = True
            raise OSError(errno.EIO, "injected quarantine unlink failure", path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", fail_manifest_quarantine_unlink_once)

    with pytest.raises(OSError, match="manifest post-publication failure"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert unlink_injected
    assert not manifest.exists()
    assert not (tmp_path / "sample.wav").exists()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_persistent_manifest_quarantine_unlink_error_finishes_verified_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    unlink_attempts = 0

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def always_fail_quarantine_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_attempts
        if path.name.startswith(".voiceflow-rollback-"):
            unlink_attempts += 1
            raise OSError(errno.EIO, "persistent quarantine unlink failure", path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", always_fail_quarantine_unlink)

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert unlink_attempts > 1
    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == output.name
    assert _mode(manifest) == 0o600
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_transient_wav_quarantine_unlink_error_restores_prior_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    manifest.write_bytes(original_manifest)
    os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    unlink_injected = False

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def fail_wav_quarantine_unlink_once(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_injected
        if path.name.startswith(".voiceflow-rollback-") and not unlink_injected:
            unlink_injected = True
            raise OSError(errno.EIO, "injected WAV quarantine unlink failure", path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", fail_wav_quarantine_unlink_once)

    with pytest.raises(OSError, match="manifest post-publication failure"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert unlink_injected
    assert manifest.read_bytes() == original_manifest
    assert _mode(manifest) == 0o640
    assert not (tmp_path / "sample.wav").exists()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_persistent_wav_quarantine_unlink_error_converts_rollback_to_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    manifest.write_bytes(original_manifest)
    os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    unlink_attempts = 0

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def always_fail_quarantine_unlink(path: Path, *args: object, **kwargs: object) -> None:
        nonlocal unlink_attempts
        if path.name.startswith(".voiceflow-rollback-"):
            unlink_attempts += 1
            raise OSError(errno.EIO, "persistent quarantine unlink failure", path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", always_fail_quarantine_unlink)

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert unlink_attempts == 3
    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    assert manifest.read_bytes().startswith(original_manifest)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == output.name
    assert _mode(manifest) == 0o600
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_persistent_wav_unlink_after_new_manifest_removal_republishes_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    quarantine_unlinks = 0

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def remove_manifest_but_reject_wav_unlink(
        path: Path,
        *args: object,
        **kwargs: object,
    ) -> None:
        nonlocal quarantine_unlinks
        if path.name.startswith(".voiceflow-rollback-"):
            quarantine_unlinks += 1
            if quarantine_unlinks > 1:
                raise OSError(errno.EIO, "persistent WAV quarantine unlink failure", path)
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", remove_manifest_but_reject_wav_unlink)

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert quarantine_unlinks == 4
    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == output.name
    assert _mode(manifest) == 0o600
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


@pytest.mark.parametrize("persistent_rename_failure", [False, True])
def test_new_manifest_commit_fallback_survives_no_replace_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    persistent_rename_failure: bool,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    original_rename = FileFeedbackCorpusRepository._rename_no_replace
    quarantine_unlinks = 0
    fallback_rename_failures = 0

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("manifest post-publication failure")

    def reject_wav_quarantine_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        nonlocal quarantine_unlinks
        if path.name.startswith(".voiceflow-rollback-"):
            quarantine_unlinks += 1
            if quarantine_unlinks > 1:
                raise OSError(errno.EIO, "persistent WAV quarantine unlink failure", path)
        original_unlink(path, missing_ok=missing_ok)

    def fail_fallback_rename(source: Path, destination: Path) -> None:
        nonlocal fallback_rename_failures
        if source.name.startswith(".manifest-commit-"):
            fallback_rename_failures += 1
            if persistent_rename_failure or fallback_rename_failures == 1:
                raise OSError(errno.EIO, "fallback no-replace failure", source)
        original_rename(source, destination)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", reject_wav_quarantine_unlink)
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        fail_fallback_rename,
    )

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert fallback_rename_failures >= 1
    assert output is not None
    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == output.name
    assert _mode(manifest) == 0o600
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_prior_manifest_commit_fallback_hardens_before_replacing_prior_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    manifest.write_bytes(original_manifest)
    os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_unlink = Path.unlink
    original_fchmod = feedback_corpus.os.fchmod
    quarantine_unlinks = 0
    injected = False

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest and source.name.startswith(".manifest-"):
            raise OSError("manifest post-publication failure")

    def reject_wav_quarantine_unlink(
        path: Path,
        missing_ok: bool = False,
    ) -> None:
        nonlocal quarantine_unlinks
        if path.name.startswith(".voiceflow-rollback-"):
            quarantine_unlinks += 1
            raise OSError(errno.EIO, "persistent WAV quarantine unlink failure", path)
        original_unlink(path, missing_ok=missing_ok)

    def fail_first_commit_hardening(descriptor: int, mode: int) -> None:
        nonlocal injected
        if quarantine_unlinks == 3 and mode == 0o600 and not injected:
            injected = True
            raise OSError(errno.EIO, "fallback manifest hardening failure")
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(Path, "unlink", reject_wav_quarantine_unlink)
    monkeypatch.setattr(feedback_corpus.os, "fchmod", fail_first_commit_hardening)

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert injected
    assert output is not None
    assert output == tmp_path / "sample.wav"
    assert manifest.read_bytes().startswith(original_manifest)
    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == output.name
    assert _mode(manifest) == 0o600
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))
    assert not list(tmp_path.glob(".voicebud-audio-*"))


def test_commit_verification_rejects_incomplete_or_superseding_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest_path = tmp_path / "manifest.jsonl"
    manifest_path.write_bytes(b"transaction\n")
    os.chmod(manifest_path, 0o600)
    manifest_state = manifest_path.stat()
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"wav")
    wav_state = wav_path.stat()
    manifest = feedback_corpus._ReplacementState(
        manifest_path,
        identity=(manifest_state.st_dev, manifest_state.st_ino),
        contents=b"transaction\n",
    )
    wav = feedback_corpus._ReplacementState(
        wav_path,
        identity=(wav_state.st_dev, wav_state.st_ino),
    )

    assert not FileFeedbackCorpusRepository._transaction_is_committed(
        feedback_corpus._ReplacementState(manifest_path),
        wav,
    )

    original_identity = FileFeedbackCorpusRepository._reconciled_path_identity

    def fail_wav_identity(path: Path) -> tuple[int, int] | None:
        if path == wav_path:
            raise OSError("ambiguous WAV identity")
        return original_identity(path)

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_reconciled_path_identity",
        fail_wav_identity,
    )
    assert not FileFeedbackCorpusRepository._transaction_is_committed(manifest, wav)
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_reconciled_path_identity",
        original_identity,
    )

    wav.identity = (wav_state.st_dev, wav_state.st_ino + 1)
    assert not FileFeedbackCorpusRepository._transaction_is_committed(manifest, wav)
    wav.identity = (wav_state.st_dev, wav_state.st_ino)
    manifest_path.unlink()
    assert not FileFeedbackCorpusRepository._transaction_is_committed(manifest, wav)


def test_temporary_cleanup_reconciles_missing_transient_and_unowned_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    missing = tmp_path / "missing"
    assert FileFeedbackCorpusRepository._unlink_owned_temporary(missing, (1, 1))

    temporary = tmp_path / "temporary"
    temporary.write_bytes(b"transaction")
    state = temporary.stat()
    identity = state.st_dev, state.st_ino
    original_unlink = feedback_corpus.os.unlink
    unlink_attempts = 0

    def fail_once(path: Path) -> None:
        nonlocal unlink_attempts
        unlink_attempts += 1
        if unlink_attempts == 1:
            raise OSError(errno.EIO, "transient unlink failure", path)
        original_unlink(path)

    monkeypatch.setattr(feedback_corpus.os, "unlink", fail_once)
    assert FileFeedbackCorpusRepository._unlink_owned_temporary(temporary, identity)
    assert unlink_attempts == 2

    unowned = tmp_path / "unowned"
    unowned.write_bytes(b"other")
    monkeypatch.setattr(
        feedback_corpus.os,
        "unlink",
        lambda path: (_ for _ in ()).throw(OSError(errno.EIO, "persistent unlink", path)),
    )
    assert not FileFeedbackCorpusRepository._unlink_owned_temporary(unowned, (1, 1))

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_reconciled_path_identity",
        lambda path: (_ for _ in ()).throw(OSError(errno.EIO, "identity unavailable", path)),
    )
    assert not FileFeedbackCorpusRepository._unlink_owned_temporary(unowned, identity)


def test_owned_path_removal_accepts_an_ambiguously_disappeared_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "owned"
    path.write_bytes(b"owned")
    state = path.stat()

    def remove_then_fail(source: Path, destination: Path) -> None:
        source.unlink()
        raise OSError(errno.EIO, "ambiguous quarantine publication", source)

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_rename_no_replace",
        remove_then_fail,
    )
    FileFeedbackCorpusRepository._remove_owned_path(path, (state.st_dev, state.st_ino))
    assert not path.exists()


@pytest.mark.parametrize("cleanup_state", ["replacement", "no_op", "restore_no_op"])
def test_owned_path_removal_rejects_unverifiable_cleanup_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    cleanup_state: str,
) -> None:
    path = tmp_path / "owned"
    path.write_bytes(b"owned")
    state = path.stat()
    identity = state.st_dev, state.st_ino
    original_rename = FileFeedbackCorpusRepository._rename_no_replace
    original_unlink = Path.unlink

    def injected_unlink(target: Path, missing_ok: bool = False) -> None:
        if cleanup_state == "replacement":
            original_unlink(target, missing_ok=missing_ok)
            target.write_bytes(b"superseding")
        elif cleanup_state == "no_op":
            return
        else:
            raise OSError(errno.EIO, "persistent cleanup failure", target)

    def injected_rename(source: Path, destination: Path) -> None:
        if cleanup_state == "restore_no_op" and source.name.startswith(".voiceflow-rollback-"):
            return
        original_rename(source, destination)

    monkeypatch.setattr(Path, "unlink", injected_unlink)
    monkeypatch.setattr(FileFeedbackCorpusRepository, "_rename_no_replace", injected_rename)

    expected = (
        "owned-file removal verification"
        if cleanup_state == "replacement"
        else "owned-file quarantine cleanup"
        if cleanup_state == "no_op"
        else "owned-file quarantine restoration"
    )
    with pytest.raises(OSError, match=expected):
        FileFeedbackCorpusRepository._remove_owned_path(path, identity)


@pytest.mark.parametrize(
    ("publication", "expected"),
    [
        ("rename_then_error", True),
        ("superseding_destination", False),
        ("link_then_error", True),
        ("move_then_link_error", True),
        ("link_error", False),
        ("wrong_link", False),
    ],
)
def test_new_manifest_publication_reconciles_ambiguous_and_superseding_states(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    publication: str,
    expected: bool,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    temporary = tmp_path / ".manifest-commit-test"
    destination = tmp_path / "manifest.jsonl"
    temporary.write_bytes(b"transaction")
    state = temporary.stat()
    identity = state.st_dev, state.st_ino
    original_rename = FileFeedbackCorpusRepository._rename_no_replace
    original_link = feedback_corpus.os.link

    def fail_rename(source: Path, target: Path) -> None:
        if publication == "rename_then_error":
            original_rename(source, target)
        elif publication == "superseding_destination" and not target.exists():
            target.write_bytes(b"superseding")
        raise OSError(errno.EIO, "ambiguous no-replace", source)

    def publish_link_then_error(
        source: Path,
        target: Path,
        *,
        follow_symlinks: bool,
    ) -> None:
        if publication == "link_then_error":
            original_link(source, target, follow_symlinks=follow_symlinks)
        elif publication == "move_then_link_error":
            os.replace(source, target)
        elif publication == "wrong_link":
            target.write_bytes(b"other")
            return
        raise OSError(errno.EIO, "ambiguous link", source)

    monkeypatch.setattr(FileFeedbackCorpusRepository, "_rename_no_replace", fail_rename)
    monkeypatch.setattr(feedback_corpus.os, "link", publish_link_then_error)

    assert (
        FileFeedbackCorpusRepository._publish_new_manifest(temporary, destination, identity)
        is expected
    )

    if expected:
        assert destination.read_bytes() == b"transaction"
    elif destination.exists():
        expected_contents = b"superseding" if publication == "superseding_destination" else b"other"
        assert destination.read_bytes() == expected_contents
    else:
        assert publication == "link_error"


@pytest.mark.parametrize(
    ("exchange_state", "expected", "raises"),
    [
        ("fail_before", False, False),
        ("publish_then_error", True, False),
        ("captured_disappears", False, False),
        ("superseding", False, False),
        ("restore_then_error", False, False),
        ("restore_failure", False, True),
    ],
)
def test_prior_manifest_publication_preserves_superseding_entries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    exchange_state: str,
    expected: bool,
    raises: bool,
) -> None:
    temporary = tmp_path / ".manifest-commit-test"
    destination = tmp_path / "manifest.jsonl"
    temporary.write_bytes(b"transaction")
    destination.write_bytes(
        b"prior"
        if exchange_state in {"fail_before", "publish_then_error", "captured_disappears"}
        else b"superseding"
    )
    transaction_state = temporary.stat()
    prior_state = destination.stat()
    transaction_identity = transaction_state.st_dev, transaction_state.st_ino
    prior_identity = prior_state.st_dev, prior_state.st_ino
    if exchange_state not in {"fail_before", "publish_then_error", "captured_disappears"}:
        prior_identity = (prior_identity[0], prior_identity[1] + 1)
    original_exchange = FileFeedbackCorpusRepository._rename_exchange
    exchanges = 0

    def injected_exchange(first: Path, second: Path) -> None:
        nonlocal exchanges
        exchanges += 1
        if exchange_state == "fail_before":
            raise OSError(errno.EIO, "exchange failed before publication")
        if exchange_state == "restore_failure" and exchanges == 2:
            raise OSError(errno.EIO, "superseding restoration failed")
        original_exchange(first, second)
        if exchange_state == "captured_disappears":
            first.unlink()
        if exchange_state == "publish_then_error" or (
            exchange_state == "restore_then_error" and exchanges == 2
        ):
            raise OSError(errno.EIO, "ambiguous exchange")

    monkeypatch.setattr(FileFeedbackCorpusRepository, "_rename_exchange", injected_exchange)

    def operation() -> tuple[bool, tuple[int, int] | None]:
        return FileFeedbackCorpusRepository._publish_over_prior_manifest(
            temporary,
            destination,
            transaction_identity,
            prior_identity,
        )

    if raises:
        with pytest.raises(OSError, match="superseding manifest restoration"):
            operation()
        return

    published, captured = operation()
    assert published is expected
    if expected:
        assert captured == (prior_state.st_dev, prior_state.st_ino)
        assert destination.read_bytes() == b"transaction"
    elif exchange_state not in {"fail_before", "captured_disappears"}:
        assert destination.read_bytes() == b"superseding"


def test_private_file_preparation_rejects_unverified_fallback_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    path = tmp_path / "temporary"
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o640)
    os.fchmod(descriptor, 0o640)
    monkeypatch.setattr(
        feedback_corpus,
        "restrict_open_file",
        lambda opened: (_ for _ in ()).throw(OSError(errno.EIO, "fchmod failed")),
    )
    monkeypatch.setattr(feedback_corpus.os, "chmod", lambda *args, **kwargs: None)
    try:
        with pytest.raises(OSError, match="hardening did not take effect"):
            FileFeedbackCorpusRepository._prepare_private_file(descriptor, path, b"contents")
        assert path.read_bytes() == b""
    finally:
        os.close(descriptor)


def test_manifest_restore_handles_absent_and_unowned_new_destinations(tmp_path: Path) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    path = tmp_path / "manifest.jsonl"
    snapshot = feedback_corpus._ManifestSnapshot(False, b"", None, None)
    replacement = feedback_corpus._ReplacementState(path, identity=(1, 1))
    FileFeedbackCorpusRepository._restore_manifest(snapshot, replacement)

    path.write_bytes(b"superseding")
    with pytest.raises(OSError, match="manifest ownership check"):
        FileFeedbackCorpusRepository._restore_manifest(snapshot, replacement)


def test_failed_wav_rollback_commit_fallback_rejects_unowned_prior_manifest(
    tmp_path: Path,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest_path = tmp_path / "manifest.jsonl"
    prior = b"prior\n"
    manifest_path.write_bytes(prior)
    os.chmod(manifest_path, 0o640)
    manifest_state = manifest_path.stat()
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"wav")
    wav_state = wav_path.stat()
    snapshot = feedback_corpus._ManifestSnapshot(
        True,
        prior,
        0o640,
        (manifest_state.st_dev, manifest_state.st_ino),
    )
    manifest = feedback_corpus._ReplacementState(
        manifest_path,
        contents=b"prior\ntransaction\n",
    )
    wav = feedback_corpus._ReplacementState(
        wav_path,
        identity=(wav_state.st_dev, wav_state.st_ino),
    )

    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    manifest.rollback_identity = (manifest_state.st_dev, manifest_state.st_ino + 1)
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    manifest.rollback_identity = (manifest_state.st_dev, manifest_state.st_ino)
    snapshot = feedback_corpus._ManifestSnapshot(
        True,
        b"different prior\n",
        0o640,
        snapshot.identity,
    )
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    assert manifest_path.read_bytes() == prior
    assert _mode(manifest_path) == 0o640


def test_failed_wav_rollback_commit_fallback_rejects_incomplete_observations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest_path = tmp_path / "manifest.jsonl"
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"wav")
    wav_state = wav_path.stat()
    wav = feedback_corpus._ReplacementState(
        wav_path,
        identity=(wav_state.st_dev, wav_state.st_ino),
    )
    missing_contents = feedback_corpus._ReplacementState(manifest_path)
    snapshot = feedback_corpus._ManifestSnapshot(False, b"", None, None)
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        missing_contents,
        wav,
    )

    manifest = feedback_corpus._ReplacementState(manifest_path, contents=b"transaction\n")
    original_identity = FileFeedbackCorpusRepository._reconciled_path_identity

    wav.identity = (wav_state.st_dev, wav_state.st_ino + 1)
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    wav.identity = (wav_state.st_dev, wav_state.st_ino)

    def fail_wav_observation(path: Path) -> tuple[int, int] | None:
        if path == wav_path:
            raise OSError(errno.EIO, "WAV identity unavailable", path)
        return original_identity(path)

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_reconciled_path_identity",
        fail_wav_observation,
    )
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_reconciled_path_identity",
        original_identity,
    )

    manifest_path.write_bytes(b"superseding")
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )
    manifest_path.unlink()
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_publish_new_manifest",
        lambda temporary, path, identity: False,
    )
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )


def test_prior_commit_fallback_rejects_failed_publication_and_verification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest_path = tmp_path / "manifest.jsonl"
    prior = b"prior\n"
    manifest_path.write_bytes(prior)
    os.chmod(manifest_path, 0o640)
    manifest_state = manifest_path.stat()
    prior_identity = manifest_state.st_dev, manifest_state.st_ino
    wav_path = tmp_path / "sample.wav"
    wav_path.write_bytes(b"wav")
    wav_state = wav_path.stat()
    snapshot = feedback_corpus._ManifestSnapshot(True, prior, 0o640, prior_identity)
    manifest = feedback_corpus._ReplacementState(
        manifest_path,
        contents=b"prior\ntransaction\n",
        rollback_identity=prior_identity,
    )
    wav = feedback_corpus._ReplacementState(
        wav_path,
        identity=(wav_state.st_dev, wav_state.st_ino),
    )

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_publish_over_prior_manifest",
        lambda temporary, path, transaction_identity, expected_prior: (False, None),
    )
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )

    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_publish_over_prior_manifest",
        lambda temporary, path, transaction_identity, expected_prior: (True, prior_identity),
    )
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_transaction_is_committed",
        lambda transaction_manifest, transaction_wav: False,
    )
    assert not FileFeedbackCorpusRepository._finish_commit_after_failed_wav_rollback(
        snapshot,
        manifest,
        wav,
    )


@pytest.mark.parametrize("prior_manifest", [False, True])
def test_transient_identity_failure_during_manifest_restore_still_rolls_back_wav(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    prior_manifest: bool,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    if prior_manifest:
        manifest.write_bytes(original_manifest)
        os.chmod(manifest, 0o640)
    original_replace = feedback_corpus.replace_private_file
    original_identity = FileFeedbackCorpusRepository._path_identity
    manifest_publications = 0
    injected = False

    def publish_manifest_then_fail(source: Path, destination: Path) -> None:
        nonlocal manifest_publications
        original_replace(source, destination)
        if destination == manifest:
            manifest_publications += 1
            if manifest_publications == 1:
                raise OSError("ambiguous manifest publication")

    def fail_once_during_restore(path: Path) -> tuple[int, int] | None:
        nonlocal injected
        if (
            path == manifest
            and not injected
            and (
                (prior_manifest and manifest_publications == 2)
                or (not prior_manifest and manifest_publications == 1)
            )
        ):
            injected = True
            raise OSError("transient manifest identity failure")
        return original_identity(path)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_manifest_then_fail)
    monkeypatch.setattr(
        FileFeedbackCorpusRepository,
        "_path_identity",
        fail_once_during_restore,
    )

    with pytest.raises(OSError, match="ambiguous manifest publication"):
        FileFeedbackCorpusRepository().save(
            FeedbackSample("sample", np.array([0.25], dtype=np.float32), 16000, "sample", ()),
            tmp_path,
        )

    assert injected
    assert not (tmp_path / "sample.wav").exists()
    if prior_manifest:
        assert manifest.read_bytes() == original_manifest
        assert _mode(manifest) == 0o640
    else:
        assert not manifest.exists()
    assert not list(tmp_path.glob(".voiceflow-rollback-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_manifest_reconciliation_preserves_a_superseding_consistent_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"existing","audio":"existing.wav"}\n', encoding="utf-8")
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    superseding_contents = b'{"id":"other","audio":"sample.wav"}\n'

    def publish_supersede_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            superseding = tmp_path / ".superseding-manifest"
            superseding.write_bytes(superseding_contents)
            os.replace(superseding, destination)
            raise OSError("ambiguous manifest publication")

    monkeypatch.setattr(
        feedback_corpus,
        "replace_private_file",
        publish_supersede_then_fail,
    )

    with pytest.raises(OSError, match="feedback corpus reconciliation failed"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert manifest.read_bytes() == superseding_contents
    assert (tmp_path / "sample.wav").is_file()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".manifest-*"))


def test_manifest_restore_publication_failure_returns_verified_committed_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text('{"id":"existing","audio":"existing.wav"}\n', encoding="utf-8")
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    manifest_replacements = 0

    def fail_restore_before_publication(source: Path, destination: Path) -> None:
        nonlocal manifest_replacements
        if destination != manifest:
            original_replace(source, destination)
            return
        manifest_replacements += 1
        if manifest_replacements == 1:
            original_replace(source, destination)
            raise OSError("ambiguous manifest publication")
        raise OSError("manifest restore publication failed")

    monkeypatch.setattr(
        feedback_corpus,
        "replace_private_file",
        fail_restore_before_publication,
    )

    output = buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    records = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines()]
    assert records[-1]["audio"] == "sample.wav"
    assert output == tmp_path / "sample.wav"
    assert output.is_file()
    assert buffer.get("sample") is None
    assert not list(tmp_path.glob(".manifest-*"))


def test_manifest_restore_mode_failure_rolls_back_wav_after_restoring_prior_bytes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    manifest = tmp_path / "manifest.jsonl"
    original_manifest = b'{"id":"existing","audio":"existing.wav"}\n'
    manifest.write_bytes(original_manifest)
    os.chmod(manifest, 0o640)
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    original_replace = feedback_corpus.replace_private_file
    original_fchmod = feedback_corpus.os.fchmod

    def publish_then_fail(source: Path, destination: Path) -> None:
        original_replace(source, destination)
        if destination == manifest:
            raise OSError("ambiguous manifest publication")

    def fail_original_mode_restore(descriptor: int, mode: int) -> None:
        if mode == 0o640:
            raise OSError("manifest mode restore failed")
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(feedback_corpus, "replace_private_file", publish_then_fail)
    monkeypatch.setattr(feedback_corpus.os, "fchmod", fail_original_mode_restore)

    with pytest.raises(
        OSError,
        match="feedback corpus reconciliation failed during manifest restoration verification",
    ):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert manifest.read_bytes() == original_manifest
    assert not (tmp_path / "sample.wav").exists()
    assert buffer.get("sample") is not None
    assert not list(tmp_path.glob(".manifest-*"))


def test_concurrent_same_identifier_saves_select_distinct_committed_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    first_selection = threading.Event()
    second_selection = threading.Event()
    selection_count = 0
    selection_guard = threading.Lock()
    original_restrict = feedback_corpus.restrict_file_permissions

    def coordinate_initial_selection(path: Path) -> bool:
        nonlocal selection_count
        exists = original_restrict(path)
        if path.name != "same.wav":
            return exists
        with selection_guard:
            selection_count += 1
            position = selection_count
        if position == 1:
            first_selection.set()
            second_selection.wait(timeout=0.25)
        elif position == 2:
            second_selection.set()
        return exists

    monkeypatch.setattr(
        feedback_corpus,
        "restrict_file_permissions",
        coordinate_initial_selection,
    )
    repository = FileFeedbackCorpusRepository()
    sample = FeedbackSample("same", np.array([0.25], dtype=np.float32), 16000, "same", ())

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(lambda _index: repository.save(sample, tmp_path), range(2)))

    assert first_selection.is_set()
    assert len(set(outputs)) == 2
    assert all(output.is_file() for output in outputs)
    records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {record["audio"] for record in records} == {output.name for output in outputs}
    assert all((tmp_path / record["audio"]).is_file() for record in records)


def test_concurrent_buffer_confirmations_of_same_identifier_commit_distinct_paths(
    tmp_path: Path,
) -> None:
    ready = threading.Barrier(2)
    delegate = FileFeedbackCorpusRepository()

    class CoordinatedRepository:
        def save(self, sample: FeedbackSample, directory: Path) -> Path:
            ready.wait()
            return delegate.save(sample, directory)

    buffer = RecentAudioBuffer(repository=CoordinatedRepository())
    buffer.store("same", np.array([0.25], dtype=np.float32), 16000)

    def confirm(reference: str) -> Path | None:
        return buffer.save_confirmed(
            "same",
            tmp_path,
            reference=reference,
            confirmed=True,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        outputs = list(executor.map(confirm, ("first", "second")))

    assert all(output is not None for output in outputs)
    committed = [output for output in outputs if output is not None]
    assert len(set(committed)) == 2
    assert all(output.is_file() for output in committed)
    records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {record["audio"] for record in records} == {output.name for output in committed}
    assert all((tmp_path / record["audio"]).is_file() for record in records)
    assert buffer.get("same") is None


def test_repository_instances_share_directory_transaction_lock_on_mixed_outcomes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import voiceflow.storage.feedback_corpus as feedback_corpus

    failure_entered = threading.Event()
    success_started = threading.Event()
    context = threading.local()
    original_read = feedback_corpus.read_managed_text_file

    def fail_one_manifest_read(path: Path) -> str:
        if getattr(context, "fail", False):
            failure_entered.set()
            assert success_started.wait(timeout=1)
            raise OSError("manifest read failed")
        return original_read(path)

    monkeypatch.setattr(feedback_corpus, "read_managed_text_file", fail_one_manifest_read)
    sample = FeedbackSample("same", np.array([0.25], dtype=np.float32), 16000, "same", ())

    def failing_save() -> Path:
        context.fail = True
        return FileFeedbackCorpusRepository().save(sample, tmp_path)

    def successful_save() -> Path:
        assert failure_entered.wait(timeout=1)
        success_started.set()
        return FileFeedbackCorpusRepository().save(sample, tmp_path)

    with ThreadPoolExecutor(max_workers=2) as executor:
        failed = executor.submit(failing_save)
        succeeded = executor.submit(successful_save)
        with pytest.raises(OSError, match="manifest read failed"):
            failed.result()
        output = succeeded.result()

    assert output.is_file()
    records = [
        json.loads(line)
        for line in (tmp_path / "manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert records == [
        {
            "id": "same",
            "audio": output.name,
            "reference": "same",
            "required_terms": [],
        }
    ]
    assert all((tmp_path / record["audio"]).is_file() for record in records)
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_invalid_sample_rate_preserves_buffer_and_filesystem_atomicity(tmp_path: Path) -> None:
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 0)

    with pytest.raises((ValueError, wave.Error)):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert buffer.get("sample") is not None
    assert not (tmp_path / "sample.wav").exists()
    assert not (tmp_path / "manifest.jsonl").exists()
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_invalid_required_term_metadata_rolls_back_wav_and_preserves_buffer(
    tmp_path: Path,
) -> None:
    buffer = RecentAudioBuffer(repository=FileFeedbackCorpusRepository())
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    with pytest.raises(TypeError):
        buffer.save_confirmed(
            "sample",
            tmp_path,
            reference="sample",
            required_terms=(object(),),  # type: ignore[arg-type]
            confirmed=True,
        )

    assert buffer.get("sample") is not None
    assert not (tmp_path / "sample.wav").exists()
    assert not (tmp_path / "manifest.jsonl").exists()
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_invalid_audio_fails_without_publishing_corpus_files(tmp_path: Path) -> None:
    sample = FeedbackSample(
        "sample",
        np.array(["not-a-number"]),
        16000,
        "sample",
        (),
    )

    with pytest.raises((TypeError, ValueError)):
        FileFeedbackCorpusRepository().save(sample, tmp_path)

    assert not (tmp_path / "sample.wav").exists()
    assert not (tmp_path / "manifest.jsonl").exists()
    assert not list(tmp_path.glob(".voicebud-audio-*"))
    assert not list(tmp_path.glob(".manifest-*"))


def test_capacity_expiration_and_clear_zeroize_buffer_owned_arrays() -> None:
    now = [0.0]
    buffer = RecentAudioBuffer(10, capacity=2, clock=lambda: now[0])
    buffer.store("one", np.array([1], dtype=np.float32), 16000)
    one = buffer._entries["one"][1]
    buffer.store("two", np.array([2], dtype=np.float32), 16000)
    two = buffer._entries["two"][1]
    buffer.store("three", np.array([3], dtype=np.float32), 16000)
    three = buffer._entries["three"][1]

    assert one.tolist() == [0]
    now[0] = 10
    assert buffer.get("two") is None
    assert two.tolist() == [0]
    assert three.tolist() == [0]

    now[0] = 20
    buffer.store("four", np.array([4], dtype=np.float32), 16000)
    four = buffer._entries["four"][1]
    buffer.clear()
    assert four.tolist() == [0]
