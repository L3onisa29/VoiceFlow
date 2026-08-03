from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

_CLOSE_ON_EXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _symlink_error(path: Path) -> OSError:
    return OSError(errno.ELOOP, "refusing to follow a managed-path symlink", path)


def _not_directory_error(path: Path) -> NotADirectoryError:
    return NotADirectoryError(errno.ENOTDIR, "managed path component is not a directory", path)


def _not_regular_file_error(path: Path) -> OSError:
    return OSError(errno.EINVAL, "managed file is not a regular file", path)


def _validate_directory(path: Path) -> None:
    state = path.lstat()
    if stat.S_ISLNK(state.st_mode):
        raise _symlink_error(path)
    if not stat.S_ISDIR(state.st_mode):
        raise _not_directory_error(path)


def ensure_private_directory(directory: Path) -> None:
    """Create and restrict an app-managed directory without accepting symlinks."""
    absolute = _absolute(directory)
    current = Path(absolute.anchor)
    created_target = False
    for name in absolute.parts[1:]:
        current /= name
        try:
            _validate_directory(current)
        except FileNotFoundError:
            os.mkdir(current, PRIVATE_DIRECTORY_MODE)
            _validate_directory(current)
            os.chmod(current, PRIVATE_DIRECTORY_MODE, follow_symlinks=False)
            created_target = current == absolute

    descriptor = os.open(
        absolute,
        os.O_RDONLY | _CLOSE_ON_EXEC | _DIRECTORY | _NO_FOLLOW,
    )
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise _not_directory_error(absolute)
        if created_target:
            os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
    finally:
        os.close(descriptor)


def _open_regular_file(
    path: Path,
    flags: int,
    *,
    create: bool = False,
    missing_ok: bool = False,
) -> int | None:
    absolute = _absolute(path)
    open_flags = flags | _CLOSE_ON_EXEC | _NO_FOLLOW
    if create:
        open_flags |= os.O_CREAT
    try:
        descriptor = os.open(absolute, open_flags, PRIVATE_FILE_MODE)
    except FileNotFoundError:
        if missing_ok:
            return None
        raise
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _symlink_error(absolute) from error
        raise
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise _not_regular_file_error(absolute)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def restrict_file_permissions(path: Path) -> bool:
    descriptor = _open_regular_file(path, os.O_RDONLY, missing_ok=True)
    if descriptor is None:
        return False
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)
    return True


def ensure_private_file(path: Path) -> None:
    descriptor = _open_regular_file(path, os.O_RDWR, create=True)
    assert descriptor is not None
    try:
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
    finally:
        os.close(descriptor)


def read_managed_text_file(path: Path) -> str:
    descriptor = _open_regular_file(path, os.O_RDONLY, missing_ok=True)
    if descriptor is None:
        return ""
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(descriptor)


def restrict_open_file(descriptor: int) -> None:
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise _not_regular_file_error(Path(f"<descriptor {descriptor}>"))
    os.fchmod(descriptor, PRIVATE_FILE_MODE)


def replace_private_file(source: Path, destination: Path) -> None:
    """Atomically publish a private regular file into an app-managed directory."""
    source = _absolute(source)
    destination = _absolute(destination)
    if source == destination:
        raise OSError(errno.EINVAL, "replacement source and destination must differ", source)

    try:
        destination_state = destination.lstat()
    except FileNotFoundError:
        pass
    else:
        if stat.S_ISLNK(destination_state.st_mode):
            raise _symlink_error(destination)
        if not stat.S_ISREG(destination_state.st_mode):
            raise _not_regular_file_error(destination)

    descriptor = _open_regular_file(source, os.O_RDONLY)
    assert descriptor is not None
    try:
        source_state = os.fstat(descriptor)
        if source_state.st_nlink != 1:
            raise OSError(errno.EMLINK, "private replacement source has multiple links", source)
        if hasattr(os, "geteuid") and source_state.st_uid != os.geteuid():
            raise PermissionError(
                errno.EPERM,
                "private replacement source is not app-owned",
                source,
            )
        os.fchmod(descriptor, PRIVATE_FILE_MODE)
        os.replace(source, destination)
        published = destination.lstat()
        if not stat.S_ISREG(published.st_mode) or (
            published.st_dev,
            published.st_ino,
        ) != (source_state.st_dev, source_state.st_ino):
            raise OSError(errno.ESTALE, "published file changed during replacement", destination)
    finally:
        os.close(descriptor)
