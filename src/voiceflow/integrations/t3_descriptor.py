from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from voiceflow.integrations.t3_protocol import (
    PROTOCOL_VERSION,
    TRANSPORT,
    LocalValidationError,
    decode_json_object,
)

DEFAULT_T3_CODE_HOME = Path("~/.t3").expanduser()
DESCRIPTOR_RELATIVE_PATH = Path("userdata/integrations/voicebud/bridge.json")
DEFAULT_DESCRIPTOR_PATH = DEFAULT_T3_CODE_HOME / DESCRIPTOR_RELATIVE_PATH
MAX_DESCRIPTOR_BYTES = 16 * 1024

_DESCRIPTOR_KEYS = frozenset({"version", "transport", "socketPath", "secret", "pid"})


@dataclass(frozen=True, slots=True)
class Descriptor:
    path: Path
    socket_path: Path
    secret: str
    owner: int
    descriptor_device: int
    descriptor_inode: int
    descriptor_fingerprint: bytes
    socket_device: int
    socket_inode: int


class SessionChangedError(LocalValidationError):
    pass


class DescriptorLoader:
    def __init__(
        self,
        path: Path,
        *,
        current_uid: Callable[[], int] = os.getuid,
    ) -> None:
        self.path = path
        self._current_uid = current_uid

    def load(self) -> Descriptor:
        descriptor_bytes, descriptor_stat = self.secure_read(self.path)
        try:
            data = decode_json_object(descriptor_bytes)
        except (UnicodeDecodeError, ValueError, TypeError) as exc:
            raise LocalValidationError from exc
        if set(data) != _DESCRIPTOR_KEYS:
            raise LocalValidationError
        if (
            type(data["version"]) is not int
            or data["version"] != PROTOCOL_VERSION
            or data["transport"] != TRANSPORT
        ):
            raise LocalValidationError
        secret = data["secret"]
        socket_value = data["socketPath"]
        pid = data["pid"]
        if not isinstance(secret, str) or not secret or len(secret) > 4096:
            raise LocalValidationError
        if not isinstance(socket_value, str) or not socket_value:
            raise LocalValidationError
        if type(pid) is not int or pid <= 0:
            raise LocalValidationError
        socket_path = Path(socket_value)
        if not socket_path.is_absolute() or ".." in socket_path.parts:
            raise LocalValidationError
        socket_stat = self.validate_socket_path(socket_path, descriptor_stat.st_uid)
        return Descriptor(
            path=self.path,
            socket_path=socket_path,
            secret=secret,
            owner=descriptor_stat.st_uid,
            descriptor_device=descriptor_stat.st_dev,
            descriptor_inode=descriptor_stat.st_ino,
            descriptor_fingerprint=hashlib.sha256(descriptor_bytes).digest(),
            socket_device=socket_stat.st_dev,
            socket_inode=socket_stat.st_ino,
        )

    def secure_read(self, path: Path) -> tuple[bytes, os.stat_result]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor_fd = os.open(path, flags)
        except OSError as exc:
            raise LocalValidationError from exc
        try:
            descriptor_stat = os.fstat(descriptor_fd)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise LocalValidationError
            if descriptor_stat.st_uid != self._current_uid():
                raise LocalValidationError
            if descriptor_stat.st_mode & 0o077:
                raise LocalValidationError
            if descriptor_stat.st_size <= 0 or descriptor_stat.st_size > MAX_DESCRIPTOR_BYTES:
                raise LocalValidationError
            chunks: list[bytes] = []
            remaining = MAX_DESCRIPTOR_BYTES + 1
            while remaining:
                chunk = os.read(descriptor_fd, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            descriptor_bytes = b"".join(chunks)
            if len(descriptor_bytes) > MAX_DESCRIPTOR_BYTES:
                raise LocalValidationError
            return descriptor_bytes, descriptor_stat
        finally:
            os.close(descriptor_fd)

    def validate_socket_path(
        self,
        socket_path: Path,
        expected_owner: int,
    ) -> os.stat_result:
        descriptor_directory = self.path.parent
        try:
            lexical_socket = Path(os.path.abspath(socket_path))
            lexical_directory = Path(os.path.abspath(descriptor_directory))
            if os.path.commonpath((lexical_socket, lexical_directory)) != str(lexical_directory):
                raise LocalValidationError
            relative = lexical_socket.relative_to(lexical_directory)
            if ".." in relative.parts:
                raise LocalValidationError
            resolved_directory = descriptor_directory.resolve(strict=True)
            resolved_socket = socket_path.resolve(strict=True)
            if resolved_socket.parent != resolved_directory:
                raise LocalValidationError
            socket_stat = os.lstat(socket_path)
        except (OSError, ValueError) as exc:
            raise LocalValidationError from exc
        if stat.S_ISLNK(socket_stat.st_mode) or not stat.S_ISSOCK(socket_stat.st_mode):
            raise LocalValidationError
        if socket_stat.st_uid != expected_owner:
            raise LocalValidationError
        return socket_stat

    def verify_session(self, descriptor: Descriptor) -> None:
        try:
            current_bytes, current_stat = self.secure_read(descriptor.path)
            current_socket = self.validate_socket_path(
                descriptor.socket_path,
                descriptor.owner,
            )
        except LocalValidationError:
            raise SessionChangedError from None
        if (
            current_stat.st_dev != descriptor.descriptor_device
            or current_stat.st_ino != descriptor.descriptor_inode
            or hashlib.sha256(current_bytes).digest() != descriptor.descriptor_fingerprint
            or current_socket.st_dev != descriptor.socket_device
            or current_socket.st_ino != descriptor.socket_inode
        ):
            raise SessionChangedError


def default_descriptor_path() -> Path:
    configured_home = os.environ.get("T3CODE_HOME", "").strip()
    t3_code_home = Path(configured_home).expanduser() if configured_home else DEFAULT_T3_CODE_HOME
    return t3_code_home / DESCRIPTOR_RELATIVE_PATH
