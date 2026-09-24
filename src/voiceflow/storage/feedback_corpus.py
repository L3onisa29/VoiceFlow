from __future__ import annotations

import ctypes
import errno
import json
import logging
import os
import stat
import sys
import tempfile
import threading
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar, Protocol, runtime_checkable
from uuid import uuid4

import numpy as np

from voiceflow.storage.permissions import (
    ensure_private_directory,
    read_managed_text_file,
    replace_private_file,
    restrict_file_permissions,
    restrict_open_file,
)

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FeedbackSample:
    identifier: str
    audio: np.ndarray
    sample_rate: int
    reference: str
    required_terms: tuple[str, ...]


@runtime_checkable
class FeedbackCorpusRepository(Protocol):
    def save(self, sample: FeedbackSample, directory: Path) -> Path: ...


_FileIdentity = tuple[int, int]


@dataclass(frozen=True, slots=True)
class _ManifestSnapshot:
    exists: bool
    contents: bytes
    mode: int | None
    identity: _FileIdentity | None


@dataclass(slots=True)
class _ReplacementState:
    destination: Path
    identity: _FileIdentity | None = None
    published: bool = False
    contents: bytes | None = None
    rollback_identity: _FileIdentity | None = None


class _ReconciliationFailure(OSError):
    def __init__(
        self,
        stage: str,
        path: Path,
        *,
        manifest_restored: bool = False,
        owned_path_restored: bool = False,
    ) -> None:
        super().__init__(
            errno.EIO,
            f"feedback corpus reconciliation failed during {stage}",
            path,
        )
        self.manifest_restored = manifest_restored
        self.owned_path_restored = owned_path_restored


