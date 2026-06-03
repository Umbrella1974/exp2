"""Pure-Python debug GUI view models.

This module contains no GUI imports. It converts DashboardSnapshot plus scene
metadata into simple JSON-safe structures that PySide/pyqtgraph can render.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any

from visual_profile import (
    DEBUG_ALL,
    EXPERIMENT_BLANK,
    EXPERIMENT_VISIBILITY_FEEDBACK,
    resolve_visual_profile,
)


@dataclass(frozen=True)
class DebugBox2D:
    """Axis-aligned x-y box for debug rendering."""

    box_id: str
    label: str
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    order: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DebugSceneView:
    """Static scene information used by the debug GUI."""

    map_id: str
    block_initial_center_task: list[float] | None
    block_size: list[float] | None
    track_boxes: tuple[DebugBox2D, ...] = ()
    target_region: DebugBox2D | None = None
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["track_boxes"] = [box.to_dict() for box in self.track_boxes]
        payload["target_region"] = self.target_region.to_dict() if self.target_region else None
        return payload


@dataclass(frozen=True)
class DebugRuntimeStats:
    """Optional runtime diagnostics displayed beside snapshots."""

    mode: str = "replay"
    snapshot_age_seconds: float | None = None
    gui_fps: float | None = None
    render_lag_ms: float | None = None
    total_received_frames: int | None = None
    parse_error_count: int | None = None
    raw_dropped_frame_count: int | None = None
    overwritten_snapshot_count: int | None = None
    receive_fps: float | None = None
    warnings: tuple[str, ...] = ()

    @property
    def dropped_frame_count(self) -> int | None:
        """Compatibility alias for raw stream dropped frames."""

        return self.raw_dropped_frame_count


@dataclass(frozen=True)
class DebugViewRange:
    """x-y view range for auto-scaling a task-space plot."""

    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


@dataclass(frozen=True)
class DebugViewModel:
    """Complete display model consumed by the GUI."""

    frame_index: int
    sample_time: float | None
    mode: str
    map_id: str
    calibration_id: str
    tracker_valid: bool
    hand_valid: bool
    pinch_valid: bool
    pinch_distance: float | None
    pinch_center_task: list[float] | None
    block_center_task: list[float] | None
    block_size: list[float] | None
    block_visible: bool
    delta_task: list[float] | None
    distance_to_block_center: float | None
    main_state_label: str
    contact_label: str
    release_label: str
    interaction_label: str
    feedback_label: str
    status_line: str
    scene: DebugSceneView | None
    view_range: DebugViewRange
    status_lines: tuple[str, ...]
    visual_profile: str
    show_track: bool
    show_target: bool
    show_initial_block: bool
    show_block: bool
    show_pinch: bool
    show_block_pinch_line: bool
    status_panel_visible: bool
    axes_visible: bool
    grid_visible: bool
    runtime: DebugRuntimeStats = field(default_factory=DebugRuntimeStats)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scene"] = self.scene.to_dict() if self.scene else None
        payload["view_range"] = self.view_range.to_dict()
        return payload


def scene_view_from_trial_config(trial_config: dict[str, Any]) -> DebugSceneView:
    """Extract debug scene geometry from session trial_config.json payload."""

    warnings: list[str] = []
    map_id = str(trial_config.get("map_id", trial_config.get("map_source", "")) or "")
    block_initial = _optional_vec3(trial_config.get("block_initial_center_task"))
    if block_initial is None:
        block_initial = _optional_vec3(_nested_get(trial_config, ("scene_auto", "block_center_task")))
    block_size = _block_size_from_payload(trial_config.get("block_size"))

    track_boxes: list[DebugBox2D] = []
    for index, box_payload in enumerate(trial_config.get("track_boxes", []) or []):
        box = _box_from_payload(box_payload, fallback_id=f"track_{index}")
        if box is not None:
            track_boxes.append(box)
    if not track_boxes:
        bounds_box = _track_bounds_box(trial_config)
        if bounds_box is not None:
            track_boxes.append(bounds_box)
        else:
            warnings.append("No track boxes or track bounds found for debug view.")

    target_region = None
    if isinstance(trial_config.get("target_region"), dict):
        target_region = _box_from_payload(trial_config["target_region"], fallback_id="target")

    return DebugSceneView(
        map_id=map_id,
        block_initial_center_task=block_initial,
        block_size=block_size,
        track_boxes=tuple(track_boxes),
        target_region=target_region,
        warnings=tuple(warnings),
    )


def snapshot_to_debug_view_model(
    snapshot: Any,
    *,
    scene: DebugSceneView | None = None,
    runtime: DebugRuntimeStats | None = None,
    visual_profile: str = DEBUG_ALL,
    status_panel: str = "auto",
    show_axes: str = "auto",
    show_grid: str = "auto",
) -> DebugViewModel:
    """Convert a DashboardSnapshot-like object into a debug view model."""

    runtime = runtime or DebugRuntimeStats()
    visual = resolve_visual_profile(
        visual_profile,
        status_panel=status_panel,
        show_axes=show_axes,
        show_grid=show_grid,
    )
    pinch = _optional_vec3(getattr(snapshot, "pinch_center_task", None))
    block = _optional_vec3(getattr(snapshot, "block_center_task", None))
    block_size = _block_size_from_payload(getattr(snapshot, "block_size", None))
    block_visible = bool(getattr(snapshot, "block_visible", True))
    delta = _delta(pinch, block)
    distance = _norm(delta)
    render_policy = _render_policy(visual.visual_profile, block_visible=block_visible)
    view_range = calculate_debug_view_range(
        scene=scene,
        pinch_center_task=pinch,
        block_center_task=block,
        block_size=block_size,
        include_dynamic=visual.visual_profile == DEBUG_ALL,
    )
    status_lines = build_status_lines(
        snapshot,
        delta_task=delta,
        distance_to_block_center=distance,
        runtime=runtime,
    )
    return DebugViewModel(
        frame_index=int(getattr(snapshot, "frame_index", -1)),
        sample_time=_optional_float(getattr(snapshot, "time", None)),
        mode=runtime.mode,
        map_id=str(getattr(snapshot, "map_id", scene.map_id if scene else "")),
        calibration_id=str(getattr(snapshot, "calibration_id", "")),
        tracker_valid=bool(getattr(snapshot, "tracker_valid", False)),
        hand_valid=bool(getattr(snapshot, "hand_valid", False)),
        pinch_valid=bool(getattr(snapshot, "pinch_valid", False)),
        pinch_distance=_optional_float(getattr(snapshot, "pinch_distance", None)),
        pinch_center_task=pinch,
        block_center_task=block,
        block_size=block_size,
        block_visible=block_visible,
        delta_task=delta,
        distance_to_block_center=distance,
        main_state_label=str(getattr(snapshot, "main_state_label", "")),
        contact_label=str(getattr(snapshot, "contact_label", "")),
        release_label=str(getattr(snapshot, "release_label", "")),
        interaction_label=str(getattr(snapshot, "interaction_label", "")),
        feedback_label=str(getattr(snapshot, "feedback_label", "")),
        status_line=str(getattr(snapshot, "status_line", "")),
        scene=scene,
        view_range=view_range,
        status_lines=status_lines,
        visual_profile=visual.visual_profile,
        show_track=render_policy["show_track"],
        show_target=render_policy["show_target"],
        show_initial_block=render_policy["show_initial_block"],
        show_block=render_policy["show_block"],
        show_pinch=render_policy["show_pinch"],
        show_block_pinch_line=render_policy["show_block_pinch_line"],
        status_panel_visible=visual.effective_status_panel_visible,
        axes_visible=visual.effective_axes_visible,
        grid_visible=visual.effective_grid_visible,
        runtime=runtime,
    )


def calculate_debug_view_range(
    *,
    scene: DebugSceneView | None = None,
    pinch_center_task: list[float] | None = None,
    block_center_task: list[float] | None = None,
    block_size: list[float] | None = None,
    min_span: float = 0.5,
    padding_ratio: float = 0.15,
    include_dynamic: bool = True,
) -> DebugViewRange:
    """Return an x-y range that keeps map, block, and pinch visible."""

    xs: list[float] = []
    ys: list[float] = []
    if scene is not None:
        for box in scene.track_boxes:
            xs.extend([box.min_x, box.max_x])
            ys.extend([box.min_y, box.max_y])
        if scene.target_region is not None:
            xs.extend([scene.target_region.min_x, scene.target_region.max_x])
            ys.extend([scene.target_region.min_y, scene.target_region.max_y])
        _extend_point(xs, ys, scene.block_initial_center_task, scene.block_size)

    if include_dynamic:
        _extend_point(xs, ys, pinch_center_task, None)
        _extend_point(xs, ys, block_center_task, block_size)

    if not xs or not ys:
        return DebugViewRange(-0.5, 0.5, -0.5, 0.5)

    x_min = min(xs)
    x_max = max(xs)
    y_min = min(ys)
    y_max = max(ys)
    x_span = max(x_max - x_min, min_span)
    y_span = max(y_max - y_min, min_span)
    x_mid = (x_min + x_max) * 0.5
    y_mid = (y_min + y_max) * 0.5
    x_half = x_span * (0.5 + padding_ratio)
    y_half = y_span * (0.5 + padding_ratio)
    return DebugViewRange(
        x_min=x_mid - x_half,
        x_max=x_mid + x_half,
        y_min=y_mid - y_half,
        y_max=y_mid + y_half,
    )


def build_status_lines(
    snapshot: Any,
    *,
    delta_task: list[float] | None,
    distance_to_block_center: float | None,
    runtime: DebugRuntimeStats,
) -> tuple[str, ...]:
    """Build readable side-panel status lines without inventing new states."""

    lines = [
        f"mode: {runtime.mode}",
        f"frame: {getattr(snapshot, 'frame_index', 'NA')}",
        f"main: {getattr(snapshot, 'main_state_label', '')}",
        f"tracker_valid: {bool(getattr(snapshot, 'tracker_valid', False))}",
        f"hand_valid: {bool(getattr(snapshot, 'hand_valid', False))}",
        f"pinch_valid: {bool(getattr(snapshot, 'pinch_valid', False))}",
        f"pinch_distance: {_format_optional_float(getattr(snapshot, 'pinch_distance', None), 'm')}",
        f"pinch_task: {_format_vec(getattr(snapshot, 'pinch_center_task', None))}",
        f"block_task: {_format_vec(getattr(snapshot, 'block_center_task', None))}",
        f"block_visible: {bool(getattr(snapshot, 'block_visible', True))}",
        f"delta_task: {_format_vec(delta_task)}",
        f"distance_to_block: {_format_optional_float(distance_to_block_center, 'm')}",
        f"contact: {getattr(snapshot, 'contact_label', '')}",
        f"release: {getattr(snapshot, 'release_label', '')}",
        f"motion: {getattr(snapshot, 'interaction_label', '')}",
        f"feedback: {getattr(snapshot, 'feedback_label', '')}",
        f"stop_reason: {getattr(snapshot, 'stop_reason', '')}",
        f"track_state: {getattr(snapshot, 'track_state', '')}",
        f"pinch_state: {getattr(snapshot, 'pinch_state', '')}",
        f"detach_state: {getattr(snapshot, 'detach_state', '')}",
        f"snapshot_age: {_format_optional_float(runtime.snapshot_age_seconds, 's')}",
        f"gui_fps: {_format_optional_float(runtime.gui_fps, 'Hz')}",
        f"render_lag: {_format_optional_float(runtime.render_lag_ms, 'ms')}",
    ]
    if runtime.total_received_frames is not None:
        lines.append(f"received_frames: {runtime.total_received_frames}")
    if runtime.parse_error_count is not None:
        lines.append(f"parse_errors: {runtime.parse_error_count}")
    if runtime.raw_dropped_frame_count is not None:
        lines.append(f"raw_dropped_frames: {runtime.raw_dropped_frame_count}")
    if runtime.overwritten_snapshot_count is not None:
        lines.append(f"overwritten snapshots: {runtime.overwritten_snapshot_count}")
    if runtime.receive_fps is not None:
        lines.append(f"receive_fps: {_format_optional_float(runtime.receive_fps, 'Hz')}")
    return tuple(lines)


def _render_policy(visual_profile: str, *, block_visible: bool) -> dict[str, bool]:
    if visual_profile == DEBUG_ALL:
        return {
            "show_track": True,
            "show_target": True,
            "show_initial_block": True,
            "show_block": True,
            "show_pinch": True,
            "show_block_pinch_line": True,
        }
    if visual_profile == EXPERIMENT_VISIBILITY_FEEDBACK:
        return {
            "show_track": False,
            "show_target": False,
            "show_initial_block": False,
            "show_block": block_visible,
            "show_pinch": block_visible,
            "show_block_pinch_line": False,
        }
    if visual_profile == EXPERIMENT_BLANK:
        return {
            "show_track": False,
            "show_target": False,
            "show_initial_block": False,
            "show_block": False,
            "show_pinch": False,
            "show_block_pinch_line": False,
        }
    raise ValueError(f"unsupported visual profile: {visual_profile}")


def _track_bounds_box(payload: dict[str, Any]) -> DebugBox2D | None:
    candidates = [
        payload.get("track_bounds_task"),
        payload.get("track_bounds"),
        _nested_get(payload, ("scene_auto", "track_bounds")),
        payload.get("bounds"),
    ]
    for candidate in candidates:
        if isinstance(candidate, dict):
            box = _box_from_payload(
                {
                    "id": "track_bounds",
                    "label": "Track bounds",
                    "min": candidate.get("min"),
                    "max": candidate.get("max"),
                },
                fallback_id="track_bounds",
            )
            if box is not None:
                return box
    return None


def _box_from_payload(payload: dict[str, Any], *, fallback_id: str) -> DebugBox2D | None:
    minimum = _optional_vec3(payload.get("min"))
    maximum = _optional_vec3(payload.get("max"))
    if minimum is None or maximum is None:
        return None
    return DebugBox2D(
        box_id=str(payload.get("id", fallback_id)),
        label=str(payload.get("label", payload.get("id", fallback_id))),
        min_x=float(minimum[0]),
        min_y=float(minimum[1]),
        max_x=float(maximum[0]),
        max_y=float(maximum[1]),
        order=_optional_int(payload.get("order")),
    )


def _extend_point(
    xs: list[float],
    ys: list[float],
    point: list[float] | None,
    size: list[float] | None,
) -> None:
    if point is None:
        return
    if size is None:
        xs.append(float(point[0]))
        ys.append(float(point[1]))
        return
    half_x = float(size[0]) * 0.5
    half_y = float(size[1]) * 0.5
    xs.extend([float(point[0]) - half_x, float(point[0]) + half_x])
    ys.extend([float(point[1]) - half_y, float(point[1]) + half_y])


def _delta(a: list[float] | None, b: list[float] | None) -> list[float] | None:
    if a is None or b is None:
        return None
    return [float(a[index]) - float(b[index]) for index in range(3)]


def _norm(value: list[float] | None) -> float | None:
    if value is None:
        return None
    return math.sqrt(sum(component * component for component in value))


def _block_size_from_payload(value: Any) -> list[float] | None:
    if isinstance(value, int | float):
        return [float(value), float(value), float(value)]
    return _optional_vec3(value)


def _optional_vec3(value: Any) -> list[float] | None:
    if value in (None, ""):
        return None
    if all(hasattr(value, attr) for attr in ("x", "y", "z")):
        return [float(value.x), float(value.y), float(value.z)]
    if hasattr(value, "tolist"):
        value = value.tolist()
    try:
        items = list(value)
    except TypeError:
        return None
    if len(items) != 3:
        return None
    try:
        vector = [float(items[0]), float(items[1]), float(items[2])]
    except (TypeError, ValueError):
        return None
    if not all(math.isfinite(component) for component in vector):
        return None
    return vector


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _nested_get(payload: dict[str, Any], path: tuple[str, ...]) -> Any:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _format_vec(value: Any) -> str:
    vector = _optional_vec3(value)
    if vector is None:
        return "NA"
    return f"({vector[0]:.3f}, {vector[1]:.3f}, {vector[2]:.3f})"


def _format_optional_float(value: Any, unit: str) -> str:
    number = _optional_float(value)
    if number is None:
        return "NA"
    return f"{number:.3f} {unit}"
