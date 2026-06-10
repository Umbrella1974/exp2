"""State labels for Stage 5C integrated live sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto


class LiveSessionPhase(Enum):
    """Coarse phase of an integrated live session."""

    WAITING_FOR_STREAM = auto()
    WAITING_FOR_VALID_TRACKER = auto()
    WAITING_FOR_VALID_PINCH = auto()
    READY_FOR_CALIBRATION = auto()
    CALIBRATING_ORIGIN = auto()
    CALIBRATING_LONG_LINE = auto()
    CALIBRATING_WIDTH_LINE = auto()
    CALIBRATING_DIAGONAL_LINE = auto()
    CALIBRATION_REVIEW = auto()
    CALIBRATION_FAILED = auto()
    READY_FOR_TRIAL = auto()
    PRE_TRIAL_PINCH_THRESHOLD_CALIBRATION = auto()
    TRIAL_RUNNING = auto()
    TRIAL_ENDED = auto()
    SAVING = auto()
    ERROR = auto()
    STOPPED = auto()


@dataclass(frozen=True)
class LiveSessionStatus:
    """Display and logging snapshot for live session progress."""

    phase: LiveSessionPhase
    message: str = ""
    frame_index: int | None = None
    tracker_valid: bool = False
    hand_valid: bool = False
    pinch_valid: bool = False
    calibration_id: str | None = None
    map_id: str | None = None
    trial_id: str | int | None = None
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a JSON-friendly status payload."""

        return {
            "phase": self.phase.name,
            "message": self.message,
            "frame_index": self.frame_index,
            "tracker_valid": self.tracker_valid,
            "hand_valid": self.hand_valid,
            "pinch_valid": self.pinch_valid,
            "calibration_id": self.calibration_id,
            "map_id": self.map_id,
            "trial_id": self.trial_id,
            "warnings": list(self.warnings),
            "errors": list(self.errors),
        }
