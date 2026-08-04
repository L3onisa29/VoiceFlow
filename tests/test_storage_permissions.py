from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

import voiceflow.storage.permissions as permissions
from voiceflow.config import HistoryConfig
from voiceflow.storage.database import VoiceBudStorage
from voiceflow.storage.permissions import (
    ensure_private_directory,
    ensure_private_file,
    read_managed_text_file,
    replace_private_file,
    restrict_file_permissions,
    restrict_open_file,
)


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_private_directory_creates_private_managed_components(tmp_path: Path) -> None:
    target = tmp_path / "one" / "two"

    ensure_private_directory(target)

    assert mode(target.parent) == 0o700
    assert mode(target) == 0o700

    # Custom pre-existing directories belong to the user, so their mode is preserved.
    target.chmod(0o755)
    ensure_private_directory(target)
    assert mode(target) == 0o755


def test_private_directory_rejects_symlinks_and_regular_files(tmp_path: Path) -> None:
    external = tmp_path / "external"
    external.mkdir()
    linked = tmp_path / "linked"
    linked.symlink_to(external, target_is_directory=True)
    regular = tmp_path / "regular"
    regular.write_text("content", encoding="utf-8")

    with pytest.raises(OSError):
        ensure_private_directory(linked / "nested")
    with pytest.raises(NotADirectoryError):
        ensure_private_directory(regular / "nested")


def test_private_directory_preserves_concurrent_creator_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "concurrent"
    real_mkdir = os.mkdir

    def create_then_report_exists(
        path: str | bytes | os.PathLike[str],
        mode_value: int,
        *,
        dir_fd: int | None = None,
    ) -> None:
        assert path == target.name
        assert dir_fd is not None
        real_mkdir(path, 0o755, dir_fd=dir_fd)
        os.chmod(target, 0o755)
        raise FileExistsError(errno.EEXIST, "concurrently created", path)

    monkeypatch.setattr(os, "mkdir", create_then_report_exists)

    ensure_private_directory(target)

    assert mode(target) == 0o755


@pytest.mark.parametrize("concurrent_kind", ["file", "symlink"])
def test_private_directory_rejects_invalid_concurrent_creator(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    concurrent_kind: str,
) -> None:
    target = tmp_path / "concurrent"
    external = tmp_path / "external"
    external.mkdir()

    def create_invalid_then_report_exists(
        path: str | bytes | os.PathLike[str],
        mode_value: int,
        *,
        dir_fd: int | None = None,
    ) -> None:
        assert path == target.name
        assert dir_fd is not None
        if concurrent_kind == "file":
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT,
                0o600,
                dir_fd=dir_fd,
            )
            os.close(descriptor)
        else:
            os.symlink(external, path, target_is_directory=True, dir_fd=dir_fd)
        raise FileExistsError(errno.EEXIST, "concurrently created", path)

    monkeypatch.setattr(os, "mkdir", create_invalid_then_report_exists)

    expected = NotADirectoryError if concurrent_kind == "file" else OSError
    with pytest.raises(expected):
        ensure_private_directory(target)


def test_private_directory_created_by_app_remains_private(tmp_path: Path) -> None:
    target = tmp_path / "created-by-app"

    ensure_private_directory(target)

    assert mode(target) == 0o700


@pytest.mark.parametrize("restrictive_umask", [0o100, 0o200, 0o300])
def test_private_directory_fails_when_umask_removes_owner_permissions(
    tmp_path: Path,
    restrictive_umask: int,
) -> None:
    target = tmp_path / f"umask-{restrictive_umask:o}"
    previous_umask = os.umask(restrictive_umask)
    try:
        with pytest.raises(PermissionError, match="does not have mode 0700"):
            ensure_private_directory(target)
    finally:
        os.umask(previous_umask)

    assert mode(target) == 0o700 & ~restrictive_umask


def test_private_directory_succeeds_with_normal_umask(tmp_path: Path) -> None:
    target = tmp_path / "normal-umask"
    previous_umask = os.umask(0o022)
    try:
        ensure_private_directory(target)
    finally:
        os.umask(previous_umask)

    assert mode(target) == 0o700


