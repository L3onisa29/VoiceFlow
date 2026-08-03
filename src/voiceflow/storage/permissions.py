from __future__ import annotations

import ctypes
import errno
import os
import secrets
import stat
import sys
import threading
from contextlib import suppress
from pathlib import Path

PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600

_CLOSE_ON_EXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY_OPEN_FLAGS = os.O_RDONLY | _CLOSE_ON_EXEC | _DIRECTORY | _NO_FOLLOW
_PRIVATE_UMASK = 0o077
_UMASK_LOCK = threading.Lock()
_RENAME_EXCL = 0x00000004
_TEMPORARY_DIRECTORY_ATTEMPTS = 3


def _absolute(path: Path) -> Path:
    return Path(os.path.abspath(os.fspath(path.expanduser())))


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _symlink_error(path: Path) -> OSError:
    return OSError(errno.ELOOP, "refusing to follow a managed-path symlink", path)


def _not_directory_error(path: Path) -> NotADirectoryError:
    return NotADirectoryError(errno.ENOTDIR, "managed path component is not a directory", path)


def _not_regular_file_error(path: Path) -> OSError:
    return OSError(errno.EINVAL, "managed file is not a regular file", path)


def _open_existing_directory(parent_descriptor: int, name: str, path: Path) -> int:
    before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if stat.S_ISLNK(before.st_mode):
        raise _symlink_error(path)
    if not stat.S_ISDIR(before.st_mode):
        raise _not_directory_error(path)

    descriptor = os.open(name, _DIRECTORY_OPEN_FLAGS, dir_fd=parent_descriptor)
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_inode(before, opened):
            raise OSError(errno.ESTALE, "managed directory changed while opening", path)
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if not stat.S_ISDIR(current.st_mode) or not _same_inode(opened, current):
            raise OSError(errno.ESTALE, "managed directory changed after opening", path)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _rename_directory_exclusive(
    parent_descriptor: int,
    source_name: str,
    destination_name: str,
    path: Path,
) -> None:
    """Publish a sibling directory atomically without replacing an existing entry."""
    if sys.platform != "darwin":
        raise OSError(errno.ENOTSUP, "exclusive directory publication is unavailable", path)

    library = ctypes.CDLL(None, use_errno=True)
    try:
        renameatx_np = library.renameatx_np
    except AttributeError as error:
        raise OSError(
            errno.ENOTSUP,
            "exclusive directory publication is unavailable",
            path,
        ) from error

    renameatx_np.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameatx_np.restype = ctypes.c_int
    ctypes.set_errno(0)
    result = renameatx_np(
        parent_descriptor,
        os.fsencode(source_name),
        parent_descriptor,
        os.fsencode(destination_name),
        _RENAME_EXCL,
    )
    if result != 0:
        error_number = ctypes.get_errno() or errno.EIO
        raise OSError(error_number, os.strerror(error_number), path)


def _remove_owned_temporary_directory(
    parent_descriptor: int,
    name: str,
    owned: os.stat_result,
) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(current.st_mode) or not _same_inode(owned, current):
        return
    os.rmdir(name, dir_fd=parent_descriptor)


