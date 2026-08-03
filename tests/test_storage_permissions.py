from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

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
