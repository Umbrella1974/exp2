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


def test_matrix_combination_uses_exact_combination_mapping_not_union() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_POS": [1], "Y_POS": [2]},
                "combination_channel_map": {"X_POS+Y_POS": [20, 21]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("Y_POS", "X_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["blocked_surface_set"] == "X_POS+Y_POS"
    assert row["correction_direction_set"] == "X_NEG+Y_NEG"
    assert row["matrix_direction_used"] == "X_POS+Y_POS"
    assert row["channel_list"] == "[20,21]"
    assert row["send_status"] == "sent"
    assert worker.instances[0].packets


def test_matrix_combination_missing_mapping_skips_without_union_by_default() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_POS": [1], "Y_POS": [2]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("X_POS", "Y_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["matrix_direction_used"] == "X_POS+Y_POS"
    assert row["send_status"] == "not_sent"
    assert row["not_sent_reason"] == "missing_combination_mapping"
    assert worker.instances[0].packets == []


def test_matrix_combination_can_use_correction_direction_semantics() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_semantics": "correction_direction",
                "combination_channel_map": {"X_NEG+Y_NEG": [40]},
            },
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
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("X_POS", "Y_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["blocked_surface_set"] == "X_POS+Y_POS"
    assert row["correction_direction_set"] == "X_NEG+Y_NEG"
    assert row["matrix_direction_used"] == "X_NEG+Y_NEG"
    assert row["matrix_direction_semantics"] == "correction_direction"
    assert row["channel_list"] == "[40]"


def test_matrix_ignore_direction_axes_filters_before_lookup() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "ignore_direction_axes": ["Z"],
                "combination_channel_map": {"X_POS+Y_NEG": [21]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("X_POS", "Y_NEG", "Z_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["blocked_surface_set"] == "X_POS+Y_NEG+Z_POS"
    assert row["correction_direction_set"] == "X_NEG+Y_POS+Z_NEG"
    assert row["matrix_filtered_blocked_surface_set"] == "X_POS+Y_NEG"
    assert row["matrix_filtered_correction_direction_set"] == "X_NEG+Y_POS"
    assert row["matrix_ignored_direction_axes"] == "Z"
    assert row["matrix_direction_used"] == "X_POS+Y_NEG"
    assert row["channel_list"] == "[21]"
    assert row["send_status"] == "sent"
    assert worker.instances[0].packets


def test_matrix_ignore_direction_axes_empty_after_filter_skips_without_fallback() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "ignore_direction_axes": ["Z"],
                "direction_channel_map": {"X_POS": [1], "Z_POS": [9]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="Z_POS",
            track_state="BLOCKED_Z_POS",
            blocked_surfaces=("Z_POS",),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["blocked_surface_set"] == "Z_POS"
    assert row["matrix_filtered_blocked_surface_set"] is None
    assert row["matrix_direction_used"] is None
    assert row["send_status"] == "skipped"
    assert row["not_sent_reason"] == "direction_filtered_empty"
    assert worker.instances[0].packets == []


def test_matrix_without_ignore_direction_axes_keeps_full_3d_key_requirement() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "combination_channel_map": {"X_POS+Y_NEG": [21]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("X_POS", "Y_NEG", "Z_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["matrix_direction_used"] == "X_POS+Y_NEG+Z_POS"
    assert row["send_status"] == "not_sent"
    assert row["not_sent_reason"] == "missing_combination_mapping"
    assert worker.instances[0].packets == []


def test_matrix_ignore_direction_axes_filters_before_correction_direction() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_semantics": "correction_direction",
                "ignore_direction_axes": ["Z"],
                "combination_channel_map": {"X_NEG+Y_POS": [41]},
            },
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
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            blocked_surfaces=("X_POS", "Y_NEG", "Z_POS"),
        ),
        snapshot=None,
    )

    row = runtime.records_snapshot()[0]
    assert row["correction_direction_set"] == "X_NEG+Y_POS+Z_NEG"
    assert row["matrix_filtered_correction_direction_set"] == "X_NEG+Y_POS"
    assert row["matrix_direction_used"] == "X_NEG+Y_POS"
    assert row["channel_list"] == "[41]"


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


def test_matrix_contact_valid_and_pinch_insufficient_are_single_main_outputs() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {
                    "enabled": True,
                    "channel_list": [1],
                },
                "pinch_insufficient_feedback": {
                    "enabled": True,
                    "channel_list": [2],
                },
                "reset_before_output_change": {
                    "enabled": True,
                    "reset_map": {
                        "contact_valid": {"channel_list": [10]},
                    },
                },
            },
        },
        worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_VALID",
        ),
        snapshot=None,
    )
    runtime.process_frame(
        frame_index=2,
        source_frame_id=None,
        sample=_sample(0.1),
        trial_result=_trial_result(
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_INSUFFICIENT",
            slip=False,
        ),
        snapshot=None,
    )

    rows = runtime.records_snapshot()
    assert [row["haptic_type"] for row in rows] == [
        "matrix_contact_valid",
        "matrix_reset_before_output_change",
        "matrix_pinch_insufficient",
    ]
    assert [row["channel_list"] for row in rows] == ["[1]", "[10]", "[2]"]
    assert rows[1]["previous_matrix_output_key"] == "contact_valid"
    assert rows[1]["next_matrix_output_key"] == "pinch_insufficient"
    assert rows[2]["matrix_output_key"] == "pinch_insufficient"
    assert [len(sequence) for sequence in worker.instances[0].sequences] == [1, 2]
    assert runtime._previous_matrix_output_key == "pinch_insufficient"