@pytest.mark.parametrize("replacement_kind", ["directory", "file", "symlink"])
def test_private_directory_rejects_replacement_before_first_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    target = tmp_path / "replaced-before-validation"
    parked = tmp_path / "parked-before-validation"
    external = tmp_path / "external-before-validation"
    external.mkdir()
    real_mkdir = os.mkdir

    def create_then_replace(
        path: str | bytes | os.PathLike[str],
        mode_value: int,
        *,
        dir_fd: int | None = None,
    ) -> None:
        assert path == target.name
        assert dir_fd is not None
        real_mkdir(path, mode_value, dir_fd=dir_fd)
        target.rename(parked)
        if replacement_kind == "directory":
            real_mkdir(path, 0o755, dir_fd=dir_fd)
            target.chmod(0o755)
        elif replacement_kind == "file":
            descriptor = os.open(
                path,
                os.O_WRONLY | os.O_CREAT,
                0o600,
                dir_fd=dir_fd,
            )
            os.close(descriptor)
        else:
            os.symlink(external, path, target_is_directory=True, dir_fd=dir_fd)

    monkeypatch.setattr(os, "mkdir", create_then_replace)

    with pytest.raises(OSError):
        ensure_private_directory(target)

    if replacement_kind == "directory":
        assert mode(target) == 0o755
    elif replacement_kind == "file":
        assert target.read_text(encoding="utf-8") == ""
    else:
        assert target.is_symlink()


def test_private_directory_rechecks_created_mode_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created-mode-changed-before-final-check"
    real_open_component = permissions._open_directory_component

    def open_then_change_mode(
        parent_descriptor: int,
        name: str,
        path: Path,
        expected: os.stat_result,
    ) -> int:
        descriptor = real_open_component(parent_descriptor, name, path, expected)
        if path == target:
            target.chmod(0o755)
        return descriptor

    monkeypatch.setattr(permissions, "_open_directory_component", open_then_change_mode)

    with pytest.raises(PermissionError, match="does not have mode 0700"):
        ensure_private_directory(target)

    assert mode(target) == 0o755


