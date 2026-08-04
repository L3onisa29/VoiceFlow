from __future__ import annotations

import logging
import os
import socket
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
from voiceflow.integrations.t3_descriptor import (
    DEFAULT_DESCRIPTOR_PATH,
    DEFAULT_T3_CODE_HOME,
    DESCRIPTOR_RELATIVE_PATH,
    MAX_DESCRIPTOR_BYTES,
    Descriptor,
    DescriptorLoader,
    SessionChangedError,
    default_descriptor_path,
)
from voiceflow.integrations.t3_protocol import (
    MAX_REQUEST_FRAME_BYTES,
    MAX_RESPONSE_FRAME_BYTES,
    MAX_TRANSCRIPT_CHARACTERS,
    PROTOCOL_VERSION,
    TRANSPORT,
    AmbiguousResponseError,
    BridgeOutcome,
    BridgeResult,
    LocalValidationError,
    decode_json_object,
    validate_identifier,
    validate_recording_id,
    validate_response,
    validate_transcript,
)
from voiceflow.integrations.t3_transport import (
    DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS,
    DEFAULT_RESPONSE_TIMEOUT_SECONDS,
    SocketPort,
    SocketTransport,
    bounded_timeout,
    read_response,
    remaining_timeout,
    verify_socket_identity,
)
from voiceflow.system.text_injector import InjectionDestination

LOGGER = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_CONNECT_WRITE_TIMEOUT_SECONDS",
    "DEFAULT_DESCRIPTOR_PATH",
    "DEFAULT_RESPONSE_TIMEOUT_SECONDS",
    "DEFAULT_T3_CODE_HOME",
    "DESCRIPTOR_RELATIVE_PATH",
    "MAX_DESCRIPTOR_BYTES",
    "MAX_REQUEST_FRAME_BYTES",
    "MAX_RESPONSE_FRAME_BYTES",
    "MAX_TRANSCRIPT_CHARACTERS",
    "PROTOCOL_VERSION",
    "T3_CODE_BUNDLE_IDENTIFIER",
    "TRANSPORT",
    "BridgeOutcome",
    "BridgeResult",
    "DictationDestination",
    "FrontmostApplication",
    "FrontmostApplicationKind",
    "MacOSFrontmostApplicationDetector",
    "T3CodeBridge",
    "T3CodeDestination",
]

T3_CODE_BUNDLE_IDENTIFIER = "com.t3tools.t3code"


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


# Compatibility aliases retained for callers and tests that imported the old
# monolithic module's private contract helpers.
_Descriptor = Descriptor
_LocalValidationError = LocalValidationError
_AmbiguousResponseError = AmbiguousResponseError
_SessionChangedError = SessionChangedError


@dataclass(frozen=True, slots=True)
class _Session:
    descriptor: Descriptor


_PROCESS_REQUEST_IDENTIFIER_INSTANCES: weakref.WeakSet[_ProcessRequestIdentifiers] = (
    weakref.WeakSet()
)


def _reset_process_request_identifier_locks_after_fork() -> None:
    global_identifiers = _PROCESS_REQUEST_IDENTIFIERS
    try:  # noqa: SIM105 - avoid allocating a suppress context after fork
        global_identifiers._reset_lock_after_fork()
    except BaseException:
        pass

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
        self._descriptor_loader = DescriptorLoader(
            self._descriptor_path,
            current_uid=current_uid,
        )
        self._transport = SocketTransport(
            connect_write_timeout_seconds=self._connect_write_timeout_seconds,
            response_timeout_seconds=self._response_timeout_seconds,
            monotonic=monotonic,
            socket_factory=lambda family, kind: socket.socket(family, kind),
            identity_verifier=self._verify_socket_identity,
            response_reader=self._read_response,
        )
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

    def _load_descriptor(self) -> Descriptor:
        return self._descriptor_loader.load()

    def _secure_read_descriptor(self, path: Path) -> tuple[bytes, os.stat_result]:
        return self._descriptor_loader.secure_read(path)

    def _validate_socket_path(
        self,
        socket_path: Path,
        expected_owner: int,
    ) -> os.stat_result:
        return self._descriptor_loader.validate_socket_path(socket_path, expected_owner)

    def _verify_session(self, session: _Session) -> None:
        self._descriptor_loader.verify_session(session.descriptor)

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
        descriptor: Descriptor,
        request: Mapping[str, object],
    ) -> BridgeResult:
        return self._transport.exchange(descriptor, request)

    def _remaining(self, deadline: float) -> float:
        return remaining_timeout(deadline, self._monotonic)

    def _verify_socket_identity(self, descriptor: Descriptor) -> None:
        verify_socket_identity(descriptor)

    def _read_response(self, client: SocketPort, deadline: float) -> bytes:
        return read_response(client, deadline, self._monotonic)

    def _validate_response(
        self,
        response_frame: bytes,
        request: Mapping[str, object],
    ) -> BridgeResult:
        return validate_response(response_frame, request)


def _default_descriptor_path() -> Path:
    return default_descriptor_path()


def _bounded_timeout(value: float, *, maximum: float) -> float:
    return bounded_timeout(value, maximum=maximum)


def _decode_json_object(data: bytes) -> dict[str, Any]:
    return decode_json_object(data)


def _validate_identifier(value: str) -> None:
    validate_identifier(value)


_PROCESS_REQUEST_IDENTIFIERS = _ProcessRequestIdentifiers()
if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_process_request_identifier_locks_after_fork)


def _validate_recording_id(recording_id: str) -> None:
    validate_recording_id(recording_id)


def _validate_transcript(transcript: str) -> None:
    validate_transcript(transcript)
