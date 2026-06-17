"""Tests for Stage 1 haptic command routing."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from data_models import Vec3
from haptic_config import haptic_config_from_dict
from haptic_runtime import HapticRuntime


def test_blocked_directional_routes_to_matrix_with_blocked_surface_direction() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_NEG": [1, 2, 3]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=7,
        source_frame_id="frame-7",
        sample=_sample(),
        trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"),
        snapshot=None,
    )

    records = runtime.records_snapshot()
    assert len(records) == 1
    assert records[0]["target_device"] == "matrix"
    assert records[0]["haptic_phase"] == "state_start"
    assert records[0]["matrix_direction_semantics"] == "blocked_surface"
    assert records[0]["matrix_direction_used"] == "X_NEG"
    assert records[0]["correction_direction"] == "X_POS"
    assert records[0]["channel_list"] == "[1,2,3]"
    assert records[0]["send_status"] == "sent"
    assert worker.instances[0].packets


def test_matrix_direction_semantics_can_use_correction_direction() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_semantics": "correction_direction",
                "direction_channel_map": {"X_POS": [4]},
            },
        },
        worker_factory=_FakeWorkerFactory(),
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["primary_blocked_surface"] == "X_NEG"
    assert row["correction_direction"] == "X_POS"
    assert row["matrix_direction_used"] == "X_POS"
    assert row["channel_list"] == "[4]"


def test_missing_channel_mapping_is_skipped_without_send() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["send_status"] == "skipped"
    assert row["not_sent_reason"] == "no_channel_mapping"
    assert worker.instances[0].packets == []


def test_latched_once_sends_only_on_start_and_direction_change() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "feedback_mode": "latched_once",
                "direction_channel_map": {"X_NEG": [1], "X_POS": [2]},
            },
        },
        worker_factory=_FakeWorkerFactory(),
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)
    runtime.process_frame(frame_index=3, source_frame_id=None, sample=_sample(0.2), trial_result=_trial_result(blocked=True, primary_surface="X_POS", track_state="BLOCKED_X_POS"), snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["haptic_phase"] for row in rows] == ["state_start", "state_update"]
    assert [row["matrix_direction_used"] for row in rows] == ["X_NEG", "X_POS"]


def test_continuous_resend_repeats_after_interval() -> None:
    clock = _Clock(0.0)
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "feedback_mode": "continuous_resend",
                "resend_interval_ms": 100.0,
                "direction_channel_map": {"X_NEG": [1]},
            },
        },
        worker_factory=_FakeWorkerFactory(),
        monotonic_ms_fn=clock.now,
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)
    clock.value = 50.0
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)
    clock.value = 101.0
    runtime.process_frame(frame_index=3, source_frame_id=None, sample=_sample(0.2), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)

    assert [row["haptic_phase"] for row in runtime.records_snapshot()] == ["state_start", "state_update"]


def test_blocked_end_records_state_end_without_clear() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_NEG": [1]},
            },
        },
        worker_factory=_FakeWorkerFactory(),
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(blocked=True, primary_surface="X_NEG", track_state="BLOCKED_X_NEG"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(blocked=False), snapshot=None)

    rows = runtime.records_snapshot()
    assert rows[-1]["haptic_phase"] == "state_end"
    assert rows[-1]["send_status"] == "not_sent"
    assert rows[-1]["not_sent_reason"] == "state_end_no_hardware_clear"


def test_vibration_contact_records_are_one_shot_protocol_pending() -> None:
    runtime = _runtime({"enabled": True, "vibration": {"enabled": True}})

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(events=("contact_enter", "contact_exit")),
        snapshot=None,
    )

    rows = runtime.records_snapshot()
    assert [row["haptic_type"] for row in rows] == [
        "vibration_contact_enter",
        "vibration_contact_exit",
    ]
    assert all(row["haptic_phase"] == "one_shot" for row in rows)
    assert all(row["send_status"] == "protocol_pending" for row in rows)
    assert all(row["not_sent_reason"] == "not_implemented" for row in rows)


def test_vibration_slip_start_and_end_are_state_commands() -> None:
    runtime = _runtime({"enabled": True, "vibration": {"enabled": True}})

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=False), snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["haptic_phase"] for row in rows] == ["state_start", "state_end"]
    assert all(row["haptic_type"] == "vibration_slip" for row in rows)


def test_slip_global_disable_skips_all_slip_vibration() -> None:
    runtime = _runtime(
        {"enabled": True, "vibration": {"enabled": True, "enable_slip": False}}
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)

    row = runtime.records_snapshot()[0]
    assert row["send_status"] == "skipped"
    assert row["not_sent_reason"] == "slip_disabled"


def test_track_blocked_slip_outside_target_can_be_disabled_without_disabling_pinch_slip() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "enable_slip_track_blocked": False,
            },
        }
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="TRACK_BLOCKED", block_center=Vec3(2.0, 2.0, 0.0)), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT", block_center=Vec3(2.0, 2.0, 0.0)), snapshot=None)

    rows = runtime.records_snapshot()
    assert rows[0]["not_sent_reason"] == "slip_track_blocked_disabled"
    assert rows[1]["send_status"] == "protocol_pending"


def test_track_blocked_slip_inside_target_uses_target_region_flag() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "enable_slip_track_blocked": False,
                "enable_slip_track_blocked_in_target_region": True,
            },
        }
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="TRACK_BLOCKED", block_center=Vec3(0.5, 0.5, 0.0)), snapshot=None)

    row = runtime.records_snapshot()[0]
    assert row["send_status"] == "protocol_pending"
    assert row["not_sent_reason"] == "not_implemented"


def test_matrix_and_vibration_targets_do_not_suppress_each_other() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_NEG": [1]},
            },
            "vibration": {"enabled": True},
        },
        worker_factory=_FakeWorkerFactory(),
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_NEG",
            track_state="BLOCKED_X_NEG",
            slip=True,
            slip_reason="TRACK_BLOCKED",
        ),
        snapshot=None,
    )

    rows = runtime.records_snapshot()
    assert {row["target_device"] for row in rows} == {"matrix", "vibration"}
    assert {row["haptic_type"] for row in rows} == {
        "matrix_blocked_direction",
        "vibration_slip",
    }


def _runtime(
    payload: dict[str, Any],
    *,
    worker_factory: Any = None,
    monotonic_ms_fn: Any = None,
) -> HapticRuntime:
    return HapticRuntime(
        trial_id="trial",
        haptic_config=haptic_config_from_dict(payload),
        trial_config={"target_region": {"min": [0, 0, -1], "max": [1, 1, 1]}},
        worker_factory=worker_factory,
        monotonic_ms_fn=monotonic_ms_fn,
        sleep_fn=lambda _seconds: None,
    )


def _trial_result(
    *,
    blocked: bool = False,
    primary_surface: str | None = None,
    track_state: str = "INSIDE_TRACK",
    slip: bool = False,
    slip_reason: str | None = None,
    events: tuple[str, ...] = (),
    block_center: Vec3 = Vec3(0.0, 0.0, 0.0),
) -> Any:
    return SimpleNamespace(
        time_since_prompt=0.1,
        events=tuple(SimpleNamespace(event_type=event, details={}) for event in events),
        haptic_feedback_state=SimpleNamespace(
            slip_active=slip,
            slip_reason=slip_reason,
            blocked_force_active=blocked,
            primary_blocked_surface=primary_surface,
            primary_blocked_amount=0.1 if blocked else 0.0,
            force_vector_task=None,
            force_magnitude=0.0,
        ),
        frame_output=SimpleNamespace(
            feedback_state=SimpleNamespace(
                tracking_valid=True,
                recovery_frame=False,
                track_state=track_state,
            ),
            block_state=SimpleNamespace(center=block_center),
        ),
    )


def _sample(time_value: float = 0.0) -> Any:
    return SimpleNamespace(time=time_value)


class _Clock:
    def __init__(self, value: float) -> None:
        self.value = value

    def now(self) -> float:
        return self.value


class _FakeWorker:
    def __init__(self, **_: Any) -> None:
        self.connected = False
        self.packets: list[bytes] = []

    def start(self) -> None:
        self.connected = True

    def submit(self, record: Any, packet: bytes) -> bool:
        self.packets.append(packet)
        record.queued_monotonic_ms = 1.0
        record.sent_monotonic_ms = 2.0
        record.success = True
        record.send_status = "sent"
        record.not_sent_reason = None
        return True

    def stop(self) -> None:
        self.connected = False


class _FakeWorkerFactory:
    def __init__(self) -> None:
        self.instances: list[_FakeWorker] = []

    def __call__(self, **kwargs: Any) -> _FakeWorker:
        del kwargs
        worker = _FakeWorker()
        self.instances.append(worker)
        return worker
