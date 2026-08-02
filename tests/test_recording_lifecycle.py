from __future__ import annotations

import threading
from typing import Any

import numpy as np
import pytest

from voiceflow.integrations import T3CodeDestination
from voiceflow.system.text_injector import InjectionDestination


class Pipeline:
    def __init__(
        self,
        *,
        accepting: bool = True,
        fail_submission: bool = False,
        fail_cancellation: bool = False,
    ) -> None:
        self.accepting = accepting
        self.fail_submission = fail_submission
        self.fail_cancellation = fail_cancellation
        self.reservations = 0
        self.cancellations = 0
        self.submissions: list[tuple[np.ndarray, dict[str, Any]]] = []

    def try_reserve(self) -> bool:
        if not self.accepting:
            return False
        self.reservations += 1
        return True

    def cancel_reservation(self) -> None:
        self.cancellations += 1
        if self.fail_cancellation:
            raise RuntimeError("cancel failed")

    def submit_reserved(self, audio: np.ndarray, **kwargs: Any) -> int:
        self.submissions.append((audio, kwargs))
        if self.fail_submission:
            self.cancel_reservation()
            raise RuntimeError("Pipeline is shutting down")
        return len(self.submissions)


class Bridge:
    def __init__(self, *, fail_abandonment: bool = False) -> None:
        self.fail_abandonment = fail_abandonment
        self.abandoned: list[str] = []

    def abandon_recording(self, recording_id: str) -> None:
        self.abandoned.append(recording_id)
        if self.fail_abandonment:
            raise RuntimeError("abandon failed")


def lease_type() -> type[Any]:
    from voiceflow.recording import RecordingLease

    return RecordingLease


def test_pipeline_in_shutdown_cannot_be_reserved() -> None:
    pipeline = Pipeline(accepting=False)
    bridge = Bridge()

    assert lease_type().try_reserve(pipeline, bridge) is None
    assert pipeline.reservations == 0
    assert pipeline.cancellations == 0


def test_double_abort_and_close_cancel_pipeline_once() -> None:
    pipeline = Pipeline()
    lease = lease_type().try_reserve(pipeline, Bridge())
    assert lease is not None

    lease.abort()
    lease.abort()
    lease.close()

    assert pipeline.reservations == 1
    assert pipeline.cancellations == 1


def test_t3_attach_abort_and_close_abandon_once() -> None:
    pipeline = Pipeline()
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None

    lease.attach_t3("recording-1")
    lease.abort()
    lease.close()

    assert pipeline.cancellations == 1
    assert bridge.abandoned == ["recording-1"]


def test_t3_can_only_be_attached_once() -> None:
    pipeline = Pipeline()
    lease = lease_type().try_reserve(pipeline, Bridge())
    assert lease is not None
    lease.attach_t3("recording-1")

    with pytest.raises(RuntimeError, match="already has"):
        lease.attach_t3("recording-2")
    lease.close()


def test_non_t3_submission_commits_without_a_t3_attachment() -> None:
    pipeline = Pipeline()
    lease = lease_type().try_reserve(pipeline, Bridge())
    assert lease is not None

    assert (
        lease.commit_submission(
            np.ones(1, dtype=np.float32),
            destination=InjectionDestination.clipboard_only(),
            sample_rate=16_000,
            app_bundle_id=None,
        )
        == 1
    )
    lease.close()
    assert pipeline.cancellations == 0


def test_double_commit_submits_once_and_transfers_t3_ownership() -> None:
    pipeline = Pipeline()
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    destination = T3CodeDestination("recording-1")
    lease.attach_t3(destination.recording_id)

    assert (
        lease.commit_submission(
            np.ones(8, dtype=np.float32),
            destination=destination,
            sample_rate=16_000,
            app_bundle_id="com.t3tools.t3code",
        )
        == 1
    )
    with pytest.raises(RuntimeError, match="no longer open"):
        lease.commit_submission(
            np.ones(8, dtype=np.float32),
            destination=destination,
            sample_rate=16_000,
            app_bundle_id="com.t3tools.t3code",
        )
    lease.abort()
    lease.close()

    assert len(pipeline.submissions) == 1
    assert pipeline.cancellations == 0
    assert bridge.abandoned == []
    assert pipeline.submissions[0][1]["destination"] == destination