def _create_private_directory(parent_descriptor: int, name: str, path: Path) -> int | None:
    # The temporary name is not visible before mkdir, so the inode can be opened and hardened
    # before the requested entry is published. RENAME_EXCL is the Darwin no-replace primitive:
    # a concurrent winner is never overwritten and is opened without chmod by the caller.
    for _attempt in range(_TEMPORARY_DIRECTORY_ATTEMPTS):
        temporary_name = f".voiceflow-directory-{secrets.token_hex(16)}"
        with _UMASK_LOCK:
            previous_umask = os.umask(_PRIVATE_UMASK)
            try:
                try:
                    os.mkdir(
                        temporary_name,
                        PRIVATE_DIRECTORY_MODE,
                        dir_fd=parent_descriptor,
                    )
                except FileExistsError:
                    continue
            finally:
                os.umask(previous_umask)
        break
    else:
        raise OSError(errno.EAGAIN, "could not reserve private directory name", path)

    created: os.stat_result | None = None
    descriptor: int | None = None
    try:
        created = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if stat.S_ISLNK(created.st_mode):
            raise _symlink_error(path)
        if not stat.S_ISDIR(created.st_mode):
            raise _not_directory_error(path)

        descriptor = os.open(
            temporary_name,
            _DIRECTORY_OPEN_FLAGS,
            dir_fd=parent_descriptor,
        )
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode) or not _same_inode(created, opened):
            raise OSError(errno.ESTALE, "temporary directory changed while opening", path)
        current = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(current.st_mode) or not _same_inode(opened, current):
            raise OSError(errno.ESTALE, "temporary directory changed after opening", path)

        os.fchmod(descriptor, PRIVATE_DIRECTORY_MODE)
        hardened = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(hardened.st_mode)
            or not _same_inode(opened, hardened)
            or stat.S_IMODE(hardened.st_mode) != PRIVATE_DIRECTORY_MODE
        ):
            raise OSError(errno.ESTALE, "temporary directory changed while hardening", path)
        current = os.stat(
            temporary_name,
            dir_fd=parent_descriptor,
            follow_symlinks=False,
        )
        if not stat.S_ISDIR(current.st_mode) or not _same_inode(hardened, current):
            raise OSError(errno.ESTALE, "temporary directory changed while hardening", path)

        try:
            _rename_directory_exclusive(parent_descriptor, temporary_name, name, path)
        except FileExistsError:
            _remove_owned_temporary_directory(parent_descriptor, temporary_name, hardened)
            os.close(descriptor)
            return None

        try:
            published = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        except OSError as error:
            raise OSError(
                errno.ESTALE,
                "published directory could not be verified",
                path,
            ) from error
        if not stat.S_ISDIR(published.st_mode) or not _same_inode(hardened, published):
            raise OSError(errno.ESTALE, "published directory changed", path)
        return descriptor
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        if created is not None:
            with suppress(OSError):
                _remove_owned_temporary_directory(parent_descriptor, temporary_name, created)
        raise


def _walk_directory(directory: Path, *, create: bool) -> tuple[Path, ...]:
    absolute = _absolute(directory)
    missing: list[Path] = []
    current = Path(absolute.anchor)
    descriptor = os.open(absolute.anchor, _DIRECTORY_OPEN_FLAGS)
    try:
        parent_missing = False
        for name in absolute.parts[1:]:
            current /= name
            if parent_missing:
                missing.append(current)
                continue
            try:
                child_descriptor = _open_existing_directory(descriptor, name, current)
            except FileNotFoundError:
                missing.append(current)
                if not create:
                    parent_missing = True
                    continue
                child_descriptor = _create_private_directory(descriptor, name, current)
                if child_descriptor is None:
                    child_descriptor = _open_existing_directory(descriptor, name, current)
            os.close(descriptor)
            descriptor = child_descriptor
    finally:
        os.close(descriptor)
    return tuple(missing)


def missing_directories(directory: Path) -> tuple[Path, ...]:
    """Return missing components while rejecting unsafe existing components."""
    return _walk_directory(directory, create=False)


def ensure_private_directory(directory: Path) -> None:
    """Create missing components at 0700 without chmodding pathname winners."""
    _walk_directory(directory, create=True)