def test_matrix_priority_selects_blocked_without_channel_union_or_fallback() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "pinch_insufficient_feedback": {"enabled": True, "channel_list": [2]},
                "direction_channel_map": {"X_POS": [3]},
            },
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
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_INSUFFICIENT",
        ),
        snapshot=None,
    )

    rows = runtime.records_snapshot()
    assert len(rows) == 1
    assert rows[0]["haptic_type"] == "matrix_blocked_direction"
    assert rows[0]["matrix_output_key"] == "blocked:X_POS"
    assert rows[0]["channel_list"] == "[3]"

    no_mapping = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
            },
        }
    )
    no_mapping.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            blocked=True,
            primary_surface="X_POS",
            track_state="BLOCKED_X_POS",
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_VALID",
        ),
        snapshot=None,
    )
    missing_row = no_mapping.records_snapshot()[0]
    assert missing_row["haptic_type"] == "matrix_blocked_direction"
    assert missing_row["not_sent_reason"] == "no_channel_mapping"


def test_matrix_missing_reset_skip_policy_sends_main_and_empty_mapping_is_not_packet() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "pinch_insufficient_feedback": {"enabled": True, "channel_list": [2]},
                "reset_before_output_change": {
                    "enabled": True,
                    "missing_reset_policy": "skip_reset",
                    "reset_map": {
                        "contact_valid": {"channel_list": []},
                    },
                },
            },
        },
        worker_factory=worker,
    )
    runtime.start()
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_INSUFFICIENT"), snapshot=None)

    rows = runtime.records_snapshot()
    reset_row, main_row = rows[-2:]
    assert reset_row["haptic_type"] == "matrix_reset_before_output_change"
    assert reset_row["channel_list"] == "[]"
    assert reset_row["send_status"] == "skipped"
    assert reset_row["not_sent_reason"] == "missing_matrix_reset_mapping"
    assert main_row["send_status"] == "sent"
    assert len(worker.instances[0].sequences[-1]) == 1
    assert runtime._previous_matrix_output_key == "pinch_insufficient"


def test_matrix_missing_reset_error_skips_main_and_preserves_previous_key() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "pinch_insufficient_feedback": {"enabled": True, "channel_list": [2]},
                "reset_before_output_change": {
                    "enabled": True,
                    "missing_reset_policy": "error",
                },
            },
        },
        worker_factory=worker,
    )
    runtime.start()
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_INSUFFICIENT"), snapshot=None)

    reset_row, main_row = runtime.records_snapshot()[-2:]
    assert reset_row["send_status"] == "error"
    assert reset_row["not_sent_reason"] == "missing_matrix_reset_mapping"
    assert main_row["send_status"] == "skipped"
    assert main_row["not_sent_reason"] == "missing_matrix_reset_mapping"
    assert runtime._previous_matrix_output_key == "contact_valid"
    assert len(worker.instances[0].sequences) == 1


def test_matrix_reset_send_failure_aborts_main_and_rolls_back_previous_key() -> None:
    worker = _FakeWorkerFactory(fail_reset=True)
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "pinch_insufficient_feedback": {"enabled": True, "channel_list": [2]},
                "reset_before_output_change": {
                    "enabled": True,
                    "reset_map": {
                        "contact_valid": {"channel_list": [10]},
                    },
                },
            },
        },
        worker_factory=worker,
    )
    runtime.start()
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_INSUFFICIENT"), snapshot=None)

    assert runtime._previous_matrix_output_key == "pinch_insufficient"
    worker.instances[0].trigger_reset_failure()

    reset_row, main_row = runtime.records_snapshot()[-2:]
    assert reset_row["send_status"] == "send_failed"
    assert reset_row["not_sent_reason"] == "reset_send_failed"
    assert main_row["send_status"] == "skipped"
    assert main_row["not_sent_reason"] == "reset_failed"
    assert runtime._previous_matrix_output_key == "contact_valid"


