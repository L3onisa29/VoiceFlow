from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import socket
import stat
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from voiceflow.diagnostics import exception_fields, log_event
from voiceflow.system.text_injector import InjectionDestination

LOGGER = logging.getLogger(__name__)

T3_CODE_BUNDLE_IDENTIFIER = "com.t3tools.t3code"
DEFAULT_T3_CODE_HOME = Path("~/.t3").expanduser()
DESCRIPTOR_RELATIVE_PATH = Path("userdata/integrations/voicebud/bridge.json")
DEFAULT_DESCRIPTOR_PATH = DEFAULT_T3_CODE_HOME / DESCRIPTOR_RELATIVE_PATH
PROTOCOL_VERSION = 1
TRANSPORT = "unix"
MAX_DESCRIPTOR_BYTES = 16 * 1024
MAX_TRANSCRIPT_CHARACTERS = 32_768
MAX_REQUEST_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_FRAME_BYTES = 64 * 1024
DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS = 2.0
DEFAULT_RESPONSE_TIMEOUT_SECONDS = 6.0
_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_DESCRIPTOR_KEYS = frozenset({"version", "transport", "socketPath", "secret", "pid"})
_RESPONSE_KEYS = frozenset({"version", "requestId", "accepted", "code"})


class BridgeOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    AMBIGUOUS = "ambiguous"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class BridgeResult:
    outcome: BridgeOutcome
    error_code: str | None = None
    message: str | None = None
    remote_code: str | None = None

    @property
    def accepted(self) -> bool:
        return self.outcome == BridgeOutcome.ACCEPTED

    @classmethod
    def accepted_result(cls) -> BridgeResult:
        return cls(BridgeOutcome.ACCEPTED)

    @classmethod
    def rejected(cls, remote_code: str | None = None) -> BridgeResult:
        return cls(
            BridgeOutcome.REJECTED,
            error_code="t3_rejected",
            message="T3 Code ha rifiutato la richiesta",
            remote_code=remote_code,
        )

    @classmethod
    def ambiguous(cls) -> BridgeResult:
        return cls(
            BridgeOutcome.AMBIGUOUS,
            error_code="t3_ambiguous",
            message="Esito della consegna a T3 Code non verificabile",
        )

    @classmethod
    def validation_error(cls, *, session_changed: bool = False) -> BridgeResult:
        if session_changed:
            return cls(
                BridgeOutcome.ERROR,
                error_code="t3_session_changed",
                message="La sessione T3 Code riservata non è più valida",
            )
        return cls(
            BridgeOutcome.ERROR,
            error_code="t3_validation_error",
            message="Integrazione locale T3 Code non valida",
        )


@dataclass(frozen=True, slots=True)
class T3CodeDestination:
    recording_id: str


type DictationDestination = InjectionDestination | T3CodeDestination


class FrontmostApplicationKind(StrEnum):
    T3_CODE = "t3_code"
    OTHER = "other"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class FrontmostApplication:
    kind: FrontmostApplicationKind
    bundle_identifier: str | None = None


class MacOSFrontmostApplicationDetector:
    def __init__(self, workspace_provider: Callable[[], Any] | None = None) -> None:
        self._workspace_provider = workspace_provider

    def detect(self) -> FrontmostApplication:
        try:
            workspace = self._workspace()
            application = workspace.frontmostApplication()
            if application is None:
                return FrontmostApplication(FrontmostApplicationKind.UNAVAILABLE)
            bundle_identifier = application.bundleIdentifier()
            if not isinstance(bundle_identifier, str) or not bundle_identifier:
                return FrontmostApplication(FrontmostApplicationKind.UNAVAILABLE)
        except Exception as exc:
            log_event(
                LOGGER,
                logging.WARNING,
                "destination.frontmost_detection_failed",
                "macOS frontmost application could not be determined",
                host_platform="macos",
                operation="destination_detection",
                **exception_fields(exc),
            )
            return FrontmostApplication(FrontmostApplicationKind.UNAVAILABLE)
        kind = (
            FrontmostApplicationKind.T3_CODE
            if bundle_identifier == T3_CODE_BUNDLE_IDENTIFIER
            else FrontmostApplicationKind.OTHER
        )
        return FrontmostApplication(kind, bundle_identifier)

    def _workspace(self) -> Any:
        if self._workspace_provider is not None:
            return self._workspace_provider()
        from AppKit import NSWorkspace

        return NSWorkspace.sharedWorkspace()