def _open_regular_file(
    path: Path,
    flags: int,
    *,
    create: bool,
    missing_ok: bool,
    harden: bool,
) -> tuple[int, int] | None:
    absolute = _absolute(path)
    parent_descriptor = _open_directory_descriptor(absolute.parent)
    name = absolute.name
    try:
        for _attempt in range(3):
            try:
                before = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    if missing_ok:
                        os.close(parent_descriptor)
                        return None
                    raise
                before = None
            else:
                if stat.S_ISLNK(before.st_mode):
                    raise _symlink_error(absolute)
                if not stat.S_ISREG(before.st_mode):
                    raise _not_regular_file_error(absolute)

            open_flags = flags | _CLOSE_ON_EXEC | _NO_FOLLOW
            if before is None:
                open_flags |= os.O_CREAT | os.O_EXCL
            try:
                descriptor = os.open(
                    name,
                    open_flags,
                    PRIVATE_FILE_MODE,
                    dir_fd=parent_descriptor,
                )
            except FileExistsError:
                if before is None:
                    continue
                raise

            try:
                opened = os.fstat(descriptor)
                if not stat.S_ISREG(opened.st_mode):
                    raise _not_regular_file_error(absolute)
                if before is not None and not _same_inode(before, opened):
                    raise OSError(errno.ESTALE, "managed file changed while opening", absolute)
                current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or not _same_inode(opened, current):
                    raise OSError(errno.ESTALE, "managed file changed after opening", absolute)
                if harden:
                    os.fchmod(descriptor, PRIVATE_FILE_MODE)
                    hardened = os.fstat(descriptor)
                    if not stat.S_ISREG(hardened.st_mode) or not _same_inode(opened, hardened):
                        raise OSError(
                            errno.ESTALE,
                            "managed file changed while hardening",
                            absolute,
                        )
                current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
                if not stat.S_ISREG(current.st_mode) or not _same_inode(opened, current):
                    raise OSError(errno.ESTALE, "managed file changed while hardening", absolute)
            except Exception:
                os.close(descriptor)
                raise
            return descriptor, parent_descriptor
        raise OSError(errno.EAGAIN, "managed file changed repeatedly while opening", absolute)
    except Exception:
        os.close(parent_descriptor)
        raise


def _open_directory_descriptor(directory: Path) -> int:
    absolute = _absolute(directory)
    descriptor = os.open(absolute.anchor, _DIRECTORY_OPEN_FLAGS)
    current = Path(absolute.anchor)
    try:
        for name in absolute.parts[1:]:
            current /= name
            child_descriptor = _open_existing_directory(descriptor, name, current)
            os.close(descriptor)
            descriptor = child_descriptor
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def restrict_file_permissions(path: Path) -> bool:
    """Harden an existing regular file through a verified no-follow descriptor."""
    opened = _open_regular_file(
        path,
        os.O_RDONLY,
        create=False,
        missing_ok=True,
        harden=True,
    )
    if opened is None:
        return False
    descriptor, parent_descriptor = opened
    os.close(descriptor)
    os.close(parent_descriptor)
    return True


def ensure_private_file(path: Path) -> None:
    """Create or harden a regular managed file without following symlinks."""
    opened = _open_regular_file(
        path,
        os.O_RDWR,
        create=True,
        missing_ok=False,
        harden=True,
    )
    assert opened is not None
    descriptor, parent_descriptor = opened
    os.close(descriptor)
    os.close(parent_descriptor)


def read_managed_text_file(path: Path) -> str:
    """Read an optional managed UTF-8 file from a verified descriptor."""
    opened = _open_regular_file(
        path,
        os.O_RDONLY,
        create=False,
        missing_ok=True,
        harden=False,
    )
    if opened is None:
        return ""
    descriptor, parent_descriptor = opened
    try:
        chunks: list[bytes] = []
        while chunk := os.read(descriptor, 64 * 1024):
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8")
    finally:
        os.close(descriptor)
        os.close(parent_descriptor)


def restrict_open_file(descriptor: int) -> None:
    """Restrict a newly created open app-managed file."""
    opened = os.fstat(descriptor)
    if not stat.S_ISREG(opened.st_mode):
        raise _not_regular_file_error(Path(f"<descriptor {descriptor}>"))
    os.fchmod(descriptor, PRIVATE_FILE_MODE)


def _validate_private_source(state: os.stat_result, path: Path) -> None:
    if not stat.S_ISREG(state.st_mode):
        raise _not_regular_file_error(path)
    if state.st_nlink != 1:
        raise OSError(errno.EMLINK, "private replacement source has multiple links", path)
    if hasattr(os, "geteuid") and state.st_uid != os.geteuid():
        raise PermissionError(errno.EPERM, "private replacement source is not app-owned", path)