def test_matrix_same_output_continuous_resend_does_not_reset() -> None:
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
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "reset_before_output_change": {
                    "enabled": True,
                    "reset_map": {
                        "contact_valid": {"channel_list": [10]},
                    },
                },
            },
        },
        worker_factory=_FakeWorkerFactory(),
        monotonic_ms_fn=clock.now,
    )
    runtime.start()
    frame = _trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID")
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=frame, snapshot=None)
    clock.value = 120.0
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=frame, snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["haptic_type"] for row in rows] == [
        "matrix_contact_valid",
        "matrix_contact_valid",
    ]


def test_matrix_previous_key_updates_only_when_main_sequence_is_queued() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "pinch_insufficient_feedback": {"enabled": True, "channel_list": [2]},
            },
        },
        worker_factory=worker,
    )
    runtime.start()
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)
    assert runtime._previous_matrix_output_key == "contact_valid"

    worker.instances[0].reject_sequence = True
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_INSUFFICIENT"), snapshot=None)

    assert runtime.records_snapshot()[-1]["send_status"] == "queue_full"
    assert runtime._previous_matrix_output_key == "contact_valid"


def test_matrix_transition_to_none_can_queue_reset_without_empty_main_packet() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "contact_valid_feedback": {"enabled": True, "channel_list": [1]},
                "reset_before_output_change": {
                    "enabled": True,
                    "apply_on_transition_to_none": True,
                    "reset_map": {
                        "contact_valid": {"channel_list": [10]},
                    },
                },
            },
        },
        worker_factory=worker,
    )
    runtime.start()
    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(contact_state="INSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(contact_state="OUTSIDE_BLOCK", pinch_state="PINCH_VALID"), snapshot=None)

    rows = runtime.records_snapshot()
    assert rows[-2]["haptic_phase"] == "state_end"
    assert rows[-1]["haptic_type"] == "matrix_reset_before_output_change"
    assert rows[-1]["next_matrix_output_key"] is None
    assert rows[-1]["channel_list"] == "[10]"
    assert len(worker.instances[0].sequences[-1]) == 1
    assert runtime._previous_matrix_output_key == "contact_valid"


def test_vibration_contact_records_are_one_shot_line_commands() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

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
    assert [row["vibration_command_label"] for row in rows] == [
        "contact_enter",
        "contact_exit",
    ]
    assert [row["vibration_command"] for row in rows] == [1, 2]
    assert [row["sent_payload"] for row in rows] == ["1\\n", "2\\n"]
    assert [row["payload_hex"] for row in rows] == ["310a", "320a"]
    assert all(row["send_status"] == "sent" for row in rows)
    assert worker.instances[0].packets == [b"1\n", b"2\n"]


def test_vibration_slip_start_and_end_are_state_commands() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=False), snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["haptic_phase"] for row in rows] == ["state_start", "state_end"]
    assert all(row["haptic_type"] == "vibration_slip" for row in rows)
    assert [row["vibration_command_label"] for row in rows] == ["slip_start", "slip_end"]
    assert [row["sent_payload"] for row in rows] == ["3\\n", "4\\n"]
    assert worker.instances[0].packets == [b"3\n", b"4\n"]


def test_vibration_contact_exit_covers_same_frame_slip_end_when_release_queued() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=False, events=("contact_exit",)), snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["vibration_command_label"] for row in rows] == [
        "slip_start",
        "contact_exit",
        "slip_end",
    ]
    assert rows[-1]["send_status"] == "skipped"
    assert rows[-1]["not_sent_reason"] == "covered_by_contact_exit"
    assert '"covered_by_contact_exit": true' in rows[-1]["details_json"]
    assert '"coverage_basis": "queued"' in rows[-1]["details_json"]
    assert worker.instances[0].packets == [b"3\n", b"2\n"]