@dataclass(frozen=True, slots=True)
class _Descriptor:
    path: Path
    socket_path: Path
    secret: str
    owner: int
    descriptor_device: int
    descriptor_inode: int
    descriptor_fingerprint: bytes
    socket_device: int
    socket_inode: int


@dataclass(frozen=True, slots=True)
class _Session:
    descriptor: _Descriptor


class _LocalValidationError(Exception):
    pass


class _AmbiguousResponseError(Exception):
    pass


_PROCESS_REQUEST_IDENTIFIER_INSTANCES: weakref.WeakSet[_ProcessRequestIdentifiers] = (
    weakref.WeakSet()
)


def _reset_process_request_identifier_locks_after_fork() -> None:
    # Reset the process-global generator first: request generation must not depend on
    # WeakSet iteration order, and one broken auxiliary instance must not leave the
    # shared generator with a lock inherited from a vanished parent thread.
    global_identifiers = _PROCESS_REQUEST_IDENTIFIERS
    try:  # noqa: SIM105 - avoid allocating a suppress context after fork
        global_identifiers._reset_lock_after_fork()
    except BaseException:
        pass

    # A post-fork callback cannot safely report failures. Isolate every entry so a
    # faulty test/custom generator cannot prevent later instances from being reset.
    for identifiers in _PROCESS_REQUEST_IDENTIFIER_INSTANCES:
        if identifiers is global_identifiers:
            continue
        try:  # noqa: SIM105 - isolate entries without a post-fork context object
            identifiers._reset_lock_after_fork()
        except BaseException:
            pass


class _ProcessRequestIdentifiers:
    """Generate bounded request identifiers without retaining request history."""

    def __init__(self, prefix_factory: Callable[[], str] | None = None) -> None:
        self._prefix_factory = prefix_factory or (lambda: uuid.uuid4().hex)
        self._lock = threading.Lock()
        self._pid = os.getpid()
        self._prefix = self._new_prefix()
        self._counter = 0
        _PROCESS_REQUEST_IDENTIFIER_INSTANCES.add(self)

    def next(self) -> str:
        with self._lock:
            current_pid = os.getpid()
            if current_pid != self._pid:
                self._pid = current_pid
                self._prefix = self._new_prefix()
                self._counter = 0
            self._counter += 1
            request_id = f"{self._prefix}:{self._counter}"
            _validate_identifier(request_id)
            return request_id

    def _new_prefix(self) -> str:
        prefix = self._prefix_factory()
        _validate_identifier(prefix)
        return prefix

    def _reset_lock_after_fork(self) -> None:
        self._lock = threading.Lock()