def _verified_regular_entry(
    parent_descriptor: int,
    name: str,
    path: Path,
    expected: os.stat_result,
) -> os.stat_result:
    current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    if not stat.S_ISREG(current.st_mode) or not _same_inode(expected, current):
        raise OSError(errno.ESTALE, "managed file entry changed", path)
    return current


def _open_verified_regular_entry(
    parent_descriptor: int,
    name: str,
    path: Path,
    expected: os.stat_result,
) -> tuple[int, os.stat_result]:
    descriptor = os.open(
        name,
        os.O_RDONLY | _CLOSE_ON_EXEC | _NO_FOLLOW,
        dir_fd=parent_descriptor,
    )
    try:
        opened = os.fstat(descriptor)
        if not stat.S_ISREG(opened.st_mode) or not _same_inode(expected, opened):
            raise OSError(errno.ESTALE, "managed file changed while opening", path)
        _verified_regular_entry(parent_descriptor, name, path, opened)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor, opened


def _reserve_backup_entry(parent_descriptor: int) -> str:
    for _attempt in range(3):
        name = f".voiceflow-backup-{secrets.token_hex(16)}"
        try:
            descriptor = os.open(
                name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | _CLOSE_ON_EXEC | _NO_FOLLOW,
                PRIVATE_FILE_MODE,
                dir_fd=parent_descriptor,
            )
        except FileExistsError:
            continue
        try:
            os.close(descriptor)
        except Exception:
            with suppress(OSError):
                os.unlink(name, dir_fd=parent_descriptor)
            raise
        return name
    raise OSError(errno.EAGAIN, "could not reserve private replacement backup")


def _remove_unsafe_entry(parent_descriptor: int, name: str, path: Path) -> None:
    try:
        current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(current.st_mode):
        raise _not_regular_file_error(path)
    os.unlink(name, dir_fd=parent_descriptor)


def _rollback_replacement(
    destination_parent: int,
    destination_name: str,
    destination_path: Path,
    backup_name: str | None,
    previous_destination: os.stat_result | None,
) -> None:
    _remove_unsafe_entry(destination_parent, destination_name, destination_path)
    if backup_name is None or previous_destination is None:
        return
    _verified_regular_entry(
        destination_parent,
        backup_name,
        destination_path,
        previous_destination,
    )
    os.replace(
        backup_name,
        destination_name,
        src_dir_fd=destination_parent,
        dst_dir_fd=destination_parent,
    )
    _verified_regular_entry(
        destination_parent,
        destination_name,
        destination_path,
        previous_destination,
    )