class FileFeedbackCorpusRepository:
    """Persist confirmed feedback as a WAV plus a synchronized JSONL record.

    Repository instances in this process share one transaction lock per absolute corpus
    directory. The application has a single process-local feedback corpus owner, so this
    scope coordinates every supported writer without imposing a filesystem lock protocol.
    """

    _lock_guard: ClassVar[threading.Lock] = threading.Lock()
    _directory_locks: ClassVar[dict[Path, threading.Lock]] = {}
    _TRANSACTION_PREFIXES: ClassVar[tuple[str, ...]] = (
        ".manifest-",
        ".voiceflow-rollback-",
    )

    @classmethod
    def cleanup_stale_transactions(cls, directory: Path) -> int:
        """Remove only abandoned private transaction files from a prior process."""
        directory = directory.expanduser()
        try:
            directory_state = directory.lstat()
        except FileNotFoundError:
            return 0
        if stat.S_ISLNK(directory_state.st_mode) or not stat.S_ISDIR(directory_state.st_mode):
            raise OSError(errno.ENOTDIR, "feedback corpus path is not a directory", directory)

        removed = 0
        lock = cls._directory_lock(directory)
        with lock:
            for path in directory.iterdir():
                if not cls._is_transaction_name(path.name):
                    continue
                try:
                    state = path.lstat()
                    if not stat.S_ISREG(state.st_mode) or state.st_uid != os.getuid():
                        continue
                    identity = state.st_dev, state.st_ino
                    cls._remove_owned_path(path, identity)
                except FileNotFoundError:
                    continue
                except Exception:
                    LOGGER.warning(
                        "Could not remove stale feedback transaction file %s",
                        path,
                        exc_info=True,
                    )
                    continue
                removed += 1
        return removed

    @classmethod
    def _is_transaction_name(cls, name: str) -> bool:
        return (
            name.startswith(cls._TRANSACTION_PREFIXES)
            or (name.startswith(".voicebud-audio-") and name.endswith(".wav"))
        )

    def save(self, sample: FeedbackSample, directory: Path) -> Path:
        self._validate_identifier(sample.identifier, directory)
        lock = self._directory_lock(directory)
        with lock:
            output, occupied_outputs = self._select_output(sample.identifier, directory)
            manifest_record = self._serialize_manifest_record(sample, output.name)
            ensure_private_directory(directory)
            manifest = directory / "manifest.jsonl"
            manifest_snapshot = self._snapshot_manifest(manifest)
            for occupied_output in occupied_outputs:
                restrict_file_permissions(occupied_output)
            if restrict_file_permissions(output):
                raise FileExistsError(
                    errno.EEXIST,
                    "feedback output appeared after selection",
                    output,
                )
            manifest_replacement = _ReplacementState(
                manifest,
                contents=manifest_snapshot.contents + manifest_record,
            )
            wav_replacement = _ReplacementState(output)
            try:
                self._write_wav(sample, output, directory, wav_replacement)
            except Exception as error:
                try:
                    self._remove_transaction_file(wav_replacement, stage="WAV rollback")
                except _ReconciliationFailure as reconciliation:
                    raise reconciliation from error
                raise

            try:
                self._append_manifest(
                    manifest,
                    manifest_replacement.contents,
                    replacement=manifest_replacement,
                )
            except Exception as error:
                try:
                    self._restore_manifest(manifest_snapshot, manifest_replacement)
                except _ReconciliationFailure as reconciliation:
                    if self._transaction_is_committed(
                        manifest_replacement,
                        wav_replacement,
                    ):
                        return output
                    if reconciliation.manifest_restored:
                        try:
                            self._remove_transaction_file(
                                wav_replacement,
                                stage="WAV rollback after manifest recovery failure",
                            )
                        except _ReconciliationFailure as wav_reconciliation:
                            if (
                                wav_reconciliation.owned_path_restored
                                and self._finish_commit_after_failed_wav_rollback(
                                    manifest_snapshot,
                                    manifest_replacement,
                                    wav_replacement,
                                )
                            ):
                                return output
                            raise wav_reconciliation from error
                    raise reconciliation from error
                try:
                    self._remove_transaction_file(
                        wav_replacement,
                        stage="WAV rollback after manifest failure",
                    )
                except _ReconciliationFailure as reconciliation:
                    if (
                        reconciliation.owned_path_restored
                        and self._finish_commit_after_failed_wav_rollback(
                            manifest_snapshot,
                            manifest_replacement,
                            wav_replacement,
                        )
                    ):
                        return output
                    raise reconciliation from error
                raise
            return output

    @staticmethod
    def _serialize_manifest_record(sample: FeedbackSample, audio_name: str) -> bytes:
        return (
            json.dumps(
                {
                    "id": sample.identifier,
                    "audio": audio_name,
                    "reference": sample.reference,
                    "required_terms": sample.required_terms,
                },
                ensure_ascii=False,
            )
            + "\n"
        ).encode("utf-8")

    @staticmethod
    def _validate_identifier(identifier: str, directory: Path) -> None:
        separators = {"/", "\\", os.sep, os.altsep}
        separators.discard(None)
        filename = f"{identifier}.wav"
        directory_absolute = Path(os.path.abspath(os.fspath(directory.expanduser())))
        output_absolute = Path(os.path.abspath(os.fspath(directory.expanduser() / filename)))
        if (
            not isinstance(identifier, str)
            or not identifier
            or identifier in {".", ".."}
            or "\0" in identifier
            or any(separator in identifier for separator in separators)
            or os.path.isabs(identifier)
            or os.path.normpath(identifier) != identifier
            or output_absolute.parent != directory_absolute
        ):
            raise ValueError("feedback identifier must be a single safe filename component")

    @classmethod
    def _directory_lock(cls, directory: Path) -> threading.Lock:
        key = Path(os.path.abspath(os.fspath(directory.expanduser())))
        with FileFeedbackCorpusRepository._lock_guard:
            return FileFeedbackCorpusRepository._directory_locks.setdefault(
                key,
                threading.Lock(),
            )

    @staticmethod
    def _select_output(identifier: str, directory: Path) -> tuple[Path, tuple[Path, ...]]:
        output = directory / f"{identifier}.wav"
        occupied: list[Path] = []
        if not FileFeedbackCorpusRepository._output_exists(output):
            return output, ()
        occupied.append(output)
        while True:
            output = directory / f"{identifier}-{uuid4()}.wav"
            if not FileFeedbackCorpusRepository._output_exists(output):
                return output, tuple(occupied)
            occupied.append(output)

    @staticmethod
    def _output_exists(path: Path) -> bool:
        try:
            state = path.lstat()
        except FileNotFoundError:
            return False
        if stat.S_ISLNK(state.st_mode):
            raise OSError(errno.ELOOP, "refusing to follow a managed-path symlink", path)
        if not stat.S_ISREG(state.st_mode):
            raise OSError(errno.EINVAL, "managed file is not a regular file", path)
        return True

    @staticmethod
    def _write_wav(
        sample: FeedbackSample,
        output: Path,
        directory: Path,
        replacement: _ReplacementState,
    ) -> None:
        pcm = (np.clip(sample.audio, -1, 1) * np.iinfo(np.int16).max).astype(np.int16).tobytes()
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".voicebud-audio-",
            suffix=".wav",
            dir=directory,
        )
        temporary = Path(temporary_name)
        try:
            try:
                replacement.identity = FileFeedbackCorpusRepository._descriptor_identity(descriptor)
                restrict_open_file(descriptor)
                with (
                    os.fdopen(descriptor, "w+b", closefd=False) as temporary_file,
                    wave.open(temporary_file, "wb") as wav,
                ):
                    wav.setnchannels(1)
                    wav.setsampwidth(2)
                    wav.setframerate(sample.sample_rate)
                    wav.writeframes(pcm)
            finally:
                os.close(descriptor)
            FileFeedbackCorpusRepository._replace_tracked(temporary, replacement)
        finally:
            if not replacement.published and replacement.identity is not None:
                FileFeedbackCorpusRepository._remove_owned_path(
                    temporary,
                    replacement.identity,
                )

    @staticmethod
    def _append_manifest(
        path: Path,
        contents: bytes | None,
        *,
        replacement: _ReplacementState,
    ) -> None:
        assert contents is not None
        descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            try:
                replacement.identity = FileFeedbackCorpusRepository._descriptor_identity(descriptor)
                restrict_open_file(descriptor)
                with os.fdopen(descriptor, "wb", closefd=False) as temporary_file:
                    temporary_file.write(contents)
            finally:
                os.close(descriptor)
            FileFeedbackCorpusRepository._replace_tracked(temporary, replacement)
        finally:
            if not replacement.published and replacement.identity is not None:
                FileFeedbackCorpusRepository._remove_owned_path(
                    temporary,
                    replacement.identity,
                )

    @staticmethod
    def _descriptor_identity(descriptor: int) -> _FileIdentity:
        state = os.fstat(descriptor)
        return state.st_dev, state.st_ino

    @staticmethod
    def _path_identity(path: Path) -> _FileIdentity | None:
        try:
            state = path.lstat()
        except FileNotFoundError:
            return None
        if not stat.S_ISREG(state.st_mode):
            raise OSError(
                errno.ESTALE,
                "managed transaction destination is not a regular file",
                path,
            )
        return state.st_dev, state.st_ino

    @classmethod
    def _reconciled_path_identity(cls, path: Path) -> _FileIdentity | None:
        """Observe a path again, then independently through a no-follow descriptor."""

        path_error: OSError | None = None
        for _attempt in range(2):
            try:
                return cls._path_identity(path)
            except OSError as error:
                path_error = error

        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except FileNotFoundError:
            return None
        except OSError as error:
            error.add_note(f"path identity retries also failed: {path_error!r}")
            raise error from path_error
        try:
            state = os.fstat(descriptor)
            if not stat.S_ISREG(state.st_mode):
                raise OSError(
                    errno.ESTALE,
                    "managed transaction destination is not a regular file",
                    path,
                )
            return state.st_dev, state.st_ino
        finally:
            os.close(descriptor)

    @classmethod
    def _restored_manifest_matches(
        cls,
        path: Path,
        identity: _FileIdentity,
        contents: bytes,
    ) -> bool:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
        )
        try:
            state = os.fstat(descriptor)
            if not stat.S_ISREG(state.st_mode) or (state.st_dev, state.st_ino) != identity:
                return False
            with os.fdopen(descriptor, "rb", closefd=False) as manifest_file:
                return manifest_file.read() == contents
        finally:
            os.close(descriptor)

    @staticmethod
    def _native_rename(
        source: Path,
        destination: Path,
        *,
        darwin_flags: int,
        linux_flags: int,
    ) -> None:
        library = ctypes.CDLL(None, use_errno=True)
        source_bytes = os.fsencode(source)
        destination_bytes = os.fsencode(destination)
        if sys.platform == "darwin":
            rename = library.renamex_np
            rename.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
            rename.restype = ctypes.c_int
            result = rename(source_bytes, destination_bytes, darwin_flags)
        elif sys.platform.startswith("linux") and hasattr(library, "renameat2"):
            rename = library.renameat2
            rename.argtypes = [
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_uint,
            ]
            rename.restype = ctypes.c_int
            result = rename(-100, source_bytes, -100, destination_bytes, linux_flags)
        else:
            raise OSError(
                errno.ENOTSUP,
                "required atomic rename operation is unavailable",
                source,
            )
        if result != 0:
            error_number = ctypes.get_errno()
            raise OSError(error_number, os.strerror(error_number), source, destination)

    @staticmethod
    def _rename_no_replace(source: Path, destination: Path) -> None:
        """Atomically move one directory entry without replacing another."""

        FileFeedbackCorpusRepository._native_rename(
            source,
            destination,
            darwin_flags=0x00000004,
            linux_flags=0x00000001,
        )

    @staticmethod
    def _rename_exchange(first: Path, second: Path) -> None:
        """Atomically exchange two entries while recovering an unexpected capture."""

        FileFeedbackCorpusRepository._native_rename(
            first,
            second,
            darwin_flags=0x00000002,
            linux_flags=0x00000002,
        )

    @classmethod
    def _replace_tracked(cls, source: Path, replacement: _ReplacementState) -> None:
        assert replacement.identity is not None
        try:
            replace_private_file(source, replacement.destination)
        except Exception:
            replacement.published = (
                cls._reconciled_path_identity(replacement.destination) == replacement.identity
            )
            raise
        replacement.published = (
            cls._reconciled_path_identity(replacement.destination) == replacement.identity
        )
        if not replacement.published:
            raise OSError(
                errno.ESTALE,
                "private replacement returned without publishing the transaction file",
                replacement.destination,
            )

    @classmethod
    def _remove_owned_path(cls, path: Path, identity: _FileIdentity) -> None:
        current = cls._reconciled_path_identity(path)
        if current is None:
            return
        if current != identity:
            raise _ReconciliationFailure("owned-file identity check", path)

        quarantine: Path | None = None
        for _attempt in range(8):
            candidate = path.parent / f".voiceflow-rollback-{uuid4()}"
            try:
                cls._rename_no_replace(path, candidate)
            except OSError as error:
                if error.errno == errno.EEXIST:
                    continue
                try:
                    source_identity = cls._reconciled_path_identity(path)
                    candidate_identity = cls._reconciled_path_identity(candidate)
                except OSError as observation_error:
                    raise _ReconciliationFailure(
                        "owned-file quarantine publication ambiguity",
                        path,
                    ) from observation_error
                if candidate_identity is not None and source_identity is None:
                    quarantine = candidate
                    break
                if source_identity is None:
                    return
                raise _ReconciliationFailure("owned-file quarantine publication", path) from error
            else:
                quarantine = candidate
                break
        if quarantine is None:
            raise _ReconciliationFailure("owned-file quarantine collision", path)

        captured = cls._reconciled_path_identity(quarantine)
        if captured != identity:
            try:
                cls._rename_no_replace(quarantine, path)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    restored = cls._reconciled_path_identity(path)
                    if restored != captured:
                        raise _ReconciliationFailure(
                            "unowned-file quarantine restoration",
                            path,
                        ) from error
                else:
                    try:
                        cls._rename_exchange(quarantine, path)
                    except OSError as exchange_error:
                        raise _ReconciliationFailure(
                            "unowned-file quarantine restoration collision",
                            path,
                        ) from exchange_error
                    if cls._reconciled_path_identity(path) != captured:
                        raise _ReconciliationFailure(
                            "unowned-file quarantine restoration ambiguity",
                            path,
                        ) from error
                    raise _ReconciliationFailure(
                        "unowned-file quarantine restoration collision",
                        path,
                    ) from error
            if cls._reconciled_path_identity(path) != captured:
                raise _ReconciliationFailure("unowned-file quarantine restoration", path)
            raise _ReconciliationFailure("owned-file identity check", path)

        removal_error: OSError | None = None
        for _attempt in range(3):
            try:
                quarantine.unlink()
            except OSError as error:
                removal_error = error
            remaining = cls._reconciled_path_identity(quarantine)
            if remaining is None:
                return
            if remaining != identity:
                raise _ReconciliationFailure("owned-file removal verification", quarantine)

        try:
            cls._rename_no_replace(quarantine, path)
        except OSError as error:
            try:
                restored = cls._reconciled_path_identity(path)
                remaining = cls._reconciled_path_identity(quarantine)
            except OSError as observation_error:
                raise _ReconciliationFailure(
                    "owned-file quarantine cleanup ambiguity",
                    path,
                ) from observation_error
            if remaining is None and restored is None:
                return
            if remaining is not None or restored != identity:
                raise _ReconciliationFailure("owned-file quarantine cleanup", path) from error

        if cls._reconciled_path_identity(path) != identity:
            raise _ReconciliationFailure("owned-file quarantine restoration", path)
        failure = _ReconciliationFailure(
            "owned-file quarantine cleanup",
            path,
            owned_path_restored=True,
        )
        if removal_error is not None:
            raise failure from removal_error
        raise failure

    @classmethod
    def _transaction_is_committed(
        cls,
        manifest: _ReplacementState,
        wav: _ReplacementState,
    ) -> bool:
        if manifest.identity is None or manifest.contents is None or wav.identity is None:
            return False
        try:
            wav_identity = cls._reconciled_path_identity(wav.destination)
        except OSError:
            return False
        if wav_identity != wav.identity:
            return False
        try:
            descriptor = os.open(
                manifest.destination,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
        except OSError:
            return False
        try:
            state = os.fstat(descriptor)
            if (
                not stat.S_ISREG(state.st_mode)
                or (state.st_dev, state.st_ino) != manifest.identity
                or stat.S_IMODE(state.st_mode) != 0o600
            ):
                return False
            with os.fdopen(descriptor, "rb", closefd=False) as manifest_file:
                return manifest_file.read() == manifest.contents
        finally:
            os.close(descriptor)

    @classmethod
    def _prepare_private_file(cls, descriptor: int, path: Path, contents: bytes) -> None:
        try:
            restrict_open_file(descriptor)
        except OSError as hardening_error:
            try:
                os.chmod(path, 0o600, follow_symlinks=False)
                if stat.S_IMODE(os.fstat(descriptor).st_mode) != 0o600:
                    raise OSError(errno.EPERM, "private file hardening did not take effect", path)
            except OSError as fallback_error:
                fallback_error.add_note(f"descriptor hardening also failed: {hardening_error!r}")
                raise fallback_error from hardening_error
        with os.fdopen(descriptor, "wb", closefd=False) as manifest_file:
            manifest_file.write(contents)
            manifest_file.flush()
        os.fsync(descriptor)

    @classmethod
    def _unlink_owned_temporary(cls, path: Path, identity: _FileIdentity) -> bool:
        for _attempt in range(3):
            try:
                os.unlink(path)
            except FileNotFoundError:
                return True
            except OSError:
                pass
            try:
                remaining = cls._reconciled_path_identity(path)
            except OSError:
                continue
            if remaining is None:
                return True
            if remaining != identity:
                return False
        return False

    @classmethod
    def _publish_new_manifest(
        cls,
        temporary: Path,
        path: Path,
        identity: _FileIdentity,
    ) -> bool:
        for _attempt in range(3):
            try:
                cls._rename_no_replace(temporary, path)
            except OSError:
                destination_identity = cls._reconciled_path_identity(path)
                source_identity = cls._reconciled_path_identity(temporary)
                if destination_identity == identity and source_identity is None:
                    return True
                if destination_identity is not None or source_identity != identity:
                    return False
            else:
                return cls._reconciled_path_identity(path) == identity

        try:
            os.link(temporary, path, follow_symlinks=False)
        except OSError:
            destination_identity = cls._reconciled_path_identity(path)
            source_identity = cls._reconciled_path_identity(temporary)
            if destination_identity != identity:
                return False
            if source_identity is None:
                return True
        if cls._reconciled_path_identity(path) != identity:
            return False
        cls._unlink_owned_temporary(temporary, identity)
        return True

    @classmethod
    def _publish_over_prior_manifest(
        cls,
        temporary: Path,
        path: Path,
        transaction_identity: _FileIdentity,
        prior_identity: _FileIdentity,
    ) -> tuple[bool, _FileIdentity | None]:
        try:
            cls._rename_exchange(temporary, path)
        except OSError:
            destination_identity = cls._reconciled_path_identity(path)
            captured_identity = cls._reconciled_path_identity(temporary)
            if destination_identity != transaction_identity:
                return False, None
        else:
            destination_identity = cls._reconciled_path_identity(path)
            captured_identity = cls._reconciled_path_identity(temporary)

        if destination_identity != transaction_identity or captured_identity is None:
            return False, None
        if captured_identity == prior_identity:
            return True, captured_identity

        try:
            cls._rename_exchange(temporary, path)
        except OSError as error:
            if (
                cls._reconciled_path_identity(path) != captured_identity
                or cls._reconciled_path_identity(temporary) != transaction_identity
            ):
                raise _ReconciliationFailure(
                    "superseding manifest restoration",
                    path,
                ) from error
        return False, None

    @classmethod
    def _finish_commit_after_failed_wav_rollback(
        cls,
        snapshot: _ManifestSnapshot,
        manifest: _ReplacementState,
        wav: _ReplacementState,
    ) -> bool:
        if manifest.contents is None or wav.identity is None:
            return False
        try:
            if cls._reconciled_path_identity(wav.destination) != wav.identity:
                return False
        except OSError:
            return False

        path = manifest.destination
        if snapshot.exists:
            if manifest.rollback_identity is None:
                return False
            try:
                descriptor = os.open(
                    path,
                    os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
                )
            except OSError:
                return False
            try:
                state = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(state.st_mode)
                    or (state.st_dev, state.st_ino) != manifest.rollback_identity
                    or stat.S_IMODE(state.st_mode) != snapshot.mode
                ):
                    return False
                with os.fdopen(descriptor, "rb", closefd=False) as manifest_file:
                    if manifest_file.read() != snapshot.contents:
                        return False
            finally:
                os.close(descriptor)

            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".manifest-commit-",
                dir=path.parent,
            )
            temporary = Path(temporary_name)
            identity = cls._descriptor_identity(descriptor)
            published = False
            captured_identity: _FileIdentity | None = None
            try:
                try:
                    cls._prepare_private_file(descriptor, temporary, manifest.contents)
                finally:
                    os.close(descriptor)
                published, captured_identity = cls._publish_over_prior_manifest(
                    temporary,
                    path,
                    identity,
                    manifest.rollback_identity,
                )
                if not published:
                    return False
                manifest.identity = identity
                committed = cls._transaction_is_committed(manifest, wav)
                if not committed:
                    return False
                assert captured_identity is not None
                cls._unlink_owned_temporary(temporary, captured_identity)
                return committed
            finally:
                if not published:
                    cls._unlink_owned_temporary(temporary, identity)

        try:
            if cls._reconciled_path_identity(path) is not None:
                return False
        except OSError:
            return False
        descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-commit-", dir=path.parent)
        temporary = Path(temporary_name)
        published = False
        identity = cls._descriptor_identity(descriptor)
        try:
            try:
                cls._prepare_private_file(descriptor, temporary, manifest.contents)
            finally:
                os.close(descriptor)
            published = cls._publish_new_manifest(temporary, path, identity)
            if not published:
                return False
            manifest.identity = identity
            return cls._transaction_is_committed(manifest, wav)
        finally:
            if not published:
                cls._unlink_owned_temporary(temporary, identity)

    @classmethod
    def _remove_transaction_file(
        cls,
        replacement: _ReplacementState,
        *,
        stage: str,
    ) -> None:
        if replacement.identity is None:
            return
        try:
            cls._remove_owned_path(replacement.destination, replacement.identity)
        except _ReconciliationFailure:
            raise
        except Exception as error:
            raise _ReconciliationFailure(stage, replacement.destination) from error

    @classmethod
    def _snapshot_manifest(cls, path: Path) -> _ManifestSnapshot:
        try:
            before = path.lstat()
        except FileNotFoundError:
            contents = read_managed_text_file(path).encode("utf-8")
            if cls._path_identity(path) is not None:
                raise OSError(
                    errno.ESTALE,
                    "manifest appeared while capturing prior state",
                    path,
                ) from None
            return _ManifestSnapshot(False, contents, None, None)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "manifest is not a regular file", path)
        contents = read_managed_text_file(path).encode("utf-8")
        after = path.lstat()
        before_identity = before.st_dev, before.st_ino
        after_identity = after.st_dev, after.st_ino
        if before_identity != after_identity or before.st_mode != after.st_mode:
            raise OSError(errno.ESTALE, "manifest changed while capturing prior state", path)
        return _ManifestSnapshot(
            True,
            contents,
            stat.S_IMODE(before.st_mode),
            before_identity,
        )

    @classmethod
    def _restore_manifest(
        cls,
        snapshot: _ManifestSnapshot,
        replacement: _ReplacementState,
    ) -> None:
        path = replacement.destination
        current = cls._reconciled_path_identity(path)
        if not snapshot.exists:
            if current is None:
                return
            if current != replacement.identity:
                raise _ReconciliationFailure("manifest ownership check", path)
            try:
                assert replacement.identity is not None
                cls._remove_owned_path(path, replacement.identity)
            except Exception as error:
                raise _ReconciliationFailure("new manifest removal", path) from error
            return

        if current == snapshot.identity:
            return
        if current != replacement.identity:
            raise _ReconciliationFailure("manifest publication identity check", path)

        restore_replacement = _ReplacementState(path)
        descriptor, temporary_name = tempfile.mkstemp(prefix=".manifest-restore-", dir=path.parent)
        temporary = Path(temporary_name)
        try:
            try:
                restore_replacement.identity = cls._descriptor_identity(descriptor)
                restrict_open_file(descriptor)
                with os.fdopen(descriptor, "wb", closefd=False) as temporary_file:
                    temporary_file.write(snapshot.contents)
            finally:
                os.close(descriptor)
            try:
                cls._replace_tracked(temporary, restore_replacement)
            except Exception as error:
                if not restore_replacement.published:
                    raise _ReconciliationFailure(
                        "manifest restoration publication",
                        path,
                    ) from error
        finally:
            if not restore_replacement.published and restore_replacement.identity is not None:
                cls._remove_owned_path(temporary, restore_replacement.identity)

        assert restore_replacement.identity is not None
        replacement.rollback_identity = restore_replacement.identity
        try:
            descriptor = os.open(
                path,
                os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0),
            )
            try:
                if cls._descriptor_identity(descriptor) != restore_replacement.identity:
                    raise OSError(errno.ESTALE, "restored manifest identity changed", path)
                assert snapshot.mode is not None
                os.fchmod(descriptor, snapshot.mode)
            finally:
                os.close(descriptor)
            restored = cls._snapshot_manifest(path)
            if restored.contents != snapshot.contents or restored.mode != snapshot.mode:
                raise OSError(errno.ESTALE, "restored manifest state does not match", path)
        except Exception as error:
            try:
                manifest_restored = cls._restored_manifest_matches(
                    path,
                    restore_replacement.identity,
                    snapshot.contents,
                )
            except Exception as confirmation_error:
                confirmation_error.add_note(f"original restoration verification failure: {error!r}")
                raise _ReconciliationFailure(
                    "manifest restoration verification unknown",
                    path,
                ) from confirmation_error
            raise _ReconciliationFailure(
                "manifest restoration verification",
                path,
                manifest_restored=manifest_restored,
            ) from error
