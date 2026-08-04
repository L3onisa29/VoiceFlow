from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest

from voiceflow.integrations.t3_code_bridge import (
    MAX_REQUEST_FRAME_BYTES,
    MAX_TRANSCRIPT_CHARACTERS,
    BridgeOutcome,
    T3CodeBridge,
    _AmbiguousResponseError,
    _Descriptor,
    _LocalValidationError,
    _validate_transcript,
)

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "t3_protocol_golden.json"
RECORDING_ID = "0ded863b-e851-4c21-ab6c-a1a2525b8ee2"


class _CapturingSocket:
    def __init__(self) -> None:
        self.sent = b""
        self.timeouts: list[float] = []
        self.connected_to: str | None = None
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        self.timeouts.append(timeout)

    def connect(self, path: str) -> None:
        self.connected_to = path

    def sendall(self, frame: bytes) -> None:
        self.sent = frame

    def close(self) -> None:
        self.closed = True


class _GoldenBridge(T3CodeBridge):
    def _verify_socket_identity(self, descriptor: _Descriptor) -> None:
        del descriptor

    def _read_response(self, client: Any, deadline: float) -> bytes:
        del client, deadline
        return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["accepted_response"].encode(
            "utf-8"
        )


def _descriptor() -> _Descriptor:
    return _Descriptor(
        path=Path("/private/tmp/bridge.json"),
        socket_path=Path("/private/tmp/bridge.sock"),
        secret="sècret🙂",
        owner=501,
        descriptor_device=1,
        descriptor_inode=2,
        descriptor_fingerprint=b"fingerprint",
        socket_device=1,
        socket_inode=3,
    )


def _request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "version": 1,
        "type": "transcription.completed",
        "requestId": "golden-prefix:7",
        "recordingId": RECORDING_ID,
        "nonce": "golden-nonce",
        "sentAt": 1_234_500,
        "auth": "sècret🙂",
        "transcript": "Caffè 東京 🙂",
    }
    request.update(overrides)
    return request


def test_monolith_request_bytes_match_frozen_unicode_fixture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capturing_socket = _CapturingSocket()
    monkeypatch.setattr(
        "voiceflow.integrations.t3_code_bridge.socket.socket",
        lambda *_args: capturing_socket,
    )
    bridge = _GoldenBridge(monotonic=lambda: 10.0)

    result = bridge._exchange(_descriptor(), _request())

    golden = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    assert capturing_socket.sent == golden["request_frame"].encode("utf-8")
    assert capturing_socket.connected_to == "/private/tmp/bridge.sock"
    assert capturing_socket.closed
    assert result.outcome == BridgeOutcome.ACCEPTED


def test_monolith_protocol_preserves_order_and_rejects_nonstandard_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    capturing_socket = _CapturingSocket()
    monkeypatch.setattr(
        "voiceflow.integrations.t3_code_bridge.socket.socket",
        lambda *_args: capturing_socket,
    )
    bridge = _GoldenBridge(monotonic=lambda: 10.0)

    reordered: dict[str, object] = {
        "auth": "secret",
        "version": 1,
        "requestId": "golden-prefix:7",
    }
    assert bridge._exchange(_descriptor(), reordered).accepted
    assert capturing_socket.sent == (
        b'{"auth":"secret","version":1,"requestId":"golden-prefix:7"}\n'
    )

    with pytest.raises(_LocalValidationError):
        bridge._exchange(_descriptor(), _request(sentAt=float("nan")))


def test_monolith_protocol_freezes_size_boundaries() -> None:
    bridge = _GoldenBridge(monotonic=lambda: 10.0)
    base: Mapping[str, object] = {"requestId": "golden-prefix:7", "auth": ""}
    overhead = len(json.dumps(base, separators=(",", ":")).encode("utf-8")) + 1

    accepted = dict(base, auth="x" * (MAX_REQUEST_FRAME_BYTES - overhead - 1))
    rejected = dict(base, auth="x" * (MAX_REQUEST_FRAME_BYTES - overhead))
    assert len(json.dumps(accepted, separators=(",", ":")).encode("utf-8")) + 1 == (
        MAX_REQUEST_FRAME_BYTES - 1
    )
    assert len(json.dumps(rejected, separators=(",", ":")).encode("utf-8")) + 1 == (
        MAX_REQUEST_FRAME_BYTES
    )

    _validate_transcript("x" * MAX_TRANSCRIPT_CHARACTERS)
    with pytest.raises(_LocalValidationError):
        _validate_transcript("x" * (MAX_TRANSCRIPT_CHARACTERS + 1))
    with pytest.raises(_LocalValidationError):
        bridge._exchange(_descriptor(), rejected)


@pytest.mark.parametrize(
    "response",
    [
        b'{"version":1,"requestId":"golden-prefix:7","accepted":true,"code":"accepted","code":"accepted"}',
        b'{"version":1,"requestId":"golden-prefix:7","accepted":true,"code":"rejected"}',
        b'{"version":1,"requestId":"wrong","accepted":true,"code":"accepted"}',
        b'{"version":NaN,"requestId":"golden-prefix:7","accepted":true,"code":"accepted"}',
    ],
)
def test_monolith_response_validation_is_fail_closed(response: bytes) -> None:
    bridge = T3CodeBridge()
    with pytest.raises(_AmbiguousResponseError):
        bridge._validate_response(response, _request())