class T3CodeBridge:
    def __init__(
        self,
        descriptor_path: Path | str | None = None,
        *,
        connect_write_timeout_seconds: float = DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS,
        response_timeout_seconds: float = DEFAULT_RESPONSE_TIMEOUT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] | None = None,
        current_uid: Callable[[], int] = os.getuid,
    ) -> None:
        self._descriptor_path = (
            _default_descriptor_path()
            if descriptor_path is None
            else Path(descriptor_path).expanduser()
        )
        self._connect_write_timeout_seconds = _bounded_timeout(
            connect_write_timeout_seconds,
            maximum=DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS,
        )
        self._response_timeout_seconds = _bounded_timeout(
            response_timeout_seconds,
            maximum=DEFAULT_RESPONSE_TIMEOUT_SECONDS,
        )
        self._monotonic = monotonic
        self._wall_clock = wall_clock
        self._nonce_factory = id_factory or (lambda: str(uuid.uuid4()))
        self._current_uid = current_uid
        self._lock = threading.RLock()
        self._sessions: dict[str, _Session] = {}
        self._reservations_in_progress: set[str] = set()

    def reserve_recording(self, recording_id: str) -> BridgeResult:
        registered = False
        try:
            _validate_recording_id(recording_id)
            with self._lock:
                if recording_id in self._sessions or recording_id in self._reservations_in_progress:
                    raise _LocalValidationError
                self._reservations_in_progress.add(recording_id)
                registered = True
            descriptor = self._load_descriptor()
            request = self._new_request(
                request_type="recording.started",
                recording_id=recording_id,
                secret=descriptor.secret,
            )
            result = self._exchange(descriptor, request)
            if result.accepted:
                with self._lock:
                    self._sessions[recording_id] = _Session(descriptor)
            self._log_result("recording.started", recording_id, result)
            return result
        except _LocalValidationError as exc:
            result = BridgeResult.validation_error()
            self._log_failure("recording.started", recording_id, result, exc)
            return result
        except _AmbiguousResponseError as exc:
            result = BridgeResult.ambiguous()
            self._log_failure("recording.started", recording_id, result, exc)
            return result
        except Exception as exc:
            result = BridgeResult.validation_error()
            self._log_failure(
                "recording.started",
                recording_id,
                result,
                exc,
                unexpected=True,
            )
            return result
        finally:
            if registered:
                with self._lock:
                    self._reservations_in_progress.discard(recording_id)

    def deliver_transcription(
        self,
        recording_id: str,
        transcript: str,
    ) -> BridgeResult:
        with self._lock:
            session = self._sessions.pop(recording_id, None)
        if session is None:
            result = BridgeResult.validation_error(session_changed=True)
            log_event(
                LOGGER,
                logging.WARNING,
                "integration.t3.failed",
                "T3 Code integration session is unavailable",
                operation="transcription.completed",
                integration="t3_code",
                recording_id=recording_id,
                outcome=result.outcome,
                error_code=result.error_code,
                reason="missing_or_already_consumed_session",
            )
            return result

        try:
            _validate_recording_id(recording_id)
            _validate_transcript(transcript)
            self._verify_session(session)
            request = self._new_request(
                request_type="transcription.completed",
                recording_id=recording_id,
                secret=session.descriptor.secret,
                transcript=transcript,
            )
            result = self._exchange(session.descriptor, request)
            self._log_result("transcription.completed", recording_id, result)
            return result
        except _SessionChangedError as exc:
            result = BridgeResult.validation_error(session_changed=True)
            self._log_failure("transcription.completed", recording_id, result, exc)
            return result
        except _LocalValidationError as exc:
            result = BridgeResult.validation_error()
            self._log_failure("transcription.completed", recording_id, result, exc)
            return result
        except _AmbiguousResponseError as exc:
            result = BridgeResult.ambiguous()
            self._log_failure("transcription.completed", recording_id, result, exc)
            return result
        except Exception as exc:
            result = BridgeResult.validation_error()
            self._log_failure(
                "transcription.completed",
                recording_id,
                result,
                exc,
                unexpected=True,
            )
            return result

    def abandon_recording(self, recording_id: str) -> None:
        with self._lock:
            removed = self._sessions.pop(recording_id, None)
        if removed is not None:
            log_event(
                LOGGER,
                logging.DEBUG,
                "integration.t3.abandoned",
                "T3 Code integration session abandoned",
                operation="recording.abandoned",
                integration="t3_code",
                recording_id=recording_id,
            )

    def _log_result(
        self,
        operation: str,
        recording_id: str,
        result: BridgeResult,
    ) -> None:
        log_event(
            LOGGER,
            logging.DEBUG if result.accepted else logging.WARNING,
            "integration.t3.result",
            "T3 Code integration request completed",
            operation=operation,
            integration="t3_code",
            recording_id=recording_id,
            outcome=result.outcome,
            error_code=result.error_code,
            remote_code=result.remote_code,
        )

    def _log_failure(
        self,
        operation: str,
        recording_id: str,
        result: BridgeResult,
        exc: BaseException,
        *,
        unexpected: bool = False,
    ) -> None:
        log_event(
            LOGGER,
            logging.ERROR if unexpected else logging.WARNING,
            "integration.t3.failed",
            "T3 Code integration request failed",
            operation=operation,
            integration="t3_code",
            recording_id=recording_id,
            outcome=result.outcome,
            error_code=result.error_code,
            exc_info=unexpected,
            **exception_fields(exc),
        )

    def _load_descriptor(self) -> _Descriptor:
        descriptor_bytes, descriptor_stat = self._secure_read_descriptor(self._descriptor_path)
        try:
            data = _decode_json_object(descriptor_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError) as exc:
            raise _LocalValidationError from exc
        if set(data) != _DESCRIPTOR_KEYS:
            raise _LocalValidationError
        if (
            type(data["version"]) is not int
            or data["version"] != PROTOCOL_VERSION
            or data["transport"] != TRANSPORT
        ):
            raise _LocalValidationError
        secret = data["secret"]
        socket_value = data["socketPath"]
        pid = data["pid"]
        if not isinstance(secret, str) or not secret or len(secret) > 4096:
            raise _LocalValidationError
        if not isinstance(socket_value, str) or not socket_value:
            raise _LocalValidationError
        if type(pid) is not int or pid <= 0:
            raise _LocalValidationError
        socket_path = Path(socket_value)
        if not socket_path.is_absolute():
            raise _LocalValidationError
        if ".." in socket_path.parts:
            raise _LocalValidationError
        socket_stat = self._validate_socket_path(socket_path, descriptor_stat.st_uid)
        return _Descriptor(
            path=self._descriptor_path,
            socket_path=socket_path,
            secret=secret,
            owner=descriptor_stat.st_uid,
            descriptor_device=descriptor_stat.st_dev,
            descriptor_inode=descriptor_stat.st_ino,
            descriptor_fingerprint=hashlib.sha256(descriptor_bytes).digest(),
            socket_device=socket_stat.st_dev,
            socket_inode=socket_stat.st_ino,
        )

    def _secure_read_descriptor(self, path: Path) -> tuple[bytes, os.stat_result]:
        flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            descriptor_fd = os.open(path, flags)
        except OSError as exc:
            raise _LocalValidationError from exc
        try:
            descriptor_stat = os.fstat(descriptor_fd)
            if not stat.S_ISREG(descriptor_stat.st_mode):
                raise _LocalValidationError
            if descriptor_stat.st_uid != self._current_uid():
                raise _LocalValidationError
            if descriptor_stat.st_mode & 0o077:
                raise _LocalValidationError
            if descriptor_stat.st_size <= 0 or descriptor_stat.st_size > MAX_DESCRIPTOR_BYTES:
                raise _LocalValidationError
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
                raise _LocalValidationError
            return descriptor_bytes, descriptor_stat
        finally:
            os.close(descriptor_fd)

    def _validate_socket_path(self, socket_path: Path, expected_owner: int) -> os.stat_result:
        descriptor_directory = self._descriptor_path.parent
        try:
            lexical_socket = Path(os.path.abspath(socket_path))
            lexical_directory = Path(os.path.abspath(descriptor_directory))
            if os.path.commonpath((lexical_socket, lexical_directory)) != str(lexical_directory):
                raise _LocalValidationError
            relative = lexical_socket.relative_to(lexical_directory)
            if ".." in relative.parts:
                raise _LocalValidationError
            resolved_directory = descriptor_directory.resolve(strict=True)
            resolved_socket = socket_path.resolve(strict=True)
            if resolved_socket.parent != resolved_directory:
                raise _LocalValidationError
            socket_stat = os.lstat(socket_path)
        except (OSError, ValueError) as exc:
            raise _LocalValidationError from exc
        if stat.S_ISLNK(socket_stat.st_mode) or not stat.S_ISSOCK(socket_stat.st_mode):
            raise _LocalValidationError
        if socket_stat.st_uid != expected_owner:
            raise _LocalValidationError
        return socket_stat

    def _verify_session(self, session: _Session) -> None:
        descriptor = session.descriptor
        try:
            current_bytes, current_stat = self._secure_read_descriptor(descriptor.path)
            current_socket = self._validate_socket_path(descriptor.socket_path, descriptor.owner)
        except _LocalValidationError:
            raise _SessionChangedError from None
        if (
            current_stat.st_dev != descriptor.descriptor_device
            or current_stat.st_ino != descriptor.descriptor_inode
            or hashlib.sha256(current_bytes).digest() != descriptor.descriptor_fingerprint
            or current_socket.st_dev != descriptor.socket_device
            or current_socket.st_ino != descriptor.socket_inode
        ):
            raise _SessionChangedError

    def _new_request(
        self,
        *,
        request_type: str,
        recording_id: str,
        secret: str,
        transcript: str | None = None,
    ) -> dict[str, object]:
        request_id = _PROCESS_REQUEST_IDENTIFIERS.next()
        nonce = self._nonce_factory()
        _validate_identifier(request_id)
        _validate_identifier(nonce)
        if request_id == nonce:
            raise _LocalValidationError
        request: dict[str, object] = {
            "version": PROTOCOL_VERSION,
            "type": request_type,
            "requestId": request_id,
            "recordingId": recording_id,
            "nonce": nonce,
            "sentAt": int(self._wall_clock() * 1000),
            "auth": secret,
        }
        if transcript is not None:
            request["transcript"] = transcript
        return request

    def _exchange(
        self,
        descriptor: _Descriptor,
        request: Mapping[str, object],
    ) -> BridgeResult:
        try:
            frame = (
                json.dumps(
                    request,
                    ensure_ascii=False,
                    allow_nan=False,
                    separators=(",", ":"),
                ).encode("utf-8")
                + b"\n"
            )
        except (TypeError, ValueError, UnicodeError) as exc:
            raise _LocalValidationError from exc
        if len(frame) >= MAX_REQUEST_FRAME_BYTES:
            raise _LocalValidationError

        connect_write_deadline = self._monotonic() + self._connect_write_timeout_seconds
        client: socket.socket | None = None
        try:
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.settimeout(self._remaining(connect_write_deadline))
            client.connect(str(descriptor.socket_path))
            self._verify_socket_identity(descriptor)
            client.settimeout(self._remaining(connect_write_deadline))
            client.sendall(frame)
            response_deadline = self._monotonic() + self._response_timeout_seconds
            response_frame = self._read_response(client, response_deadline)
        except OSError as exc:
            raise _AmbiguousResponseError("socket_io_or_timeout") from exc
        finally:
            if client is not None:
                client.close()
        return self._validate_response(response_frame, request)

    def _remaining(self, deadline: float) -> float:
        remaining = deadline - self._monotonic()
        if remaining <= 0:
            raise TimeoutError
        return remaining

    def _verify_socket_identity(self, descriptor: _Descriptor) -> None:
        try:
            socket_stat = os.lstat(descriptor.socket_path)
        except OSError as exc:
            raise _AmbiguousResponseError from exc
        if (
            stat.S_ISLNK(socket_stat.st_mode)
            or not stat.S_ISSOCK(socket_stat.st_mode)
            or socket_stat.st_uid != descriptor.owner
            or socket_stat.st_dev != descriptor.socket_device
            or socket_stat.st_ino != descriptor.socket_inode
        ):
            raise _AmbiguousResponseError

    def _read_response(self, client: socket.socket, deadline: float) -> bytes:
        response = bytearray()
        while True:
            client.settimeout(self._remaining(deadline))
            chunk = client.recv(min(4096, MAX_RESPONSE_FRAME_BYTES - len(response) + 1))
            if not chunk:
                raise _AmbiguousResponseError
            newline = chunk.find(b"\n")
            if newline >= 0:
                response.extend(chunk[:newline])
                if newline != len(chunk) - 1:
                    raise _AmbiguousResponseError
                if len(response) >= MAX_RESPONSE_FRAME_BYTES:
                    raise _AmbiguousResponseError
                return bytes(response)
            response.extend(chunk)
            if len(response) >= MAX_RESPONSE_FRAME_BYTES:
                raise _AmbiguousResponseError

    def _validate_response(
        self,
        response_frame: bytes,
        request: Mapping[str, object],
    ) -> BridgeResult:
        try:
            response = _decode_json_object(response_frame)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
            raise _AmbiguousResponseError from None
        keys = set(response)
        if keys != _RESPONSE_KEYS:
            raise _AmbiguousResponseError
        if (
            type(response["version"]) is not int
            or response["version"] != PROTOCOL_VERSION
            or response.get("requestId") != request["requestId"]
            or type(response["accepted"]) is not bool
        ):
            raise _AmbiguousResponseError
        code = response["code"]
        if not isinstance(code, str) or _ID_PATTERN.fullmatch(code) is None:
            raise _AmbiguousResponseError
        if response["accepted"] != (code == "accepted"):
            raise _AmbiguousResponseError
        if response["accepted"]:
            return BridgeResult.accepted_result()
        return BridgeResult.rejected(remote_code=code)