def test_vibration_contact_exit_does_not_cover_slip_end_when_release_disabled() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "enable_release": False,
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=False, events=("contact_exit",)), snapshot=None)

    rows = runtime.records_snapshot()
    assert rows[1]["vibration_command_label"] == "contact_exit"
    assert rows[1]["send_status"] == "skipped"
    assert rows[1]["not_sent_reason"] == "release_disabled"
    assert rows[2]["vibration_command_label"] == "slip_end"
    assert rows[2]["send_status"] == "sent"
    assert worker.instances[0].packets == [b"3\n", b"4\n"]


def test_vibration_one_shot_reasserts_active_slip_once_on_next_update() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT", events=("contact_enter",)), snapshot=None)
    runtime.process_frame(frame_index=3, source_frame_id=None, sample=_sample(0.2), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)
    runtime.process_frame(frame_index=4, source_frame_id=None, sample=_sample(0.3), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)

    rows = runtime.records_snapshot()
    assert [row["haptic_phase"] for row in rows] == [
        "state_start",
        "one_shot",
        "state_reassert",
    ]
    assert [row["sent_payload"] for row in rows] == ["3\\n", "1\\n", "3\\n"]
    assert worker.instances[0].packets == [b"3\n", b"1\n", b"3\n"]
    contact_details = rows[1]["details_json"]
    reassert_details = rows[2]["details_json"]
    assert '"interrupted_slip": true' in contact_details
    assert '"reassert_after_one_shot": true' in reassert_details


def test_slip_global_disable_skips_all_slip_vibration() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "enable_slip": False,
            },
        }
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT"), snapshot=None)

    row = runtime.records_snapshot()[0]
    assert row["send_status"] == "skipped"
    assert row["not_sent_reason"] == "slip_disabled"


def test_pinch_insufficient_slip_requires_prior_valid_grab_policy_skips_loose_touch() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "pinch_insufficient_slip_policy": "requires_prior_grab",
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    for frame_index in (1, 2):
        runtime.process_frame(
            frame_index=frame_index,
            source_frame_id=None,
            sample=_sample(),
            trial_result=_trial_result(
                slip=True,
                slip_reason="PINCH_INSUFFICIENT",
                contact_state="INSIDE_BLOCK",
                pinch_state="PINCH_INSUFFICIENT",
            ),
            snapshot=None,
        )

    rows = runtime.records_snapshot()
    assert len(rows) == 1
    assert rows[0]["send_status"] == "skipped"
    assert rows[0]["not_sent_reason"] == "need_pinch_requires_valid_grab"
    assert '"need_pinch_active": true' in rows[0]["details_json"]
    assert runtime.summary()["need_pinch_requires_valid_grab_count"] == 1
    assert worker.instances[0].packets == []


def test_pinch_insufficient_slip_requires_prior_valid_grab_policy_allows_after_valid_grab() -> None:
    worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "pinch_insufficient_slip_policy": "requires_prior_grab",
            },
        },
        vibration_worker_factory=worker,
    )
    runtime.start()

    runtime.process_frame(
        frame_index=1,
        source_frame_id=None,
        sample=_sample(),
        trial_result=_trial_result(
            slip=False,
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_VALID",
        ),
        snapshot=None,
    )
    runtime.process_frame(
        frame_index=2,
        source_frame_id=None,
        sample=_sample(0.1),
        trial_result=_trial_result(
            slip=True,
            slip_reason="PINCH_INSUFFICIENT",
            contact_state="INSIDE_BLOCK",
            pinch_state="PINCH_INSUFFICIENT",
        ),
        snapshot=None,
    )

    rows = runtime.records_snapshot()
    assert len(rows) == 1
    assert rows[0]["send_status"] == "sent"
    assert rows[0]["vibration_command_label"] == "slip_start"
    assert worker.instances[0].packets == [b"3\n"]


def test_track_blocked_slip_outside_target_can_be_disabled_without_disabling_pinch_slip() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "enable_slip_track_blocked": False,
            },
        }
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="TRACK_BLOCKED", block_center=Vec3(2.0, 2.0, 0.0)), snapshot=None)
    runtime.process_frame(frame_index=2, source_frame_id=None, sample=_sample(0.1), trial_result=_trial_result(slip=True, slip_reason="PINCH_INSUFFICIENT", block_center=Vec3(2.0, 2.0, 0.0)), snapshot=None)

    rows = runtime.records_snapshot()
    assert rows[0]["not_sent_reason"] == "slip_track_blocked_disabled"
    assert rows[1]["send_status"] == "not_connected"
    assert rows[1]["not_sent_reason"] == "vibration_not_connected"


