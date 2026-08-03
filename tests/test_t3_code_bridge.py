from __future__ import annotations

import gc
import json
import os
import select
import signal
import socket
import threading
import time
import uuid
import weakref
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any

import pytest

import voiceflow.integrations.t3_code_bridge as t3_bridge_module
from voiceflow.integrations import (
    BridgeOutcome,
    FrontmostApplicationKind,
    MacOSFrontmostApplicationDetector,
    T3CodeBridge,
)
from voiceflow.integrations.t3_code_bridge import (
    DEFAULT_DESCRIPTOR_PATH,
    MAX_DESCRIPTOR_BYTES,
    MAX_RESPONSE_FRAME_BYTES,
    T3_CODE_BUNDLE_IDENTIFIER,
    _LocalValidationError,
    _ProcessRequestIdentifiers,
)

ResponseFactory = Callable[[dict[str, Any]], bytes | None]
RECORDING_ID = "0ded863b-e851-4c21-ab6c-a1a2525b8ee2"
SECOND_RECORDING_ID = "1ca26cc1-37d0-451f-81cb-567d57df15ac"
SECRET = "test-secret-never-log"


@pytest.fixture
def bridge_dir(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    actual_directory = tmp_path_factory.mktemp("t3")
    alias = Path("/tmp") / f"voicebud-{uuid.uuid4().hex[:8]}"
    alias.symlink_to(actual_directory, target_is_directory=True)
    try:
        yield alias
    finally:
        alias.unlink(missing_ok=True)


class UnixTestServer:
    def __init__(
        self,
        path: Path,
        responses: list[ResponseFactory],
    ) -> None:
        self.path = path
        self.responses = responses
        self.requests: list[dict[str, Any]] = []
        self.raw_requests: list[bytes] = []
        self.errors: list[BaseException] = []
        self._socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._socket.bind(str(path))
        self._socket.listen()
        self._socket.settimeout(3)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            for response_factory in self.responses:
                connection, _address = self._socket.accept()
                with connection:
                    connection.settimeout(2)
                    raw = bytearray()
                    while not raw.endswith(b"\n"):
                        chunk = connection.recv(4096)
                        if not chunk:
                            break
                        raw.extend(chunk)
                    self.raw_requests.append(bytes(raw))
                    request = json.loads(raw.decode("utf-8"))
                    self.requests.append(request)
                    response = response_factory(request)
                    if response is not None:
                        with suppress(BrokenPipeError):
                            connection.sendall(response)
        except BaseException as exc:
            self.errors.append(exc)
        finally:
            self._socket.close()

    def finish(self) -> None:
        self._thread.join(timeout=4)
        assert not self._thread.is_alive()
        assert self.errors == []


@contextmanager
def server(
    path: Path,
    *responses: ResponseFactory,
) -> Iterator[UnixTestServer]:
    instance = UnixTestServer(path, list(responses))
    try:
        yield instance
    finally:
        instance.finish()


def accepted_response(request: dict[str, Any]) -> bytes:
    return response_frame(request, accepted=True)


def rejected_response(
    request: dict[str, Any],
    code: str = "rejected",
) -> bytes:
    return response_frame(request, accepted=False, code=code)


def response_frame(
    request: dict[str, Any],
    *,
    accepted: bool,
    code: str | None = None,
) -> bytes:
    response_code = code if code is not None else ("accepted" if accepted else "rejected")
    response: dict[str, Any] = {
        "version": 1,
        "requestId": request["requestId"],
        "accepted": accepted,
        "code": response_code,
    }
    return json.dumps(response, separators=(",", ":")).encode() + b"\n"


def write_descriptor(
    directory: Path,
    socket_path: Path,
    *,
    secret: str = SECRET,
    overrides: dict[str, Any] | None = None,
) -> Path:
    descriptor_path = directory / "bridge.json"
    descriptor: dict[str, Any] = {
        "version": 1,
        "transport": "unix",
        "socketPath": str(socket_path),
        "secret": secret,
        "pid": os.getpid(),
    }
    if overrides:
        descriptor.update(overrides)
    descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
    descriptor_path.chmod(0o600)
    return descriptor_path


def bridge_for(descriptor_path: Path, **kwargs: Any) -> T3CodeBridge:
    return T3CodeBridge(descriptor_path, **kwargs)


def reserve(
    directory: Path,
    *,
    response: ResponseFactory = accepted_response,
    recording_id: str = RECORDING_ID,
) -> tuple[T3CodeBridge, UnixTestServer]:
    socket_path = directory / "bridge.sock"
    instance = UnixTestServer(socket_path, [response])
    descriptor_path = write_descriptor(directory, socket_path)
    bridge = bridge_for(descriptor_path)
    result = bridge.reserve_recording(recording_id)
    assert result.outcome == BridgeOutcome.ACCEPTED
    instance.finish()
    return bridge, instance


def test_valid_descriptor_and_newline_delimited_accepted_request(
    bridge_dir: Path,
) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, accepted_response) as instance:
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        result = bridge.reserve_recording(RECORDING_ID)

    assert result.outcome == BridgeOutcome.ACCEPTED
    assert instance.raw_requests[0].endswith(b"\n")
    request = instance.requests[0]
    assert request == {
        "version": 1,
        "type": "recording.started",
        "requestId": request["requestId"],
        "recordingId": RECORDING_ID,
        "nonce": request["nonce"],
        "sentAt": request["sentAt"],
        "auth": SECRET,
    }


