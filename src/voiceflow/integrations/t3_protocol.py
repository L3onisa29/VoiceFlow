from __future__ import annotations

import json
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

PROTOCOL_VERSION = 1
TRANSPORT = "unix"
MAX_TRANSCRIPT_CHARACTERS = 32_768
MAX_REQUEST_FRAME_BYTES = 64 * 1024
MAX_RESPONSE_FRAME_BYTES = 64 * 1024

_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_RESPONSE_KEYS = frozenset({"version", "requestId", "accepted", "code"})


class LocalValidationError(Exception):
    """A request or local integration value violates the T3 contract."""


class AmbiguousResponseError(Exception):
    """The bridge cannot prove whether the peer accepted a request."""


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


def encode_request(request: Mapping[str, object]) -> bytes:
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
        raise LocalValidationError from exc
    if len(frame) >= MAX_REQUEST_FRAME_BYTES:
        raise LocalValidationError
    return frame


def validate_response(
    response_frame: bytes,
    request: Mapping[str, object],
) -> BridgeResult:
    try:
        response = decode_json_object(response_frame)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, TypeError):
        raise AmbiguousResponseError from None
    if set(response) != _RESPONSE_KEYS:
        raise AmbiguousResponseError
    if (
        type(response["version"]) is not int
        or response["version"] != PROTOCOL_VERSION
        or response.get("requestId") != request["requestId"]
        or type(response["accepted"]) is not bool
    ):
        raise AmbiguousResponseError
    code = response["code"]
    if not isinstance(code, str) or _ID_PATTERN.fullmatch(code) is None:
        raise AmbiguousResponseError
    if response["accepted"] != (code == "accepted"):
        raise AmbiguousResponseError
    if response["accepted"]:
        return BridgeResult.accepted_result()
    return BridgeResult.rejected(remote_code=code)


def decode_json_object(data: bytes) -> dict[str, Any]:
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


def validate_identifier(value: str) -> None:
    if not isinstance(value, str) or _ID_PATTERN.fullmatch(value) is None:
        raise LocalValidationError


def validate_recording_id(recording_id: str) -> None:
    validate_identifier(recording_id)
    try:
        parsed = uuid.UUID(recording_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise LocalValidationError from exc
    if parsed.version != 4 or str(parsed) != recording_id.lower():
        raise LocalValidationError


def validate_transcript(transcript: str) -> None:
    if (
        not isinstance(transcript, str)
        or not transcript.strip()
        or len(transcript) > MAX_TRANSCRIPT_CHARACTERS
    ):
        raise LocalValidationError