def test_commit_and_abort_race_has_one_winner() -> None:
    def run_race() -> None:
        pipeline = Pipeline()
        bridge = Bridge()
        lease = lease_type().try_reserve(pipeline, bridge)
        assert lease is not None
        destination = T3CodeDestination("recording-race")
        lease.attach_t3(destination.recording_id)
        barrier = threading.Barrier(3)
        committed: list[int] = []
        rejected: list[BaseException] = []

        def commit() -> None:
            barrier.wait()
            try:
                committed.append(
                    lease.commit_submission(
                        np.ones(1, dtype=np.float32),
                        destination=destination,
                        sample_rate=16_000,
                        app_bundle_id=None,
                    )
                )
            except BaseException as exc:
                rejected.append(exc)

        def abort() -> None:
            barrier.wait()
            lease.abort()

        workers = [threading.Thread(target=commit), threading.Thread(target=abort)]
        for worker in workers:
            worker.start()
        barrier.wait()
        for worker in workers:
            worker.join(timeout=1)
            assert not worker.is_alive()

        assert (len(committed), len(rejected)) in {(1, 0), (0, 1)}
        assert len(pipeline.submissions) + pipeline.cancellations == 1
        assert len(bridge.abandoned) == pipeline.cancellations

    for _attempt in range(25):
        run_race()


def test_exception_before_transfer_leaves_lease_abortable() -> None:
    pipeline = Pipeline()
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    lease.attach_t3("recording-1")

    with pytest.raises(ValueError, match="does not match"):
        lease.commit_submission(
            np.ones(1, dtype=np.float32),
            destination=InjectionDestination.clipboard_only(),
            sample_rate=16_000,
            app_bundle_id=None,
        )
    lease.close()

    assert pipeline.submissions == []
    assert pipeline.cancellations == 1
    assert bridge.abandoned == ["recording-1"]


@pytest.mark.parametrize(
    ("attached", "destination", "message"),
    [
        (None, T3CodeDestination("recording-1"), "no attached"),
        ("recording-1", T3CodeDestination("recording-2"), "does not match"),
    ],
)
def test_invalid_t3_destination_does_not_transfer_pipeline_reservation(
    attached: str | None,
    destination: T3CodeDestination,
    message: str,
) -> None:
    pipeline = Pipeline()
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    if attached is not None:
        lease.attach_t3(attached)

    with pytest.raises(ValueError, match=message):
        lease.commit_submission(
            np.ones(1, dtype=np.float32),
            destination=destination,
            sample_rate=16_000,
            app_bundle_id=None,
        )
    lease.close()

    assert pipeline.cancellations == 1
    assert bridge.abandoned == ([] if attached is None else [attached])


def test_pipeline_shutdown_during_commit_owns_pipeline_cleanup() -> None:
    pipeline = Pipeline(fail_submission=True)
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    destination = T3CodeDestination("recording-1")
    lease.attach_t3(destination.recording_id)

    with pytest.raises(RuntimeError, match="shutting down"):
        lease.commit_submission(
            np.ones(1, dtype=np.float32),
            destination=destination,
            sample_rate=16_000,
            app_bundle_id=None,
        )
    lease.abort()
    lease.close()

    assert pipeline.cancellations == 1
    assert bridge.abandoned == ["recording-1"]


def test_commit_preserves_submission_error_when_t3_cleanup_also_fails() -> None:
    pipeline = Pipeline(fail_submission=True)
    bridge = Bridge(fail_abandonment=True)
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    destination = T3CodeDestination("recording-1")
    lease.attach_t3(destination.recording_id)

    with pytest.raises(RuntimeError, match="shutting down") as raised:
        lease.commit_submission(
            np.ones(1, dtype=np.float32),
            destination=destination,
            sample_rate=16_000,
            app_bundle_id=None,
        )

    assert raised.value.__notes__ == ["T3 cleanup also failed: RuntimeError('abandon failed')"]
    assert bridge.abandoned == ["recording-1"]
    lease.close()


def test_abort_attempts_both_cleanups_once_when_they_fail() -> None:
    pipeline = Pipeline(fail_cancellation=True)
    bridge = Bridge(fail_abandonment=True)
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    lease.attach_t3("recording-1")

    with pytest.raises(RuntimeError, match="cancel failed") as raised:
        lease.abort()

    assert raised.value.__notes__ == ["T3 cleanup also failed: RuntimeError('abandon failed')"]
    assert pipeline.cancellations == 1
    assert bridge.abandoned == ["recording-1"]
    lease.close()


def test_exception_after_commit_cannot_reclaim_transferred_t3_session() -> None:
    pipeline = Pipeline()
    bridge = Bridge()
    lease = lease_type().try_reserve(pipeline, bridge)
    assert lease is not None
    destination = T3CodeDestination("recording-1")
    lease.attach_t3(destination.recording_id)

    with pytest.raises(RuntimeError, match="after commit"):
        try:
            lease.commit_submission(
                np.ones(1, dtype=np.float32),
                destination=destination,
                sample_rate=16_000,
                app_bundle_id=None,
            )
            raise RuntimeError("after commit")
        finally:
            lease.close()

    assert pipeline.cancellations == 0
    assert bridge.abandoned == []