class _SessionChangedError(_LocalValidationError):
    pass


def _default_descriptor_path() -> Path:
    configured_home = os.environ.get("T3CODE_HOME", "").strip()
    t3_code_home = Path(configured_home).expanduser() if configured_home else DEFAULT_T3_CODE_HOME
    return t3_code_home / DESCRIPTOR_RELATIVE_PATH


def _bounded_timeout(value: float, *, maximum: float) -> float:
    timeout = float(value)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    return min(timeout, maximum)


def _decode_json_object(data: bytes) -> dict[str, Any]:
    def object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=object_pairs,
        parse_constant=lambda _value: (_ for _ in ()).throw(ValueError("non-standard JSON value")),
    )
    if not isinstance(value, dict):
        raise TypeError("JSON object required")
    return value


def _validate_identifier(value: str) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise _LocalValidationError


_PROCESS_REQUEST_IDENTIFIERS = _ProcessRequestIdentifiers()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process_request_identifier_locks_after_fork)


def _validate_recording_id(recording_id: str) -> None:
    _validate_identifier(recording_id)
    try:
        parsed = uuid.UUID(recording_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise _LocalValidationError from exc
    if parsed.version != 4 or str(parsed) != recording_id.lower():
        raise _LocalValidationError


def _validate_transcript(transcript: str) -> None:
    if (
        not isinstance(transcript, str)
        or not transcript.strip()
        or len(transcript) > MAX_TRANSCRIPT_CHARACTERS
    ):
        raise _LocalValidationError
