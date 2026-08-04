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


def _validate_directory(path: Path) -> os.stat_result:
    state = path.lstat()
    if stat.S_ISLNK(state.st_mode):
        raise _symlink_error(path)
    if not stat.S_ISDIR(state.st_mode):
        raise _not_directory_error(path)
    return state


def _identity(state: os.stat_result) -> tuple[int, int]:
    return state.st_dev, state.st_ino


def _stale_directory_error(path: Path) -> OSError:
    return OSError(errno.ESTALE, "managed directory changed during validation", path)


def _private_directory_mode_error(path: Path) -> PermissionError:
    return PermissionError(
        errno.EACCES,
        "app-created directory does not have mode 0700",
        path,
    )


def _validate_created_directory_mode(path: Path, state: os.stat_result) -> None:
    if stat.S_IMODE(state.st_mode) != PRIVATE_DIRECTORY_MODE:
        raise _private_directory_mode_error(path)


def _open_directory_component(
    parent_descriptor: int,
    name: str,
    path: Path,
    expected: os.stat_result,
) -> int:
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | _CLOSE_ON_EXEC | _DIRECTORY | _NO_FOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise _symlink_error(path) from error
        if error.errno == errno.ENOTDIR:
            raise _not_directory_error(path) from error
        raise

    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise _not_directory_error(path)
        if _identity(opened) != _identity(expected):
            raise _stale_directory_error(path)

        entry = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(entry.st_mode):
            raise _symlink_error(path)
        if not stat.S_ISDIR(entry.st_mode):
            raise _not_directory_error(path)
        if _identity(entry) != _identity(opened):
            raise _stale_directory_error(path)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _verify_directory_chain(
    absolute: Path,
    descriptors: list[int],
    components: list[tuple[str, Path, bool]],
) -> None:
    for index, (name, path, created_by_app) in enumerate(components):
        parent_descriptor = descriptors[index]
        child = os.fstat(descriptors[index + 1])
        entry = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if stat.S_ISLNK(entry.st_mode):
            raise _symlink_error(path)
        if not stat.S_ISDIR(entry.st_mode):
            raise _not_directory_error(path)
        if _identity(entry) != _identity(child):
            raise _stale_directory_error(path)
        if created_by_app:
            _validate_created_directory_mode(path, child)
            _validate_created_directory_mode(path, entry)

    pinned = os.fstat(descriptors[-1])
    final = _validate_directory(absolute)
    if _identity(final) != _identity(pinned):
        raise _stale_directory_error(absolute)
    if components and components[-1][2]:
        _validate_created_directory_mode(absolute, pinned)
        _validate_created_directory_mode(absolute, final)


def ensure_private_directory(directory: Path) -> None:
    """Create private components and reject path replacement during validation.

    No later path-based chmod is performed because Python's mkdir API cannot
    atomically return the inode it created.  If an unusual process umask removes
    owner permissions, or the entry is replaced before it can be pinned, the
    function fails safely instead of reporting a non-0700 directory as private.
    """
    absolute = _absolute(directory)
    current = Path(absolute.anchor)
    root_descriptor = os.open(
        absolute.anchor,
        os.O_RDONLY | _CLOSE_ON_EXEC | _DIRECTORY | _NO_FOLLOW,
    )
    descriptors = [root_descriptor]
    components: list[tuple[str, Path, bool]] = []
    try:
        if not stat.S_ISDIR(os.fstat(root_descriptor).st_mode):
            raise _not_directory_error(Path(absolute.anchor))

        for name in absolute.parts[1:]:
            current /= name
            created_by_app = False
            try:
                expected = _validate_directory(current)
            except FileNotFoundError:
                try:
                    os.mkdir(name, PRIVATE_DIRECTORY_MODE, dir_fd=descriptors[-1])
                except FileExistsError:
                    # A concurrent creator owns this component; preserve its mode.
                    expected = _validate_directory(current)
                else:
                    created_by_app = True
                    expected = _validate_directory(current)
                    # A same-mode replacement is indistinguishable here, but it
                    # still satisfies the privacy invariant without mutation.
                    _validate_created_directory_mode(current, expected)

            descriptor = _open_directory_component(
                descriptors[-1],
                name,
                current,
                expected,
            )
            if created_by_app:
                _validate_created_directory_mode(current, os.fstat(descriptor))
            descriptors.append(descriptor)
            components.append((name, current, created_by_app))

        _verify_directory_chain(absolute, descriptors, components)
    finally:
        for descriptor in reversed(descriptors):
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