def test_default_production_descriptor_uses_userdata_and_t3code_home(
    bridge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert (
        Path("~/.t3").expanduser() / "userdata" / "integrations" / "voicebud" / "bridge.json"
    ) == DEFAULT_DESCRIPTOR_PATH
    t3_code_home = bridge_dir / "configured-home"
    descriptor_directory = t3_code_home / "userdata" / "integrations" / "voicebud"
    descriptor_directory.mkdir(parents=True)
    socket_path = descriptor_directory / "bridge.sock"
    monkeypatch.setenv("T3CODE_HOME", str(t3_code_home))

    with server(socket_path, accepted_response):
        write_descriptor(descriptor_directory, socket_path)
        result = T3CodeBridge().reserve_recording(RECORDING_ID)

    assert result.outcome == BridgeOutcome.ACCEPTED


def test_missing_descriptor(bridge_dir: Path) -> None:
    result = bridge_for(bridge_dir / "missing.json").reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR
    assert result.error_code == "t3_validation_error"


def test_descriptor_symlink_is_rejected(bridge_dir: Path) -> None:
    target = bridge_dir / "target.json"
    target.write_text("{}", encoding="utf-8")
    target.chmod(0o600)
    descriptor_path = bridge_dir / "bridge.json"
    descriptor_path.symlink_to(target)

    result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_non_regular_descriptor_is_rejected(bridge_dir: Path) -> None:
    result = bridge_for(bridge_dir).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_oversized_descriptor_is_rejected(bridge_dir: Path) -> None:
    descriptor_path = bridge_dir / "bridge.json"
    descriptor_path.write_bytes(b"x" * (MAX_DESCRIPTOR_BYTES + 1))
    descriptor_path.chmod(0o600)
    result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_descriptor_permissions_are_private(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(bridge_dir, socket_path)
        descriptor_path.chmod(0o640)
        result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_descriptor_owner_must_be_current_user(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(bridge_dir, socket_path)
        result = bridge_for(
            descriptor_path,
            current_uid=lambda: os.getuid() + 1,
        ).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


@pytest.mark.parametrize(
    ("content", "overrides"),
    [
        ("{", None),
        (None, {"version": True}),
        (None, {"version": 2}),
        (None, {"transport": "tcp"}),
        (None, {"secret": ""}),
        (None, {"socketPath": "relative.sock"}),
        (None, {"pid": 0}),
        (None, {"pid": -1}),
        (None, {"pid": True}),
        (None, {"pid": 1234.0}),
        (None, {"pid": "1234"}),
        (None, {"unexpected": True}),
    ],
)
def test_invalid_descriptor_json_and_schema_are_rejected(
    bridge_dir: Path,
    content: str | None,
    overrides: dict[str, Any] | None,
) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(bridge_dir, socket_path, overrides=overrides)
        if content is not None:
            descriptor_path.write_text(content, encoding="utf-8")
            descriptor_path.chmod(0o600)
        result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_descriptor_requires_pid_field(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(bridge_dir, socket_path)
        descriptor = json.loads(descriptor_path.read_text(encoding="utf-8"))
        descriptor.pop("pid")
        descriptor_path.write_text(json.dumps(descriptor), encoding="utf-8")
        descriptor_path.chmod(0o600)
        result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)

    assert result.outcome == BridgeOutcome.ERROR


def test_socket_outside_descriptor_directory_is_rejected(
    bridge_dir: Path,
) -> None:
    descriptor_directory = bridge_dir / "descriptor"
    descriptor_directory.mkdir()
    socket_path = bridge_dir / "outside.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(descriptor_directory, socket_path)
        result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_socket_must_be_a_real_unix_socket(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "not-a-socket"
    socket_path.write_text("plain", encoding="utf-8")
    descriptor_path = write_descriptor(bridge_dir, socket_path)
    result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


def test_socket_symlink_and_traversal_are_rejected(bridge_dir: Path) -> None:
    target_path = bridge_dir / "target.sock"
    with server(target_path) as _instance:
        symlink_path = bridge_dir / "linked.sock"
        symlink_path.symlink_to(target_path)
        symlink_descriptor = write_descriptor(bridge_dir, symlink_path)
        symlink_result = bridge_for(symlink_descriptor).reserve_recording(RECORDING_ID)
        traversal_descriptor = write_descriptor(
            bridge_dir,
            target_path,
            overrides={"socketPath": f"{bridge_dir}/nested/../target.sock"},
        )
        traversal_result = bridge_for(traversal_descriptor).reserve_recording(RECORDING_ID)
    assert symlink_result.outcome == BridgeOutcome.ERROR
    assert traversal_result.outcome == BridgeOutcome.ERROR


def test_socket_owner_must_match_descriptor_owner(
    bridge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path) as _instance:
        descriptor_path = write_descriptor(bridge_dir, socket_path)
        original_lstat = os.lstat

        def fake_lstat(path: os.PathLike[str] | str) -> os.stat_result:
            result = original_lstat(path)
            if Path(path) == socket_path:
                fields = list(result)
                fields[4] = result.st_uid + 1
                return os.stat_result(fields)
            return result

        monkeypatch.setattr(
            "voiceflow.integrations.t3_code_bridge.os.lstat",
            fake_lstat,
        )
        result = bridge_for(descriptor_path).reserve_recording(RECORDING_ID)
    assert result.outcome == BridgeOutcome.ERROR


@pytest.mark.parametrize(
    "code",
    ["authentication_failed", "replay", "rate_limited"],
)
def test_explicit_server_rejections_are_terminal(
    bridge_dir: Path,
    code: str,
) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(
        socket_path,
        lambda request: rejected_response(request, code),
    ):
        result = bridge_for(write_descriptor(bridge_dir, socket_path)).reserve_recording(
            RECORDING_ID
        )
    assert result.outcome == BridgeOutcome.REJECTED
    assert result.error_code == "t3_rejected"
    assert result.remote_code == code


@pytest.mark.parametrize(
    "response",
    [
        lambda _request: b"{bad}\n",
        lambda request: (
            json.dumps(
                {
                    "version": 1,
                    "requestId": "wrong",
                    "accepted": True,
                    "code": "accepted",
                }
            ).encode()
            + b"\n"
        ),
        lambda _request: b"{}\n",
        lambda request: (
            json.dumps(
                {
                    "version": 1,
                    "requestId": request["requestId"],
                    "accepted": True,
                    "code": "accepted",
                    "recordingId": request["recordingId"],
                }
            ).encode()
            + b"\n"
        ),
        lambda request: (
            json.dumps(
                {
                    "version": 1,
                    "requestId": request["requestId"],
                    "accepted": True,
                }
            ).encode()
            + b"\n"
        ),
        lambda request: response_frame(
            request,
            accepted=True,
            code="delivery_failed",
        ),
        lambda request: response_frame(
            request,
            accepted=False,
            code="accepted",
        ),
    ],
)
def test_malformed_or_uncorrelated_response_is_ambiguous(
    bridge_dir: Path,
    response: ResponseFactory,
) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, response):
        result = bridge_for(write_descriptor(bridge_dir, socket_path)).reserve_recording(
            RECORDING_ID
        )
    assert result.outcome == BridgeOutcome.AMBIGUOUS


def test_oversized_response_is_ambiguous(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    oversized = b"x" * MAX_RESPONSE_FRAME_BYTES + b"\n"
    with server(socket_path, lambda _request: oversized):
        result = bridge_for(write_descriptor(bridge_dir, socket_path)).reserve_recording(
            RECORDING_ID
        )
    assert result.outcome == BridgeOutcome.AMBIGUOUS


def test_eof_before_newline_is_ambiguous(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, lambda request: response_frame(request, accepted=True)[:-1]):
        result = bridge_for(write_descriptor(bridge_dir, socket_path)).reserve_recording(
            RECORDING_ID
        )
    assert result.outcome == BridgeOutcome.AMBIGUOUS


def test_response_has_a_separate_six_second_budget(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"

    def delayed_response(request: dict[str, Any]) -> bytes:
        time.sleep(0.08)
        return response_frame(request, accepted=True)

    with server(socket_path, delayed_response):
        result = bridge_for(
            write_descriptor(bridge_dir, socket_path),
            connect_write_timeout_seconds=0.03,
            response_timeout_seconds=0.2,
        ).reserve_recording(RECORDING_ID)

    assert result.outcome == BridgeOutcome.ACCEPTED


def test_response_timeout_can_be_reduced_for_tests(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"

    def delayed_response(request: dict[str, Any]) -> bytes:
        time.sleep(0.1)
        return response_frame(request, accepted=True)

    with server(socket_path, delayed_response):
        started = time.monotonic()
        result = bridge_for(
            write_descriptor(bridge_dir, socket_path),
            response_timeout_seconds=0.03,
        ).reserve_recording(RECORDING_ID)
        elapsed = time.monotonic() - started

    assert result.outcome == BridgeOutcome.AMBIGUOUS
    assert elapsed < 0.09


@pytest.mark.parametrize(
    "transcript",
    ["", " \t\n", "x" * 32_769],
)
def test_invalid_transcript_is_rejected_locally(
    bridge_dir: Path,
    transcript: str,
) -> None:
    bridge, _instance = reserve(bridge_dir)
    result = bridge.deliver_transcription(RECORDING_ID, transcript)
    assert result.outcome == BridgeOutcome.ERROR
    assert result.error_code == "t3_validation_error"


def test_request_frame_over_64_kib_is_rejected_locally(bridge_dir: Path) -> None:
    bridge, _instance = reserve(bridge_dir)
    transcript = "🙂" * 20_000
    result = bridge.deliver_transcription(RECORDING_ID, transcript)
    assert result.outcome == BridgeOutcome.ERROR


@pytest.mark.parametrize(
    "recording_id",
    ["not a valid id", "f" * 129, str(uuid.uuid1()), "ABC"],
)
def test_recording_id_must_be_a_canonical_uuid_v4(
    bridge_dir: Path,
    recording_id: str,
) -> None:
    result = bridge_for(bridge_dir / "missing.json").reserve_recording(recording_id)
    assert result.outcome == BridgeOutcome.ERROR


def test_request_ids_and_nonces_are_new_for_every_request(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, accepted_response, accepted_response) as instance:
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        assert bridge.reserve_recording(RECORDING_ID).accepted
        assert bridge.deliver_transcription(RECORDING_ID, "final text").accepted

    identifiers = [
        instance.requests[0]["requestId"],
        instance.requests[0]["nonce"],
        instance.requests[1]["requestId"],
        instance.requests[1]["nonce"],
    ]
    assert len(set(identifiers)) == 4


def test_process_request_ids_are_monotonic_and_concurrency_safe() -> None:
    prefix_calls = 0

    def prefix_factory() -> str:
        nonlocal prefix_calls
        prefix_calls += 1
        return "process-prefix"

    identifiers = _ProcessRequestIdentifiers(prefix_factory)
    with ThreadPoolExecutor(max_workers=16) as executor:
        generated = list(executor.map(lambda _index: identifiers.next(), range(4_000)))

    assert prefix_calls == 1
    assert len(set(generated)) == 4_000
    assert {value.rsplit(":", 1)[0] for value in generated} == {"process-prefix"}
    assert sorted(int(value.rsplit(":", 1)[1]) for value in generated) == list(range(1, 4_001))


def test_process_request_prefix_is_regenerated_after_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_pid = 100
    prefixes = iter(("parent-prefix", "child-prefix"))
    monkeypatch.setattr(t3_bridge_module.os, "getpid", lambda: current_pid)
    identifiers = _ProcessRequestIdentifiers(lambda: next(prefixes))

    assert identifiers.next() == "parent-prefix:1"
    current_pid = 101
    assert identifiers.next() == "child-prefix:1"
    assert identifiers.next() == "child-prefix:2"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_process_request_ids_reset_an_inherited_locked_lock_after_fork() -> None:
    prefixes = iter(("parent-prefix", "child-prefix"))
    identifiers = _ProcessRequestIdentifiers(lambda: next(prefixes))
    assert identifiers.next() == "parent-prefix:1"
    read_fd, write_fd = os.pipe()

    identifiers._lock.acquire()
    try:
        child_pid = os.fork()
    except BaseException:
        identifiers._lock.release()
        os.close(read_fd)
        os.close(write_fd)
        raise
    if child_pid != 0:
        identifiers._lock.release()

    if child_pid == 0:
        os.close(read_fd)

        def exit_on_timeout(_signum: int, _frame: Any) -> None:
            os._exit(42)

        signal.signal(signal.SIGALRM, exit_on_timeout)
        signal.alarm(2)
        try:
            request_id = identifiers.next()
            os.write(write_fd, request_id.encode())
        except BaseException:
            os._exit(43)
        finally:
            os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    try:
        readable, _writable, _exceptional = select.select([read_fd], [], [], 3)
        if not readable:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
            pytest.fail("forked identifier generation did not terminate")
        _waited_pid, status = os.waitpid(child_pid, 0)
        payload = os.read(read_fd, 256).decode()
    finally:
        os.close(read_fd)

    assert os.waitstatus_to_exitcode(status) == 0
    assert payload == "child-prefix:1"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="requires os.fork")
def test_at_fork_reset_isolates_base_exception_and_prioritizes_global_generator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    global_prefixes = iter(("global-parent", "global-child"))
    trailing_prefixes = iter(("trailing-parent", "trailing-child"))
    global_identifiers = _ProcessRequestIdentifiers(lambda: next(global_prefixes))
    trailing_identifiers = _ProcessRequestIdentifiers(lambda: next(trailing_prefixes))
    failing_identifiers = _ProcessRequestIdentifiers(lambda: "failing-prefix")
    assert global_identifiers.next() == "global-parent:1"
    assert trailing_identifiers.next() == "trailing-parent:1"

    failure_calls = 0

    def fail_reset() -> None:
        nonlocal failure_calls
        failure_calls += 1
        raise KeyboardInterrupt

    monkeypatch.setattr(failing_identifiers, "_reset_lock_after_fork", fail_reset)
    monkeypatch.setattr(
        t3_bridge_module,
        "_PROCESS_REQUEST_IDENTIFIERS",
        global_identifiers,
    )
    # Make the adversarial order deterministic: the shared generator is listed last,
    # while another locked instance follows the entry that raises BaseException.
    monkeypatch.setattr(
        t3_bridge_module,
        "_PROCESS_REQUEST_IDENTIFIER_INSTANCES",
        (failing_identifiers, trailing_identifiers, global_identifiers),
    )
    read_fd, write_fd = os.pipe()

    global_identifiers._lock.acquire()
    trailing_identifiers._lock.acquire()
    try:
        child_pid = os.fork()
    except BaseException:
        global_identifiers._lock.release()
        trailing_identifiers._lock.release()
        os.close(read_fd)
        os.close(write_fd)
        raise
    if child_pid != 0:
        global_identifiers._lock.release()
        trailing_identifiers._lock.release()

    if child_pid == 0:
        os.close(read_fd)

        def exit_on_timeout(_signum: int, _frame: Any) -> None:
            os._exit(42)

        signal.signal(signal.SIGALRM, exit_on_timeout)
        signal.alarm(2)
        try:
            payload = "|".join(
                (
                    global_identifiers.next(),
                    trailing_identifiers.next(),
                    str(failure_calls),
                )
            )
            os.write(write_fd, payload.encode())
        except BaseException:
            os._exit(43)
        finally:
            os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    try:
        readable, _writable, _exceptional = select.select([read_fd], [], [], 3)
        if not readable:
            with suppress(ProcessLookupError):
                os.kill(child_pid, signal.SIGKILL)
            os.waitpid(child_pid, 0)
            pytest.fail("forked identifier reset did not isolate the failing entry")
        _waited_pid, status = os.waitpid(child_pid, 0)
        payload = os.read(read_fd, 256).decode()
    finally:
        os.close(read_fd)

    assert os.waitstatus_to_exitcode(status) == 0
    assert payload == "global-child:1|trailing-child:1|1"


def test_process_request_identifier_registry_does_not_retain_dead_instances() -> None:
    registry = t3_bridge_module._PROCESS_REQUEST_IDENTIFIER_INSTANCES
    gc.collect()
    size_before = len(registry)
    identifiers = _ProcessRequestIdentifiers(lambda: "temporary-prefix")
    reference = weakref.ref(identifiers)

    assert len(registry) == size_before + 1
    del identifiers
    gc.collect()

    assert reference() is None
    assert len(registry) == size_before


def test_request_generation_does_not_retain_identifier_history(
    bridge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_identifiers = _ProcessRequestIdentifiers(lambda: "bounded-prefix")
    monkeypatch.setattr(
        t3_bridge_module,
        "_PROCESS_REQUEST_IDENTIFIERS",
        request_identifiers,
    )
    nonce_counter = iter(range(10_000))
    bridge = bridge_for(
        bridge_dir / "unused.json",
        id_factory=lambda: f"nonce-{next(nonce_counter)}",
    )
    collections_before = {
        name: len(value)
        for name, value in vars(bridge).items()
        if isinstance(value, (dict, list, set))
    }

    requests = [
        bridge._new_request(
            request_type="recording.started",
            recording_id=RECORDING_ID,
            secret=SECRET,
        )
        for _index in range(10_000)
    ]

    collections_after = {
        name: len(value)
        for name, value in vars(bridge).items()
        if isinstance(value, (dict, list, set))
    }
    assert collections_after == collections_before
    assert "_used_request_identifiers" not in vars(bridge)
    assert len({request["requestId"] for request in requests}) == 10_000
    assert len({request["nonce"] for request in requests}) == 10_000


def test_nonce_generation_is_independent_from_request_counter(
    bridge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        t3_bridge_module,
        "_PROCESS_REQUEST_IDENTIFIERS",
        _ProcessRequestIdentifiers(lambda: "independent-prefix"),
    )
    nonces = iter(("random-nonce-a", "random-nonce-b"))
    bridge = bridge_for(bridge_dir / "unused.json", id_factory=lambda: next(nonces))

    first = bridge._new_request(
        request_type="recording.started",
        recording_id=RECORDING_ID,
        secret=SECRET,
    )
    second = bridge._new_request(
        request_type="recording.started",
        recording_id=SECOND_RECORDING_ID,
        secret=SECRET,
    )

    assert first["requestId"] == "independent-prefix:1"
    assert second["requestId"] == "independent-prefix:2"
    assert first["nonce"] == "random-nonce-a"
    assert second["nonce"] == "random-nonce-b"


def test_request_identifier_and_nonce_respect_128_character_limit(
    bridge_dir: Path,
) -> None:
    assert len(_ProcessRequestIdentifiers(lambda: "p" * 126).next()) == 128
    with pytest.raises(_LocalValidationError):
        _ProcessRequestIdentifiers(lambda: "p" * 127).next()

    bridge = bridge_for(bridge_dir / "unused.json", id_factory=lambda: "n" * 129)
    with pytest.raises(_LocalValidationError):
        bridge._new_request(
            request_type="recording.started",
            recording_id=RECORDING_ID,
            secret=SECRET,
        )


def test_request_wire_encoding_is_unchanged(
    bridge_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        t3_bridge_module,
        "_PROCESS_REQUEST_IDENTIFIERS",
        _ProcessRequestIdentifiers(lambda: "wire-prefix"),
    )
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, accepted_response) as instance:
        bridge = bridge_for(
            write_descriptor(bridge_dir, socket_path),
            wall_clock=lambda: 1_234.5,
            id_factory=lambda: "wire-nonce",
        )
        assert bridge.reserve_recording(RECORDING_ID).accepted

    assert instance.raw_requests == [
        (
            '{"version":1,"type":"recording.started",'
            '"requestId":"wire-prefix:1",'
            f'"recordingId":"{RECORDING_ID}",'
            '"nonce":"wire-nonce","sentAt":1234500,'
            f'"auth":"{SECRET}"}}\n'
        ).encode()
    ]


def test_request_ids_are_not_reused_after_ambiguous_error(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"

    def eof_response(request: dict[str, Any]) -> bytes:
        return response_frame(request, accepted=True)[:-1]

    with server(socket_path, eof_response, accepted_response) as instance:
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        assert bridge.reserve_recording(RECORDING_ID).outcome == BridgeOutcome.AMBIGUOUS
        assert bridge.reserve_recording(SECOND_RECORDING_ID).accepted

    identifiers = {
        instance.requests[0]["requestId"],
        instance.requests[0]["nonce"],
        instance.requests[1]["requestId"],
        instance.requests[1]["nonce"],
    }
    assert len(identifiers) == 4
    first_prefix, first_counter = instance.requests[0]["requestId"].rsplit(":", 1)
    second_prefix, second_counter = instance.requests[1]["requestId"].rsplit(":", 1)
    assert first_prefix == second_prefix
    assert int(second_counter) > int(first_counter)


def test_socket_replacement_after_reservation_is_fail_closed(
    bridge_dir: Path,
) -> None:
    bridge, _instance = reserve(bridge_dir)
    socket_path = bridge_dir / "bridge.sock"
    socket_path.unlink()
    replacement = UnixTestServer(socket_path, [])
    try:
        result = bridge.deliver_transcription(RECORDING_ID, "must not arrive")
    finally:
        replacement.finish()
    assert result.error_code == "t3_session_changed"
    assert replacement.requests == []


def test_replaced_descriptor_is_not_used_for_completion(bridge_dir: Path) -> None:
    bridge, _instance = reserve(bridge_dir)
    original_socket = bridge_dir / "bridge.sock"
    new_socket = bridge_dir / "new.sock"
    replacement = UnixTestServer(new_socket, [])
    try:
        descriptor_path = bridge_dir / "bridge.json"
        write_descriptor(bridge_dir, new_socket, secret="new-secret")
        result = bridge.deliver_transcription(RECORDING_ID, "must not arrive")
    finally:
        replacement.finish()
    assert descriptor_path.exists()
    assert result.error_code == "t3_session_changed"
    assert replacement.requests == []
    assert original_socket.exists()


def test_concurrent_sessions_remain_independent(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    responses = [accepted_response] * 4
    with server(socket_path, *responses) as instance:
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        with ThreadPoolExecutor(max_workers=2) as executor:
            reservations = list(
                executor.map(
                    bridge.reserve_recording,
                    [RECORDING_ID, SECOND_RECORDING_ID],
                )
            )
        with ThreadPoolExecutor(max_workers=2) as executor:
            completions = list(
                executor.map(
                    lambda values: bridge.deliver_transcription(*values),
                    [
                        (RECORDING_ID, "first"),
                        (SECOND_RECORDING_ID, "second"),
                    ],
                )
            )
    assert all(result.accepted for result in reservations + completions)
    assert {request["recordingId"] for request in instance.requests} == {
        RECORDING_ID,
        SECOND_RECORDING_ID,
    }


def test_sessions_are_cleaned_after_completion_and_abandon(bridge_dir: Path) -> None:
    socket_path = bridge_dir / "bridge.sock"
    with server(
        socket_path,
        accepted_response,
        accepted_response,
        accepted_response,
    ):
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        assert bridge.reserve_recording(RECORDING_ID).accepted
        assert bridge.deliver_transcription(RECORDING_ID, "done").accepted
        assert (
            bridge.deliver_transcription(RECORDING_ID, "duplicate").error_code
            == "t3_session_changed"
        )
        assert bridge.reserve_recording(SECOND_RECORDING_ID).accepted
        bridge.abandon_recording(SECOND_RECORDING_ID)
        assert (
            bridge.deliver_transcription(SECOND_RECORDING_ID, "abandoned").error_code
            == "t3_session_changed"
        )


def test_sensitive_values_never_appear_in_results_or_logs(
    bridge_dir: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    transcript = "private transcript sentinel"
    socket_path = bridge_dir / "bridge.sock"
    with server(socket_path, accepted_response, rejected_response):
        bridge = bridge_for(write_descriptor(bridge_dir, socket_path))
        assert bridge.reserve_recording(RECORDING_ID).accepted
        result = bridge.deliver_transcription(RECORDING_ID, transcript)

    visible = f"{result!r}\n{caplog.text}"
    assert result.outcome == BridgeOutcome.REJECTED
    assert SECRET not in visible
    assert transcript not in visible


def test_frontmost_detector_uses_authoritative_bundle_identifier() -> None:
    class Application:
        def __init__(self, bundle_identifier: str | None) -> None:
            self._bundle_identifier = bundle_identifier

        def bundleIdentifier(self) -> str | None:
            return self._bundle_identifier

    class Workspace:
        def __init__(self, bundle_identifier: str | None) -> None:
            self.application = Application(bundle_identifier)

        def frontmostApplication(self) -> Application:
            return self.application

    assert (
        MacOSFrontmostApplicationDetector(lambda: Workspace(T3_CODE_BUNDLE_IDENTIFIER))
        .detect()
        .kind
        == FrontmostApplicationKind.T3_CODE
    )
    assert (
        MacOSFrontmostApplicationDetector(lambda: Workspace("example.other")).detect().kind
        == FrontmostApplicationKind.OTHER
    )
    assert (
        MacOSFrontmostApplicationDetector(lambda: Workspace(None)).detect().kind
        == FrontmostApplicationKind.UNAVAILABLE
    )
    assert (
        MacOSFrontmostApplicationDetector(
            lambda: (_ for _ in ()).throw(RuntimeError("unavailable"))
        )
        .detect()
        .kind
        == FrontmostApplicationKind.UNAVAILABLE
    )
