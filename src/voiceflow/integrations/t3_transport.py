from __future__ import annotations

import os
import socket
import stat
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from voiceflow.integrations.t3_descriptor import Descriptor
from voiceflow.integrations.t3_protocol import (
    MAX_RESPONSE_FRAME_BYTES,
    AmbiguousResponseError,
    BridgeResult,
    encode_request,
    validate_response,
)

DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS = 2.0
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 6.0


class SocketPort(Protocol):
    def settimeout(self, value: float | None, /) -> None: ...

    def connect(self, address: Any, /) -> None: ...

    def sendall(self, data: Any, flags: int = 0, /) -> None: ...

    def recv(self, bufsize: int, flags: int = 0, /) -> bytes: ...

    def close(self) -> None: ...


type SocketFactory = Callable[[int, int], SocketPort]
type SocketIdentityVerifier = Callable[[Descriptor], None]
type ResponseReader = Callable[[SocketPort, float], bytes]


class SocketTransport:
    def __init__(
        self,
        *,
        connect_write_timeout_seconds: float,
        response_timeout_seconds: float,
        monotonic: Callable[[], float],
        socket_factory: SocketFactory = socket.socket,
        identity_verifier: SocketIdentityVerifier | None = None,
        response_reader: ResponseReader | None = None,
    ) -> None:
        self._connect_write_timeout_seconds = connect_write_timeout_seconds
        self._response_timeout_seconds = response_timeout_seconds
        self._monotonic = monotonic
        self._socket_factory = socket_factory
        self._identity_verifier = identity_verifier or verify_socket_identity
        self._response_reader = response_reader or self.read_response

    def exchange(
        self,
        descriptor: Descriptor,
        request: Mapping[str, object],
    ) -> BridgeResult:
        frame = encode_request(request)
        connect_write_deadline = self._monotonic() + self._connect_write_timeout_seconds
        client: SocketPort | None = None
        try:
            client = self._socket_factory(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(self.remaining(connect_write_deadline))
            client.connect(str(descriptor.socket_path))
            self._identity_verifier(descriptor)
            client.settimeout(self.remaining(connect_write_deadline))
            client.sendall(frame)
            response_deadline = self._monotonic() + self._response_timeout_seconds
            response_frame = self._response_reader(client, response_deadline)
        except OSError as exc:
            raise AmbiguousResponseError("socket_io_or_timeout") from exc
        finally:
            if client is not None:
                client.close()
        return validate_response(response_frame, request)

    def remaining(self, deadline: float) -> float:
        return remaining_timeout(deadline, self._monotonic)

    def read_response(self, client: SocketPort, deadline: float) -> bytes:
        return read_response(client, deadline, self._monotonic)


def remaining_timeout(deadline: float, monotonic: Callable[[], float]) -> float:
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError
    return remaining


def read_response(
    client: SocketPort,
    deadline: float,
    monotonic: Callable[[], float],
) -> bytes:
    response = bytearray()
    while True:
        client.settimeout(remaining_timeout(deadline, monotonic))
        chunk = client.recv(min(4096, MAX_RESPONSE_FRAME_BYTES - len(response) + 1))
        if not chunk:
            raise AmbiguousResponseError
        newline = chunk.find(b"\n")
        if newline >= 0:
            response.extend(chunk[:newline])
            if newline != len(chunk) - 1:
                raise AmbiguousResponseError
            if len(response) >= MAX_RESPONSE_FRAME_BYTES:
                raise AmbiguousResponseError
            return bytes(response)
        response.extend(chunk)
        if len(response) >= MAX_RESPONSE_FRAME_BYTES:
            raise AmbiguousResponseError


def verify_socket_identity(descriptor: Descriptor) -> None:
    try:
        socket_stat = os.lstat(descriptor.socket_path)
    except OSError as exc:
        raise AmbiguousResponseError from exc
    if (
        stat.S_ISLNK(socket_stat.st_mode)
        or not stat.S_ISSOCK(socket_stat.st_mode)
        or socket_stat.st_uid != descriptor.owner
        or socket_stat.st_dev != descriptor.socket_device
        or socket_stat.st_ino != descriptor.socket_inode
    ):
        raise AmbiguousResponseError


def bounded_timeout(value: float, *, maximum: float) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    return min(timeout, maximum)
