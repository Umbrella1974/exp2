"""Shared data models for the constrained block interaction engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from math import sqrt


@dataclass(frozen=True)
class Vec3:
    """Simple immutable 3D vector used across the engine."""

    x: float
    y: float
    z: float

    def __add__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x + other.x, self.y + other.y, self.z + other.z)

    def __sub__(self, other: "Vec3") -> "Vec3":
        return Vec3(self.x - other.x, self.y - other.y, self.z - other.z)

    def scale(self, scalar: float) -> "Vec3":
        return Vec3(self.x * scalar, self.y * scalar, self.z * scalar)

    def norm(self) -> float:
        return sqrt((self.x * self.x) + (self.y * self.y) + (self.z * self.z))

    def distance_to(self, other: "Vec3") -> float:
        return (self - other).norm()

    def components(self) -> tuple[float, float, float]:
        return (self.x, self.y, self.z)

    @staticmethod
    def zero() -> "Vec3":
        return Vec3(0.0, 0.0, 0.0)


@dataclass(frozen=True)
class Box3D:
    """Axis-aligned 3D box represented by center and size."""

    center: Vec3
    size: Vec3

    @property
    def half_size(self) -> Vec3:
        return self.size.scale(0.5)

    @property
    def min_corner(self) -> Vec3:
        half = self.half_size
        return Vec3(
            self.center.x - half.x,
            self.center.y - half.y,
            self.center.z - half.z,
        )

    @property
    def max_corner(self) -> Vec3:
        half = self.half_size
        return Vec3(
            self.center.x + half.x,
            self.center.y + half.y,
            self.center.z + half.z,
        )


@dataclass(frozen=True)
class TrackRegion:
    """Union of axis-aligned boxes that defines the legal track volume."""

    boxes: tuple[Box3D, ...]


class ContactState(Enum):
    """Whether the pinch center is inside the current block box."""

    OUTSIDE_BLOCK = auto()
    INSIDE_BLOCK = auto()


class PinchState(Enum):
    """Pinch hysteresis result."""

    PINCH_VALID = auto()
    PINCH_INSUFFICIENT = auto()
    PINCH_UNKNOWN = auto()


class BlockMotionState(Enum):
    """Primary interaction state for the block.

    STOPPED_BY_PINCH and STOPPED_BY_TRACK are kept as reserved/deprecated
    compatibility values. Stage 1 logic should express stop reasons with
    StopReason instead of emitting those enum members.
    """

    FREE_VISIBLE = auto()
    CONTACT_HIDDEN = auto()
    GRABBED_MOVING = auto()
    GRABBED_BLOCKED = auto()
    GRABBED_PINCH_INSUFFICIENT = auto()
    STOPPED_BY_PINCH = auto()
    STOPPED_BY_TRACK = auto()
    STOPPED_BY_LARGE_DELTA = auto()


class TrackState(Enum):
    """Track constraint state for the current frame."""

    INSIDE_TRACK = auto()
    BLOCKED_X_POS = auto()
    BLOCKED_X_NEG = auto()
    BLOCKED_Y_POS = auto()
    BLOCKED_Y_NEG = auto()
    BLOCKED_Z_POS = auto()
    BLOCKED_Z_NEG = auto()
    HAND_DELTA_TOO_LARGE = auto()


class DetachState(Enum):
    """Detach classification for inside-to-outside transitions."""

    NONE = auto()
    ACTIVE_RELEASE = auto()
    FORCED_DETACH = auto()
    UNEXPECTED_DETACH = auto()


class StopReason(Enum):
    """Reason the block did not move normally on the frame."""

    NONE = auto()
    PINCH_INSUFFICIENT = auto()
    TRACK_BLOCKED = auto()
    LARGE_DELTA = auto()
    TRACKING_INVALID = auto()


class Surface(Enum):
    """Named axis-aligned blocked surface."""

    X_POS = auto()
    X_NEG = auto()
    Y_POS = auto()
    Y_NEG = auto()
    Z_POS = auto()
    Z_NEG = auto()


@dataclass(frozen=True)
class BlockedInfo:
    """Geometric feedback for an out-of-track candidate or point.

    blocked_vector is defined as candidate - clamped_point. The primary blocked
    surface is the sign of the largest blocked_vector component. blocked_distance
    is the Euclidean norm of blocked_vector, not a normalized value.
    """

    primary_blocked_surface: Surface | None
    primary_blocked_amount: float
    blocked_distance: float
    blocked_vector: Vec3
    all_blocked_surfaces: tuple[Surface, ...]


@dataclass(frozen=True)
class ClampResult:
    """Result of clamping a segment end to the track union."""

    clamped_point: Vec3
    end_inside_track: bool
    blocked_info: BlockedInfo | None = None


@dataclass(frozen=True)
class BlockState:
    """Current visible and kinematic state of the block."""

    center: Vec3
    size: Vec3
    visible: bool
    motion_state: BlockMotionState


@dataclass(frozen=True)
class FeedbackState:
    """Per-frame feedback and stop information."""

    tracking_valid: bool
    recovery_frame: bool
    stop_reason: StopReason = StopReason.NONE
    track_state: TrackState = TrackState.INSIDE_TRACK
    detach_state: DetachState = DetachState.NONE
    hand_delta: Vec3 | None = None
    candidate_block_center: Vec3 | None = None
    blocked_info: BlockedInfo | None = None
    boundary_lock_active: bool = False
    boundary_lock_surface: Surface | None = None
    boundary_lock_escape_progress: float = 0.0
    boundary_lock_unlock_delta_m: float | None = None
    boundary_lock_event: str = "none"


@dataclass(frozen=True)
class FrameInput:
    """External input consumed by the block controller."""

    time: float
    pinch_center_task: Vec3 | None
    pinch_distance: float | None
    tracker_valid: bool
    subject_end: bool = False


@dataclass(frozen=True)
class DetachCounts:
    """Running detach counters maintained by the controller."""

    active_release_count: int = 0
    forced_detach_count: int = 0
    unexpected_detach_count: int = 0
    total_detach_count: int = 0


class HapticEventType(Enum):
    """Discrete haptic event types."""

    CONTACT_ENTER = auto()
    CONTACT_EXIT = auto()


class SlipReason(Enum):
    """Reason for continuous slip feedback.

    BOTH is reserved for future use and should not be emitted by Stage 1 logic.
    """

    PINCH_INSUFFICIENT = auto()
    TRACK_BLOCKED = auto()
    BOTH = auto()


@dataclass(frozen=True)
class HapticEvent:
    """Discrete haptic event emitted from core state transitions."""

    time: float
    event_type: HapticEventType
    detach_state: DetachState = DetachState.NONE


@dataclass(frozen=True)
class HapticFeedbackState:
    """Continuous haptic feedback state derived from a frame output."""

    slip_active: bool = False
    slip_reason: SlipReason | None = None
    blocked_force_active: bool = False
    force_vector_task: Vec3 | None = None
    force_magnitude: float = 0.0
    primary_blocked_surface: Surface | None = None
    primary_blocked_amount: float = 0.0


@dataclass
class FrameOutput:
    """Full per-frame output produced by the controller."""

    time: float
    pinch_center_task: Vec3 | None
    pinch_distance: float | None
    block_state: BlockState
    contact_state: ContactState
    pinch_state: PinchState
    feedback_state: FeedbackState
    detach_counts: DetachCounts = field(default_factory=DetachCounts)
    haptic_feedback: HapticFeedbackState = field(default_factory=HapticFeedbackState)
    events: tuple[HapticEvent, ...] = ()