@pytest.mark.parametrize("replacement_kind", ["directory", "file", "symlink"])
def test_private_directory_rejects_replacement_after_concurrent_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    target = tmp_path / "concurrent-replaced"
    parked = tmp_path / "parked-concurrent"
    external = tmp_path / "external-concurrent"
    external.mkdir()
    real_mkdir = os.mkdir
    real_validate = permissions._validate_directory
    concurrent = False
    replaced = False

    def create_then_report_exists(
        path: str | bytes | os.PathLike[str],
        mode_value: int,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal concurrent
        assert path == target.name
        assert dir_fd is not None
        real_mkdir(path, 0o755, dir_fd=dir_fd)
        os.chmod(target, 0o755)
        concurrent = True
        raise FileExistsError(errno.EEXIST, "concurrently created", path)

    def validate_then_replace(path: Path) -> os.stat_result:
        nonlocal replaced
        state = real_validate(path)
        if path == target and concurrent and not replaced:
            target.rename(parked)
            if replacement_kind == "directory":
                real_mkdir(target, 0o755)
                target.chmod(0o755)
            elif replacement_kind == "file":
                target.write_text("replacement", encoding="utf-8")
            else:
                target.symlink_to(external, target_is_directory=True)
            replaced = True
        return state

    monkeypatch.setattr(os, "mkdir", create_then_report_exists)
    monkeypatch.setattr(permissions, "_validate_directory", validate_then_replace)

    with pytest.raises(OSError):
        ensure_private_directory(target)

    assert replaced
    if replacement_kind == "directory":
        assert mode(target) == 0o755


@pytest.mark.parametrize("replacement_kind", ["directory", "file", "symlink"])
def test_private_directory_never_chmods_replacement_of_created_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    target = tmp_path / "created-replaced"
    parked = tmp_path / "parked-created"
    external = tmp_path / "external-created"
    external.mkdir()
    real_validate = permissions._validate_directory
    replaced = False

    def validate_then_replace(path: Path) -> os.stat_result:
        nonlocal replaced
        state = real_validate(path)
        if path == target and not replaced:
            target.rename(parked)
            if replacement_kind == "directory":
                target.mkdir(mode=0o755)
                target.chmod(0o755)
            elif replacement_kind == "file":
                target.write_text("replacement", encoding="utf-8")
            else:
                target.symlink_to(external, target_is_directory=True)
            replaced = True
        return state

    monkeypatch.setattr(permissions, "_validate_directory", validate_then_replace)

    with pytest.raises(OSError):
        ensure_private_directory(target)

    assert replaced
    if replacement_kind == "directory":
        assert mode(target) == 0o755


def test_private_directory_rejects_entry_changed_after_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "changed-after-open"
    target.mkdir()
    parked = tmp_path / "parked-after-open"
    real_open = os.open
    replaced = False

    def open_then_replace(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode_value: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal replaced
        descriptor = real_open(path, flags, mode_value, dir_fd=dir_fd)
        if path == target.name and dir_fd is not None and not replaced:
            target.rename(parked)
            target.mkdir(mode=0o755)
            target.chmod(0o755)
            replaced = True
        return descriptor

    monkeypatch.setattr(os, "open", open_then_replace)

    with pytest.raises(OSError, match="changed during validation"):
        ensure_private_directory(target)

    assert mode(target) == 0o755


@pytest.mark.parametrize("replacement_kind", ["directory", "file", "symlink"])
def test_private_directory_rechecks_open_entries_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement_kind: str,
) -> None:
    target = tmp_path / "changed-before-final-check"
    target.mkdir()
    parked = tmp_path / "parked-before-final-check"
    external = tmp_path / "external-before-final-check"
    external.mkdir()
    real_open_component = permissions._open_directory_component

    def open_then_replace(
        parent_descriptor: int,
        name: str,
        path: Path,
        expected: os.stat_result,
    ) -> int:
        descriptor = real_open_component(parent_descriptor, name, path, expected)
        if path == target:
            target.rename(parked)
            if replacement_kind == "directory":
                target.mkdir(mode=0o755)
            elif replacement_kind == "file":
                target.write_text("replacement", encoding="utf-8")
            else:
                target.symlink_to(external, target_is_directory=True)
        return descriptor

    monkeypatch.setattr(permissions, "_open_directory_component", open_then_replace)

    with pytest.raises(OSError):
        ensure_private_directory(target)


def test_private_directory_rechecks_absolute_identity_before_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "changed-before-absolute-check"
    target.mkdir()
    parked = tmp_path / "parked-before-absolute-check"
    real_validate = permissions._validate_directory
    target_validations = 0

    def replace_before_final_validation(path: Path) -> os.stat_result:
        nonlocal target_validations
        if path == target:
            target_validations += 1
            if target_validations == 2:
                target.rename(parked)
                target.mkdir(mode=0o755)
        return real_validate(path)

    monkeypatch.setattr(permissions, "_validate_directory", replace_before_final_validation)

    with pytest.raises(OSError, match="changed during validation"):
        ensure_private_directory(target)


def test_private_directory_rechecks_created_mode_during_final_absolute_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "created-mode-changed-during-absolute-check"
    real_validate = permissions._validate_directory
    target_validations = 0

    def change_mode_before_final_snapshot(path: Path) -> os.stat_result:
        nonlocal target_validations
        if path == target:
            target_validations += 1
            if target_validations == 3:
                target.chmod(0o755)
        return real_validate(path)

    monkeypatch.setattr(
        permissions,
        "_validate_directory",
        change_mode_before_final_snapshot,
    )

    with pytest.raises(PermissionError, match="does not have mode 0700"):
        ensure_private_directory(target)

    assert target_validations == 3
    assert mode(target) == 0o755


def test_private_directory_rejects_non_directory_fstat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "expected-directory"
    target.mkdir()
    regular = tmp_path / "regular"
    regular.write_text("content", encoding="utf-8")
    parent_descriptor = os.open(tmp_path, os.O_RDONLY)
    regular_descriptor = os.open(regular, os.O_RDONLY)

    def open_regular_instead(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode_value: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        return regular_descriptor

    monkeypatch.setattr(os, "open", open_regular_instead)
    try:
        with pytest.raises(NotADirectoryError):
            permissions._open_directory_component(
                parent_descriptor,
                target.name,
                target,
                target.stat(),
            )
    finally:
        os.close(parent_descriptor)


def test_private_directory_propagates_nofollow_open_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "nofollow-failure"
    target.mkdir()
    real_open = os.open

    def fail_component_open(
        path: str | bytes | os.PathLike[str],
        flags: int,
        mode_value: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if path == target.name and dir_fd is not None:
            raise OSError(errno.ELOOP, "refusing link", path)
        return real_open(path, flags, mode_value, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", fail_component_open)

    with pytest.raises(OSError, match="symlink"):
        ensure_private_directory(target)


def test_private_directory_propagates_non_eexist_mkdir_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "denied"

    def deny_mkdir(
        path: str | bytes | os.PathLike[str],
        mode_value: int,
        *,
        dir_fd: int | None = None,
    ) -> None:
        raise PermissionError(errno.EACCES, "denied", path)

    monkeypatch.setattr(os, "mkdir", deny_mkdir)

    with pytest.raises(PermissionError):
        ensure_private_directory(target)


def test_file_helpers_create_restrict_read_and_report_missing_files(tmp_path: Path) -> None:
    target = tmp_path / "managed.txt"

    ensure_private_file(target)
    target.write_text("private", encoding="utf-8")
    target.chmod(0o644)

    assert restrict_file_permissions(target)
    assert mode(target) == 0o600
    assert read_managed_text_file(target) == "private"
    assert not restrict_file_permissions(tmp_path / "missing.txt")
    assert read_managed_text_file(tmp_path / "missing.txt") == ""


def test_file_helpers_reject_symlinks_and_nonregular_files(tmp_path: Path) -> None:
    external = tmp_path / "external.txt"
    external.write_text("external", encoding="utf-8")
    linked = tmp_path / "linked.txt"
    linked.symlink_to(external)

    with pytest.raises(OSError):
        ensure_private_file(linked)
    with pytest.raises(OSError):
        read_managed_text_file(linked)
    with pytest.raises(OSError):
        restrict_file_permissions(tmp_path)

    assert external.read_text(encoding="utf-8") == "external"


def test_restrict_open_file_rejects_directory_descriptor(tmp_path: Path) -> None:
    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(OSError, match="regular file"):
            restrict_open_file(descriptor)
    finally:
        os.close(descriptor)


def test_replace_private_file_is_atomic_private_and_removes_source(tmp_path: Path) -> None:
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    source.write_text("new", encoding="utf-8")
    destination.write_text("old", encoding="utf-8")
    source.chmod(0o644)

    replace_private_file(source, destination)

    assert not source.exists()
    assert destination.read_text(encoding="utf-8") == "new"
    assert mode(destination) == 0o600


def test_replace_private_file_rejects_unsafe_targets(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.write_text("new", encoding="utf-8")
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    linked = tmp_path / "linked"
    linked.symlink_to(external)

    with pytest.raises(OSError):
        replace_private_file(source, linked)
    with pytest.raises(OSError):
        replace_private_file(source, source)
    with pytest.raises(OSError):
        replace_private_file(source, tmp_path)

    assert source.read_text(encoding="utf-8") == "new"
    assert external.read_text(encoding="utf-8") == "external"


def test_database_and_sidecars_remain_private(tmp_path: Path) -> None:
    path = tmp_path / "storage" / "voicebud.sqlite3"
    storage = VoiceBudStorage(path, history=HistoryConfig())
    storage.execute("CREATE TABLE private_test (value TEXT)")
    storage.execute("INSERT INTO private_test VALUES ('value')")

    assert mode(path.parent) == 0o700
    assert mode(path) == 0o600
    for suffix in ("-wal", "-shm"):
        sidecar = Path(f"{path}{suffix}")
        if sidecar.exists():
            assert mode(sidecar) == 0o600

    storage.close()
