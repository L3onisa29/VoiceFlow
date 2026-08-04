from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from voiceflow.integrations import BridgeResult as PackageBridgeResult
from voiceflow.integrations.t3_code_bridge import BridgeResult as BridgeBridgeResult
from voiceflow.integrations.t3_descriptor import Descriptor
from voiceflow.integrations.t3_protocol import (
    MAX_REQUEST_FRAME_BYTES,
    AmbiguousResponseError,
    BridgeOutcome,
    BridgeResult,
    LocalValidationError,
    encode_request,
    validate_response,
)
from voiceflow.integrations.t3_transport import SocketTransport

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "t3_protocol_golden.json"
RECORDING_ID = "0ded863b-e851-4c21-ab6c-a1a2525b8ee2"


class FakeSocket:
    def __init__(self, response: bytes = b"") -> None:
        self.response = response
        self.sent = b""
        self.timeouts: list[float] = []
        self.connected_to: str | None = None
        self.closed = False

    def settimeout(self, value: float | None, /) -> None:
        assert value is not None
        self.timeouts.append(value)

    def connect(self, address: Any, /) -> None:
        self.connected_to = str(address)

    def sendall(self, data: Any, flags: int = 0, /) -> None:
        del flags
        self.sent = bytes(data)

    def recv(self, _size: int, _flags: int = 0, /) -> bytes:
        response, self.response = self.response, b""
        return response

    def close(self) -> None:
        self.closed = True


def descriptor() -> Descriptor:
    return Descriptor(
        path=Path("/private/tmp/bridge.json"),
        socket_path=Path("/private/tmp/bridge.sock"),
        secret="secret",
        owner=os.getuid(),
        descriptor_device=1,
        descriptor_inode=2,
        descriptor_fingerprint=b"fingerprint",
        socket_device=1,
        socket_inode=3,
    )


def request() -> dict[str, object]:
    return {
        "version": 1,
        "type": "transcription.completed",
        "requestId": "golden-prefix:7",
        "recordingId": RECORDING_ID,
        "nonce": "golden-nonce",
        "sentAt": 1_234_500,
        "auth": "sècret🙂",
        "transcript": "Caffè 東京 🙂",
    }


def test_protocol_boundary_matches_frozen_monolith_bytes() -> None:
    golden = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert encode_request(request()) == golden["request_frame"].encode("utf-8")
    assert (
        validate_response(golden["accepted_response"].encode("utf-8"), request())
        == BridgeResult.accepted_result()
    )


def test_protocol_boundary_preserves_rejection_and_size_contract() -> None:
    rejected = (
        b'{"version":1,"requestId":"golden-prefix:7",'
        b'"accepted":false,"code":"authentication_failed"}'
    )
    assert validate_response(rejected, request()) == BridgeResult.rejected(
        remote_code="authentication_failed"
    )
    with pytest.raises(LocalValidationError):
        encode_request({"requestId": "x", "auth": "x" * MAX_REQUEST_FRAME_BYTES})
    with pytest.raises(AmbiguousResponseError):
        validate_response(rejected + b"\n{}", request())


def test_transport_contract_writes_once_and_uses_separate_deadlines() -> None:
    response = b'{"version":1,"requestId":"golden-prefix:7","accepted":true,"code":"accepted"}\n'
    fake = FakeSocket(response)
    clock_values = iter((10.0, 10.25, 10.5, 11.0, 11.25))
    transport = SocketTransport(
        connect_write_timeout_seconds=2.0,
        response_timeout_seconds=6.0,
        monotonic=lambda: next(clock_values),
        socket_factory=lambda _family, _kind: fake,
        identity_verifier=lambda _descriptor: None,
    )

    result = transport.exchange(descriptor(), request())

    assert result.outcome == BridgeOutcome.ACCEPTED
    assert fake.sent == encode_request(request())
    assert fake.connected_to == "/private/tmp/bridge.sock"
    assert fake.closed
    assert fake.timeouts == [1.75, 1.5, 5.75]


def test_transport_contract_never_retries_after_socket_error() -> None:
    attempts = 0

    def fail_socket(_family: int, _kind: int) -> Any:
        nonlocal attempts
        attempts += 1
        raise OSError("unavailable")

    transport = SocketTransport(
        connect_write_timeout_seconds=2.0,
        response_timeout_seconds=6.0,
        monotonic=lambda: 10.0,
        socket_factory=fail_socket,
    )

    with pytest.raises(AmbiguousResponseError):
        transport.exchange(descriptor(), request())
    assert attempts == 1


@pytest.mark.parametrize(
    "order",
    [
        ("t3_protocol", "t3_descriptor", "t3_transport", "t3_code_bridge"),
        ("t3_descriptor", "t3_transport", "t3_protocol", "t3_code_bridge"),
        ("t3_transport", "t3_code_bridge", "t3_descriptor", "t3_protocol"),
    ],
)
def test_t3_modules_import_in_any_order_without_native_initialization(
    order: tuple[str, ...],
) -> None:
    imports = ";".join(f"import voiceflow.integrations.{module}" for module in order)
    script = (
        f"{imports};import sys;"
        "assert 'AppKit' not in sys.modules;"
        "assert 'AVFoundation' not in sys.modules;"
        "assert 'Quartz' not in sys.modules"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=Path(__file__).parents[1],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_public_result_type_is_reexported_without_duplication() -> None:
    assert PackageBridgeResult is BridgeBridgeResult is BridgeResult