def replace_private_file(source: Path, destination: Path) -> None:
    """Harden and atomically replace with post-rename inode verification.

    Python on macOS has no rename-by-descriptor operation. A hostile same-UID process can
    therefore still exchange the source entry during the final rename syscall interval.
    The replacement is not reported as committed until the destination is reopened without
    following symlinks and both descriptor and pathname still identify the captured source.
    """
    source_absolute = _absolute(source)
    destination_absolute = _absolute(destination)
    if source_absolute == destination_absolute:
        raise OSError(errno.EINVAL, "replacement source and destination must differ", source)

    opened = _open_regular_file(
        source_absolute,
        os.O_RDONLY,
        create=False,
        missing_ok=False,
        harden=False,
    )
    assert opened is not None
    source_descriptor, source_parent = opened
    destination_parent: int | None = None
    destination_descriptor: int | None = None
    committed_descriptor: int | None = None
    backup_name: str | None = None
    previous_destination: os.stat_result | None = None
    backup_moved = False
    replace_attempted = False
    try:
        source_state = os.fstat(source_descriptor)
        _validate_private_source(source_state, source_absolute)
        source_current = _verified_regular_entry(
            source_parent,
            source_absolute.name,
            source_absolute,
            source_state,
        )
        _validate_private_source(source_current, source_absolute)
        os.fchmod(source_descriptor, PRIVATE_FILE_MODE)
        hardened_source = os.fstat(source_descriptor)
        if not _same_inode(source_state, hardened_source):
            raise OSError(errno.ESTALE, "replacement source changed while hardening", source)
        _validate_private_source(hardened_source, source_absolute)
        source_current = _verified_regular_entry(
            source_parent,
            source_absolute.name,
            source_absolute,
            hardened_source,
        )
        _validate_private_source(source_current, source_absolute)

        descriptor_to_close = source_descriptor
        source_descriptor = -1
        os.close(descriptor_to_close)

        destination_parent = _open_directory_descriptor(destination_absolute.parent)
        try:
            previous_destination = os.stat(
                destination_absolute.name,
                dir_fd=destination_parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            previous_destination = None
        else:
            if stat.S_ISLNK(previous_destination.st_mode):
                raise _symlink_error(destination_absolute)
            if not stat.S_ISREG(previous_destination.st_mode):
                raise _not_regular_file_error(destination_absolute)
            destination_descriptor, previous_destination = _open_verified_regular_entry(
                destination_parent,
                destination_absolute.name,
                destination_absolute,
                previous_destination,
            )

            backup_name = _reserve_backup_entry(destination_parent)
            try:
                os.replace(
                    destination_absolute.name,
                    backup_name,
                    src_dir_fd=destination_parent,
                    dst_dir_fd=destination_parent,
                )
            except Exception:
                try:
                    backup_state = os.stat(
                        backup_name,
                        dir_fd=destination_parent,
                        follow_symlinks=False,
                    )
                except OSError:
                    pass
                else:
                    backup_moved = stat.S_ISREG(backup_state.st_mode) and _same_inode(
                        previous_destination,
                        backup_state,
                    )
                raise
            else:
                backup_moved = True
            _verified_regular_entry(
                destination_parent,
                backup_name,
                destination_absolute,
                previous_destination,
            )
            try:
                os.stat(
                    destination_absolute.name,
                    dir_fd=destination_parent,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                pass
            else:
                raise OSError(
                    errno.ESTALE,
                    "destination changed while creating replacement backup",
                    destination_absolute,
                )

            descriptor_to_close = destination_descriptor
            destination_descriptor = None
            os.close(descriptor_to_close)

        replace_attempted = True
        os.replace(
            source_absolute.name,
            destination_absolute.name,
            src_dir_fd=source_parent,
            dst_dir_fd=destination_parent,
        )
        committed_descriptor, committed_state = _open_verified_regular_entry(
            destination_parent,
            destination_absolute.name,
            destination_absolute,
            hardened_source,
        )
        _validate_private_source(committed_state, destination_absolute)

        if backup_moved:
            assert backup_name is not None
            try:
                os.unlink(backup_name, dir_fd=destination_parent)
            except OSError:
                try:
                    os.stat(
                        backup_name,
                        dir_fd=destination_parent,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    pass
                else:
                    raise
            backup_moved = False
            backup_name = None
    except Exception:
        if destination_parent is not None:
            if backup_moved:
                _rollback_replacement(
                    destination_parent,
                    destination_absolute.name,
                    destination_absolute,
                    backup_name,
                    previous_destination,
                )
                backup_moved = False
                backup_name = None
            elif replace_attempted:
                _rollback_replacement(
                    destination_parent,
                    destination_absolute.name,
                    destination_absolute,
                    None,
                    None,
                )
            elif backup_name is not None:
                with suppress(OSError):
                    os.unlink(backup_name, dir_fd=destination_parent)
                backup_name = None
        raise
    finally:
        for descriptor in (
            committed_descriptor,
            destination_descriptor,
            None if source_descriptor == -1 else source_descriptor,
            source_parent,
            destination_parent,
        ):
            if descriptor is not None:
                with suppress(OSError):
                    os.close(descriptor)