def test_track_blocked_slip_inside_target_uses_target_region_flag() -> None:
    runtime = _runtime(
        {
            "enabled": True,
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "enable_slip_track_blocked": False,
                "enable_slip_track_blocked_in_target_region": True,
            },
        }
    )

    runtime.process_frame(frame_index=1, source_frame_id=None, sample=_sample(), trial_result=_trial_result(slip=True, slip_reason="TRACK_BLOCKED", block_center=Vec3(0.5, 0.5, 0.0)), snapshot=None)

    row = runtime.records_snapshot()[0]
    assert row["send_status"] == "not_connected"
    assert row["not_sent_reason"] == "vibration_not_connected"


def test_matrix_and_vibration_targets_do_not_suppress_each_other() -> None:
    matrix_worker = _FakeWorkerFactory()
    vibration_worker = _FakeWorkerFactory()
    runtime = _runtime(
        {
            "enabled": True,
            "matrix": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
                "direction_channel_map": {"X_NEG": [1]},
            },
            "vibration": {
                "enabled": True,
                "host": "127.0.0.1",
                "startup_settle_seconds": 0.0,
            },
        },
        worker_factory=matrix_worker,
        vibration_worker_factory=vibration_worker,
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
    vibration_worker_factory: Any = None,
    monotonic_ms_fn: Any = None,
) -> HapticRuntime:
    return HapticRuntime(
        trial_id="trial",
        haptic_config=haptic_config_from_dict(payload),
        trial_config={"target_region": {"min": [0, 0, -1], "max": [1, 1, 1]}},
        worker_factory=worker_factory,
        vibration_worker_factory=vibration_worker_factory,
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
    blocked_surfaces: tuple[str, ...] = (),
    contact_state: str = "OUTSIDE_BLOCK",
    pinch_state: str = "PINCH_UNKNOWN",
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
            contact_state=contact_state,
            pinch_state=pinch_state,
            feedback_state=SimpleNamespace(
                tracking_valid=True,
                recovery_frame=False,
                track_state=track_state,
                blocked_info=SimpleNamespace(all_blocked_surfaces=blocked_surfaces),
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
    def __init__(self, *, fail_reset: bool = False, reject_sequence: bool = False, **_: Any) -> None:
        self.connected = False
        self.packets: list[bytes] = []
        self.sequences: list[list[bytes]] = []
        self.fail_reset = fail_reset
        self.reject_sequence = reject_sequence
        self._pending_reset_failure: tuple[Any, ...] | None = None

    def start(self) -> None:
        self.connected = True

    def submit(self, record: Any, packet: bytes, **_: Any) -> bool:
        self.packets.append(packet)
        record.queued_monotonic_ms = 1.0
        record.sent_monotonic_ms = 2.0
        record.success = True
        record.send_status = "sent"
        record.not_sent_reason = None
        return True

    def submit_sequence(
        self,
        steps: tuple[Any, ...],
        *,
        on_reset_failure: Any = None,
    ) -> bool:
        self.sequences.append([step.packet for step in steps])
        if self.reject_sequence:
            for step in steps:
                step.record.success = False
                step.record.send_status = "queue_full"
                step.record.not_sent_reason = "queue_full"
            return False
        if self.fail_reset and any(step.role == "reset" for step in steps):
            for step in steps:
                step.record.send_status = "queued"
                step.record.not_sent_reason = None
            self._pending_reset_failure = (steps, on_reset_failure)
            return True
        for step in steps:
            self.submit(step.record, step.packet)
        return True

    def trigger_reset_failure(self) -> None:
        assert self._pending_reset_failure is not None
        steps, callback = self._pending_reset_failure
        reset_step = next(step for step in steps if step.role == "reset")
        reset_step.record.success = False
        reset_step.record.send_status = "send_failed"
        reset_step.record.not_sent_reason = "reset_send_failed"
        reset_step.record.error = "fake reset failure"
        for step in steps:
            if step.role == "main":
                step.record.success = False
                step.record.send_status = "skipped"
                step.record.not_sent_reason = "reset_failed"
        if callback is not None:
            callback()
        self._pending_reset_failure = None

    def stop(self) -> None:
        self.connected = False


class _FakeWorkerFactory:
    def __init__(
        self,
        *,
        fail_reset: bool = False,
        reject_sequence: bool = False,
    ) -> None:
        self.instances: list[_FakeWorker] = []
        self.fail_reset = fail_reset
        self.reject_sequence = reject_sequence

    def __call__(self, **kwargs: Any) -> _FakeWorker:
        del kwargs
        worker = _FakeWorker(
            fail_reset=self.fail_reset,
            reject_sequence=self.reject_sequence,
        )
        self.instances.append(worker)
        return worker
