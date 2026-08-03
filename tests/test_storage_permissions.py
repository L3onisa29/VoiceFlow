from __future__ import annotations

import errno
import os
import sqlite3
import threading
from pathlib import Path

import numpy as np
import pytest

from voiceflow.config import HistoryConfig
from voiceflow.feedback import RecentAudioBuffer
from voiceflow.storage import VoiceBudStorage


def _mode(path: Path) -> int:
    return os.stat(path).st_mode & 0o777


def test_each_created_directory_component_is_private(tmp_path: Path) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "first" / "second" / "third"
    previous_umask = os.umask(0)
    try:
        ensure_private_directory(target)
    finally:
        os.umask(previous_umask)

    assert [_mode(path) for path in (target.parent.parent, target.parent, target)] == [
        0o700,
        0o700,
        0o700,
    ]


@pytest.mark.parametrize("restrictive_umask", [0o200, 0o700, 0o777])
def test_each_created_directory_component_is_private_under_restrictive_umask(
    tmp_path: Path,
    restrictive_umask: int,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "first" / "second"
    previous_umask = os.umask(restrictive_umask)
    try:
        ensure_private_directory(target)
    finally:
        os.umask(previous_umask)

    assert _mode(target.parent) == 0o700
    assert _mode(target) == 0o700


def test_missing_directories_reports_the_missing_chain(tmp_path: Path) -> None:
    from voiceflow.storage.permissions import missing_directories

    target = tmp_path / "first" / "second"

    assert missing_directories(target) == (target.parent, target)


def test_directory_open_inode_substitution_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "target"
    target.mkdir()
    substitute = tmp_path / "substitute"
    substitute.mkdir()
    original_open = os.open
    substituted = False

    def substitute_opened_directory(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted
        if not substituted and os.fspath(path) == target.name:
            substituted = True
            return original_open(substitute.name, flags, mode, dir_fd=dir_fd)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", substitute_opened_directory)

    with pytest.raises(OSError):
        ensure_private_directory(target)

    assert substituted


def test_directory_entry_swap_after_open_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "target"
    target.mkdir()
    displaced = tmp_path / "displaced"
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    os.chmod(external, 0o755)
    original_open = os.open
    swapped = False

    def swap_entry_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and os.fspath(path) == target.name:
            target.rename(displaced)
            target.symlink_to(external, target_is_directory=True)
            swapped = True
        return descriptor

    monkeypatch.setattr(os, "open", swap_entry_after_open)

    with pytest.raises(OSError):
        ensure_private_directory(target)

    assert swapped
    assert target.is_symlink()
    assert _mode(external) == 0o755


def test_concurrent_directory_winner_is_not_chmodded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "winner"
    original_mkdir = os.mkdir
    creation_won = threading.Event()

    def concurrent_publish(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        assert destination_name == target.name
        original_mkdir(destination_name, mode=0o755, dir_fd=parent_descriptor)
        os.chmod(target, 0o755)
        creation_won.set()
        raise FileExistsError(errno.EEXIST, "concurrent winner", path)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", concurrent_publish)
    permissions.ensure_private_directory(target)

    assert creation_won.is_set()
    assert _mode(target) == 0o755


def test_directory_mkdir_substitution_never_chmods_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "winner"
    original_mkdir = os.mkdir
    substituted = False

    def substitute_immediately_after_mkdir(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal substituted
        original_mkdir(path, mode=mode, dir_fd=dir_fd)
        if substituted or dir_fd is None:
            return

        created_name = os.fsdecode(path)
        if created_name == target.name:
            os.rmdir(created_name, dir_fd=dir_fd)
        original_mkdir(target.name, mode=0o755, dir_fd=dir_fd)
        os.chmod(target, 0o755)
        substituted = True

    monkeypatch.setattr(os, "mkdir", substitute_immediately_after_mkdir)

    ensure_private_directory(target)

    assert substituted
    assert _mode(target) == 0o755
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


def test_directory_helpers_never_chmod_a_preexisting_directory_or_file_winner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    existing = tmp_path / "existing"
    existing.mkdir(mode=0o755)
    os.chmod(existing, 0o755)
    with pytest.raises(OSError):
        permissions.restrict_file_permissions(existing)
    assert _mode(existing) == 0o755

    target = tmp_path / "file-winner"

    def file_wins(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        target.write_text("winner", encoding="utf-8")
        os.chmod(target, 0o644)
        raise FileExistsError(errno.EEXIST, "file winner", path)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", file_wins)
    with pytest.raises(NotADirectoryError):
        permissions.ensure_private_directory(target)
    assert _mode(target) == 0o644


def test_directory_helper_rejects_symlink_components_and_file_targets(
    tmp_path: Path,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    os.chmod(external, 0o755)
    linked = tmp_path / "linked"
    linked.symlink_to(external, target_is_directory=True)

    with pytest.raises(OSError):
        ensure_private_directory(linked / "nested")

    assert not (external / "nested").exists()
    assert _mode(external) == 0o755

    regular = tmp_path / "regular"
    regular.write_text("not a directory", encoding="utf-8")
    os.chmod(regular, 0o644)
    with pytest.raises(NotADirectoryError):
        ensure_private_directory(regular)
    assert regular.read_text(encoding="utf-8") == "not a directory"
    assert _mode(regular) == 0o644

    dangling = tmp_path / "dangling-directory"
    dangling.symlink_to(tmp_path / "absent-directory", target_is_directory=True)
    with pytest.raises(OSError):
        ensure_private_directory(dangling / "nested")
    assert not (tmp_path / "absent-directory").exists()


def test_directory_creation_swap_cannot_chmod_or_traverse_external_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "swapped"
    displaced = tmp_path / "displaced"
    external = tmp_path / "external"
    external.mkdir(mode=0o755)
    os.chmod(external, 0o755)
    original_publish = permissions._rename_directory_exclusive

    def swap_published_directory(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        original_publish(parent_descriptor, source_name, destination_name, path)
        target.rename(displaced)
        target.symlink_to(external, target_is_directory=True)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", swap_published_directory)

    with pytest.raises(OSError):
        permissions.ensure_private_directory(target / "nested")

    assert _mode(external) == 0o755
    assert not (external / "nested").exists()
    assert _mode(displaced) == 0o700


def test_directory_collision_during_hardening_only_chmods_temporary_inode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_directory

    target = tmp_path / "target"
    original_fchmod = os.fchmod
    collided = False

    def collide_before_fchmod(descriptor: int, mode: int) -> None:
        nonlocal collided
        if not collided:
            target.mkdir(mode=0o755)
            os.chmod(target, 0o755)
            collided = True
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", collide_before_fchmod)

    ensure_private_directory(target)

    assert collided
    assert _mode(target) == 0o755


def test_directory_creation_exhausts_temporary_name_collisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"
    collision = tmp_path / ".voiceflow-directory-collision"
    collision.mkdir()
    monkeypatch.setattr(permissions.secrets, "token_hex", lambda _size: "collision")

    with pytest.raises(OSError) as raised:
        permissions.ensure_private_directory(target)

    assert raised.value.errno == errno.EAGAIN
    assert collision.is_dir()
    assert not target.exists()


def test_directory_publication_failure_cleans_owned_temporary_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"

    def fail_publication(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        raise OSError(errno.EIO, "injected publication failure", path)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", fail_publication)

    with pytest.raises(OSError, match="injected publication failure"):
        permissions.ensure_private_directory(target)

    assert not target.exists()
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


def test_directory_creation_restores_umask_after_publication_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"

    def fail_publication(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        raise OSError(errno.EIO, "injected publication failure", path)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", fail_publication)
    original_umask = os.umask(0o027)
    try:
        with pytest.raises(OSError):
            permissions.ensure_private_directory(target)
        restored_umask = os.umask(0o027)
        assert restored_umask == 0o027
    finally:
        os.umask(original_umask)


def test_directory_creation_fails_closed_without_no_replace_primitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"
    monkeypatch.setattr(permissions.sys, "platform", "unsupported")

    with pytest.raises(OSError) as raised:
        permissions.ensure_private_directory(target)

    assert raised.value.errno == errno.ENOTSUP
    assert not target.exists()
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


def test_directory_creation_fails_closed_when_darwin_rename_symbol_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"
    monkeypatch.setattr(permissions.sys, "platform", "darwin")
    monkeypatch.setattr(permissions.ctypes, "CDLL", lambda *_args, **_kwargs: object())

    with pytest.raises(OSError) as raised:
        permissions.ensure_private_directory(target)

    assert raised.value.errno == errno.ENOTSUP
    assert not target.exists()
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


def test_directory_publication_failure_after_move_fails_closed_without_temp_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"
    original_publish = permissions._rename_directory_exclusive

    def fail_after_move(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        original_publish(parent_descriptor, source_name, destination_name, path)
        raise OSError(errno.EIO, "ambiguous publication outcome", path)

    monkeypatch.setattr(permissions, "_rename_directory_exclusive", fail_after_move)

    with pytest.raises(OSError, match="ambiguous publication outcome"):
        permissions.ensure_private_directory(target)

    assert target.is_dir()
    assert _mode(target) == 0o700
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


@pytest.mark.parametrize("winner_kind", ["symlink", "file"])
def test_directory_publication_collision_rejects_unsafe_winner_without_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    winner_kind: str,
) -> None:
    from voiceflow.storage import permissions

    target = tmp_path / "target"
    external = tmp_path / "external"
    if winner_kind == "symlink":
        external.mkdir(mode=0o755)
        os.chmod(external, 0o755)

    def collide_with_unsafe_winner(
        parent_descriptor: int,
        source_name: str,
        destination_name: str,
        path: Path,
    ) -> None:
        if winner_kind == "symlink":
            target.symlink_to(external, target_is_directory=True)
        else:
            target.write_text("winner", encoding="utf-8")
            os.chmod(target, 0o644)
        raise FileExistsError(errno.EEXIST, "unsafe winner", path)

    monkeypatch.setattr(
        permissions,
        "_rename_directory_exclusive",
        collide_with_unsafe_winner,
    )

    with pytest.raises(OSError):
        permissions.ensure_private_directory(target)

    if winner_kind == "symlink":
        assert target.is_symlink()
        assert _mode(external) == 0o755
    else:
        assert target.read_text(encoding="utf-8") == "winner"
        assert _mode(target) == 0o644
    assert not list(tmp_path.glob(".voiceflow-directory-*"))


@pytest.mark.parametrize("helper_name", ["restrict_file_permissions", "ensure_private_file"])
def test_file_helpers_reject_existing_and_dangling_symlinks(
    tmp_path: Path,
    helper_name: str,
) -> None:
    from voiceflow.storage import permissions

    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    linked = tmp_path / f"{helper_name}-linked"
    linked.symlink_to(external)

    with pytest.raises(OSError):
        getattr(permissions, helper_name)(linked)

    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644

    absent = tmp_path / f"{helper_name}-absent"
    dangling = tmp_path / f"{helper_name}-dangling"
    dangling.symlink_to(absent)
    with pytest.raises(OSError):
        getattr(permissions, helper_name)(dangling)
    assert not absent.exists()


@pytest.mark.parametrize("helper_name", ["restrict_file_permissions", "ensure_private_file"])
def test_file_helper_detects_path_swap_without_chmodding_referent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper_name: str,
) -> None:
    from voiceflow.storage import permissions

    managed = tmp_path / "managed"
    managed.write_text("managed", encoding="utf-8")
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    displaced = tmp_path / "displaced"
    original_open = os.open
    swapped = False

    def swap_after_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal swapped
        descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and os.fspath(path) in {managed.name, os.fspath(managed)}:
            managed.rename(displaced)
            managed.symlink_to(external)
            swapped = True
        return descriptor

    monkeypatch.setattr(os, "open", swap_after_open)

    with pytest.raises(OSError):
        getattr(permissions, helper_name)(managed)

    assert swapped
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_file_creation_rejects_substituted_descriptor_before_hardening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_file

    target = tmp_path / "target"
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    original_open = os.open
    substituted = False

    def substitute_created_file(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted
        if not substituted and os.fspath(path) == target.name and flags & os.O_CREAT:
            substituted = True
            return original_open(external, os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", substitute_created_file)

    with pytest.raises(OSError):
        ensure_private_file(target)

    assert substituted
    assert not target.exists()
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_file_creation_retries_an_exclusive_create_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import ensure_private_file

    target = tmp_path / "target"
    original_open = os.open
    collided = False

    def create_concurrent_winner(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal collided
        if not collided and os.fspath(path) == target.name and flags & os.O_CREAT:
            collided = True
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
            os.fchmod(descriptor, 0o644)
            os.close(descriptor)
            raise FileExistsError(path)
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", create_concurrent_winner)

    ensure_private_file(target)

    assert collided
    assert target.is_file()
    assert _mode(target) == 0o600


def test_replace_helper_hardens_verified_inode_and_has_no_post_commit_chmod(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    os.chmod(source, 0o644)
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    os.chmod(destination, 0o644)
    original_restrict = permissions.restrict_file_permissions

    def fail_post_commit(path: Path) -> None:
        if path == destination:
            raise OSError("post-commit chmod failed")
        original_restrict(path)

    monkeypatch.setattr(permissions, "restrict_file_permissions", fail_post_commit)

    permissions.replace_private_file(source, destination)

    assert destination.read_text(encoding="utf-8") == "replacement"
    assert _mode(destination) == 0o600


def test_replace_helper_detects_source_swap_without_chmodding_referent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    displaced = tmp_path / "displaced"
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    original_fchmod = os.fchmod
    swapped = False

    def swap_before_fchmod(descriptor: int, mode: int) -> None:
        nonlocal swapped
        if not swapped:
            source.rename(displaced)
            source.symlink_to(external)
            swapped = True
        original_fchmod(descriptor, mode)

    monkeypatch.setattr(os, "fchmod", swap_before_fchmod)

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert swapped
    assert destination.read_text(encoding="utf-8") == "old"
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_replace_helper_rolls_back_source_path_swap_at_rename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    os.chmod(destination, 0o640)
    displaced = tmp_path / "displaced"
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    original_replace = os.replace
    swapped = False

    def swap_source_at_rename(
        source_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal swapped
        if not swapped and os.fspath(source_name) == source.name:
            source.rename(displaced)
            source.symlink_to(external)
            swapped = True
        original_replace(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", swap_source_at_rename)

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert swapped
    assert not destination.is_symlink()
    assert destination.read_text(encoding="utf-8") == "old"
    assert _mode(destination) == 0o640
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_replace_helper_removes_unsafe_swap_when_destination_was_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    displaced = tmp_path / "displaced"
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    original_replace = os.replace

    def swap_source_at_rename(
        source_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source.rename(displaced)
        source.symlink_to(external)
        original_replace(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", swap_source_at_rename)

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert not destination.exists()
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_replace_helper_rolls_back_post_rename_descriptor_substitution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    os.chmod(destination, 0o640)
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    original_open = os.open
    substituted = False

    def substitute_committed_open(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        nonlocal substituted
        if not substituted and not source.exists() and os.fspath(path) == destination.name:
            substituted = True
            return original_open(external, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        return original_open(path, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(os, "open", substitute_committed_open)

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert substituted
    assert destination.read_text(encoding="utf-8") == "old"
    assert _mode(destination) == 0o640
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_replace_helper_rejects_hard_linked_source_without_chmodding_inode(
    tmp_path: Path,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    source = tmp_path / "source"
    source.hardlink_to(external)
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert source.exists()
    assert destination.read_text(encoding="utf-8") == "old"
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_replace_helper_ignores_close_failure_after_verified_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    original_close = os.close
    failed = False

    def fail_post_commit_close(descriptor: int) -> None:
        nonlocal failed
        original_close(descriptor)
        if not failed and not source.exists():
            failed = True
            raise OSError("injected post-commit close failure")

    monkeypatch.setattr(os, "close", fail_post_commit_close)

    replace_private_file(source, destination)

    assert failed
    assert destination.read_text(encoding="utf-8") == "replacement"
    assert _mode(destination) == 0o600


def test_replace_helper_exhausted_backup_collisions_leave_entries_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    collision = tmp_path / ".voiceflow-backup-collision"
    collision.write_text("reserved", encoding="utf-8")
    monkeypatch.setattr(permissions.secrets, "token_hex", lambda _size: "collision")

    with pytest.raises(OSError, match="could not reserve"):
        permissions.replace_private_file(source, destination)

    assert source.read_text(encoding="utf-8") == "replacement"
    assert destination.read_text(encoding="utf-8") == "old"
    assert collision.read_text(encoding="utf-8") == "reserved"


def test_backup_reservation_close_failure_removes_placeholder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage import permissions

    parent_descriptor = os.open(tmp_path, os.O_RDONLY)
    original_close = os.close
    failed = False

    def fail_reserved_close(descriptor: int) -> None:
        nonlocal failed
        original_close(descriptor)
        if not failed and descriptor != parent_descriptor:
            failed = True
            raise OSError("injected backup close failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "close", fail_reserved_close)
            with pytest.raises(OSError, match="injected backup close failure"):
                permissions._reserve_backup_entry(parent_descriptor)
    finally:
        original_close(parent_descriptor)

    assert failed
    assert not list(tmp_path.glob(".voiceflow-backup-*"))


def test_replace_helper_cleans_up_when_backup_placeholder_disappears_on_move_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    original_replace = os.replace
    failed = False

    def fail_backup_move(
        source_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if not failed and Path(os.fsdecode(destination_name)).name.startswith(".voiceflow-backup-"):
            failed = True
            os.unlink(destination_name, dir_fd=dst_dir_fd)
            raise OSError("injected backup move failure")
        original_replace(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", fail_backup_move)

    with pytest.raises(OSError, match="injected backup move failure"):
        replace_private_file(source, destination)

    assert failed
    assert source.read_text(encoding="utf-8") == "replacement"
    assert destination.read_text(encoding="utf-8") == "old"
    assert not list(tmp_path.glob(".voiceflow-backup-*"))


def test_replace_helper_rolls_back_if_backup_rename_reports_failure_after_move(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    os.chmod(destination, 0o640)
    original_replace = os.replace
    failed = False

    def fail_after_backup_move(
        source_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        destination_name: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        original_replace(
            source_name,
            destination_name,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        if not failed and Path(os.fsdecode(destination_name)).name.startswith(".voiceflow-backup-"):
            failed = True
            raise OSError("injected backup rename failure")

    monkeypatch.setattr(os, "replace", fail_after_backup_move)

    with pytest.raises(OSError, match="injected backup rename failure"):
        replace_private_file(source, destination)

    assert failed
    assert source.read_text(encoding="utf-8") == "replacement"
    assert destination.read_text(encoding="utf-8") == "old"
    assert _mode(destination) == 0o640


def test_replace_helper_contains_backup_unlink_failure_after_unlink(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    original_unlink = os.unlink
    failed = False

    def fail_after_backup_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        original_unlink(path, dir_fd=dir_fd)
        if not failed and Path(os.fsdecode(path)).name.startswith(".voiceflow-backup-"):
            failed = True
            raise OSError("injected backup cleanup failure")

    monkeypatch.setattr(os, "unlink", fail_after_backup_unlink)

    replace_private_file(source, destination)

    assert failed
    assert destination.read_text(encoding="utf-8") == "replacement"
    assert _mode(destination) == 0o600


def test_replace_helper_rolls_back_if_backup_unlink_fails_before_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    destination = tmp_path / "destination"
    destination.write_text("old", encoding="utf-8")
    os.chmod(destination, 0o640)
    original_unlink = os.unlink
    failed = False

    def fail_before_backup_unlink(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *,
        dir_fd: int | None = None,
    ) -> None:
        nonlocal failed
        if not failed and Path(os.fsdecode(path)).name.startswith(".voiceflow-backup-"):
            failed = True
            raise OSError("injected backup cleanup failure")
        original_unlink(path, dir_fd=dir_fd)

    monkeypatch.setattr(os, "unlink", fail_before_backup_unlink)

    with pytest.raises(OSError, match="injected backup cleanup failure"):
        replace_private_file(source, destination)

    assert failed
    assert destination.read_text(encoding="utf-8") == "old"
    assert _mode(destination) == 0o640
    assert not list(tmp_path.glob(".voiceflow-backup-*"))


def test_replace_helper_rejects_same_path_and_nonregular_destination(tmp_path: Path) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    with pytest.raises(OSError):
        replace_private_file(source, source)

    destination = tmp_path / "destination"
    destination.mkdir()
    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert source.read_text(encoding="utf-8") == "replacement"
    assert destination.is_dir()


def test_restrict_open_file_rejects_directory_descriptor(tmp_path: Path) -> None:
    from voiceflow.storage.permissions import restrict_open_file

    descriptor = os.open(tmp_path, os.O_RDONLY)
    try:
        with pytest.raises(OSError):
            restrict_open_file(descriptor)
    finally:
        os.close(descriptor)


def test_replace_helper_rejects_symlink_destination_without_touching_referent(
    tmp_path: Path,
) -> None:
    from voiceflow.storage.permissions import replace_private_file

    source = tmp_path / "source"
    source.write_text("replacement", encoding="utf-8")
    external = tmp_path / "external"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    destination = tmp_path / "destination"
    destination.symlink_to(external)

    with pytest.raises(OSError):
        replace_private_file(source, destination)

    assert destination.is_symlink()
    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_database_rejects_existing_and_dangling_symlinks(tmp_path: Path) -> None:
    external = tmp_path / "external.sqlite3"
    connection = sqlite3.connect(external)
    connection.execute("CREATE TABLE sentinel (value TEXT)")
    connection.close()
    original = external.read_bytes()
    os.chmod(external, 0o644)
    linked = tmp_path / "linked.sqlite3"
    linked.symlink_to(external)

    with pytest.raises(OSError):
        VoiceBudStorage(linked, history=HistoryConfig())

    assert external.read_bytes() == original
    assert _mode(external) == 0o644

    absent_external = tmp_path / "absent-external.sqlite3"
    dangling = tmp_path / "dangling.sqlite3"
    dangling.symlink_to(absent_external)
    with pytest.raises(OSError):
        VoiceBudStorage(dangling, history=HistoryConfig())
    assert not absent_external.exists()


def test_database_rejects_sidecar_symlink_before_sqlite_uses_it(tmp_path: Path) -> None:
    path = tmp_path / "voicebud.sqlite3"
    external = tmp_path / "external-wal"
    external.write_text("external", encoding="utf-8")
    os.chmod(external, 0o644)
    Path(f"{path}-wal").symlink_to(external)

    with pytest.raises(OSError):
        VoiceBudStorage(path, history=HistoryConfig())

    assert external.read_text(encoding="utf-8") == "external"
    assert _mode(external) == 0o644


def test_database_and_wal_artifacts_are_private_across_lifecycle(tmp_path: Path) -> None:
    directory = tmp_path / "database" / "nested"
    path = directory / "voicebud.sqlite3"
    previous_umask = os.umask(0)
    try:
        storage = VoiceBudStorage(path, history=HistoryConfig())
        storage.execute(
            "INSERT INTO dictionary_entries (id, kind, spoken_form, written_form, "
            "priority, enabled, use_count, created_at, updated_at) "
            "VALUES ('one', 'preferred_term', 'one', 'One', 0, 1, 0, 1, 1)"
        )
        present = [
            candidate
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
            if candidate.exists()
        ]
        assert path in present
        assert all(_mode(candidate) == 0o600 for candidate in present)
        storage.execute("PRAGMA wal_checkpoint(FULL)")
        assert all(
            _mode(candidate) == 0o600
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
            if candidate.exists()
        )
        storage.close()
        assert _mode(path) == 0o600

        reopened = VoiceBudStorage(path, history=HistoryConfig())
        assert all(
            _mode(candidate) == 0o600
            for candidate in (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
            if candidate.exists()
        )
        reopened.close()
    finally:
        os.umask(previous_umask)

    assert _mode(directory.parent) == 0o700
    assert _mode(directory) == 0o700


def test_feedback_replacements_are_private_under_permissive_umask(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "corpus" / "nested"
    replaced_modes: list[tuple[str, int, int]] = []
    original_replace = os.replace

    def inspect_replace(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        source_mode = os.stat(source, dir_fd=src_dir_fd).st_mode & 0o777
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )
        final_mode = os.stat(destination, dir_fd=dst_dir_fd).st_mode & 0o777
        replaced_modes.append((Path(destination).name, source_mode, final_mode))

    monkeypatch.setattr(os, "replace", inspect_replace)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)
    previous_umask = os.umask(0)
    try:
        output = buffer.save_confirmed(
            "sample",
            directory,
            reference="sample",
            confirmed=True,
        )
    finally:
        os.umask(previous_umask)

    assert output is not None
    assert _mode(directory.parent) == 0o700
    assert _mode(directory) == 0o700
    assert _mode(output) == 0o600
    assert _mode(directory / "manifest.jsonl") == 0o600
    assert {name for name, _, _ in replaced_modes} == {"sample.wav", "manifest.jsonl"}
    assert all(source_mode == final_mode == 0o600 for _, source_mode, final_mode in replaced_modes)


def test_failed_manifest_replace_keeps_content_and_original_mode(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest = tmp_path / "manifest.jsonl"
    original = '{"id":"existing"}\n'
    manifest.write_text(original, encoding="utf-8")
    os.chmod(manifest, 0o644)
    temporary_paths: list[Path] = []
    original_replace = os.replace

    def fail_manifest_replace(
        source: str | Path,
        destination: str | Path,
        *,
        src_dir_fd: int | None = None,
        dst_dir_fd: int | None = None,
    ) -> None:
        if Path(destination).name == manifest.name and Path(source).name.startswith(".manifest-"):
            temporary_paths.append(tmp_path / Path(source))
            assert os.stat(source, dir_fd=src_dir_fd).st_mode & 0o777 == 0o600
            raise OSError("replace failed")
        original_replace(
            source,
            destination,
            src_dir_fd=src_dir_fd,
            dst_dir_fd=dst_dir_fd,
        )

    monkeypatch.setattr(os, "replace", fail_manifest_replace)
    buffer = RecentAudioBuffer()
    buffer.store("sample", np.array([0.25], dtype=np.float32), 16000)

    with pytest.raises(OSError, match="replace failed"):
        buffer.save_confirmed("sample", tmp_path, reference="sample", confirmed=True)

    assert manifest.read_text(encoding="utf-8") == original
    assert _mode(manifest) == 0o644
    assert temporary_paths and all(not path.exists() for path in temporary_paths)
    assert not (tmp_path / "sample.wav").exists()
