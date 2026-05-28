"""Best-effort live visual/text display for MVP live preview."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any

from dashboard_snapshot import DashboardSnapshot


GUIDANCE_AXIS_EPSILON = 0.005


@dataclass(frozen=True)
class GuidanceInfo:
    """Display-only guidance from the current pinch point to the block."""

    available: bool
    message: str
    dx: float | None = None
    dy: float | None = None
    dz: float | None = None
    center_distance: float | None = None
    footprint_edge_distance_xy: float | None = None
    block_edge_distance_xyz: float | None = None
    pinch_inside_footprint_xy: bool = False
    pinch_inside_block_aabb: bool = False


class NullLiveVisualDisplay:
    """Display that intentionally does nothing."""

    mode = "none"

    def update(self, snapshot: DashboardSnapshot) -> None:
        del snapshot

    def close(self) -> None:
        pass


class TextLiveVisualDisplay:
    """Text fallback display that prints readable labels."""

    mode = "text"

    def __init__(self, *, print_every: int = 30) -> None:
        self.print_every = max(1, int(print_every))

    def update(self, snapshot: DashboardSnapshot) -> None:
        if snapshot.frame_index % self.print_every != 0:
            return
        print(build_compact_status_line(snapshot))

    def close(self) -> None:
        pass


class BestEffortLiveVisualDisplay:
    """Wrapper that falls back to text if matplotlib display fails."""

    def __init__(self, primary: Any, fallback: TextLiveVisualDisplay) -> None:
        self.primary = primary
        self.fallback = fallback
        self.mode = getattr(primary, "mode", "unknown")
        self.fell_back = False

    def update(self, snapshot: DashboardSnapshot) -> None:
        if self.fell_back:
            self.fallback.update(snapshot)
            return
        try:
            self.primary.update(snapshot)
        except Exception as exc:
            self.fell_back = True
            self.mode = "text"
            print(f"[VISUAL] matplotlib update failed; falling back to text mode: {exc}")
            self.fallback.update(snapshot)

    def close(self) -> None:
        try:
            self.primary.close()
        except Exception:
            pass


class MatplotlibLiveVisualDisplay:
    """Minimal 2D task x-y matplotlib display."""

    mode = "matplotlib"

    def __init__(
        self,
        *,
        map_config: Any,
        history_size: int = 300,
    ) -> None:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle

        self.plt = plt
        self.Rectangle = Rectangle
        self.map_config = map_config
        self.history_size = max(1, int(history_size))
        self.pinch_history: deque[list[float]] = deque(maxlen=self.history_size)
        self.block_history: deque[list[float]] = deque(maxlen=self.history_size)

        self.fig, (self.ax, self.text_ax) = plt.subplots(
            1,
            2,
            figsize=(11, 6),
            gridspec_kw={"width_ratios": [3, 2]},
        )
        self.text_ax.axis("off")
        self._setup_track_axes()
        self.pinch_line, = self.ax.plot([], [], color="#2563eb", linewidth=1.5, label="pinch path")
        self.block_line, = self.ax.plot([], [], color="#f97316", linewidth=1.5, label="block path")
        self.guidance_line, = self.ax.plot(
            [],
            [],
            color="#0f172a",
            linewidth=1.3,
            linestyle="--",
            label="pinch to block",
        )
        self.guidance_arrow = self.ax.annotate(
            "",
            xy=(0.0, 0.0),
            xytext=(0.0, 0.0),
            arrowprops={"arrowstyle": "->", "color": "#0f172a", "linewidth": 1.8},
            visible=False,
        )
        self.pinch_point, = self.ax.plot([], [], marker="o", color="#2563eb", markersize=7, label="pinch cursor")
        self.block_center_point, = self.ax.plot(
            [],
            [],
            marker="s",
            color="#f97316",
            markersize=6,
            label="block center",
        )
        self.block_patch = self.Rectangle((0.0, 0.0), 0.0, 0.0, fill=False, linewidth=2.5)
        self.ax.add_patch(self.block_patch)
        self.pinch_label_text = self.ax.text(
            0.0,
            0.0,
            "HAND",
            fontsize=9,
            fontweight="bold",
            color="#1d4ed8",
            visible=False,
        )
        self.block_label_text = self.ax.text(
            0.0,
            0.0,
            "BLOCK",
            fontsize=9,
            fontweight="bold",
            color="#c2410c",
            visible=False,
        )
        self.guidance_text = self.ax.text(
            0.02,
            0.02,
            "",
            transform=self.ax.transAxes,
            va="bottom",
            ha="left",
            fontsize=10,
            family="monospace",
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "alpha": 0.82, "edgecolor": "#cbd5e1"},
        )
        self.status_text = self.text_ax.text(
            0.0,
            1.0,
            "",
            va="top",
            ha="left",
            fontsize=11,
            family="monospace",
        )
        self.main_text = self.text_ax.text(
            0.0,
            0.08,
            "",
            va="bottom",
            ha="left",
            fontsize=20,
            fontweight="bold",
        )
        self.ax.legend(loc="upper right")
        self.plt.ion()
        self.fig.tight_layout()
        self.fig.canvas.draw_idle()
        self.plt.show(block=False)

    def update(self, snapshot: DashboardSnapshot) -> None:
        color = color_for_snapshot(snapshot)
        guidance = guidance_from_snapshot(snapshot)
        if snapshot.pinch_center_task is not None:
            self.pinch_history.append(snapshot.pinch_center_task)
        if snapshot.block_center_task is not None:
            self.block_history.append(snapshot.block_center_task)

        self._update_line(self.pinch_line, self.pinch_history)
        self._update_line(self.block_line, self.block_history)
        if snapshot.pinch_center_task is not None:
            self.pinch_point.set_data(
                [snapshot.pinch_center_task[0]],
                [snapshot.pinch_center_task[1]],
            )
            self.pinch_point.set_color(color)
            self.pinch_label_text.set_position(
                (snapshot.pinch_center_task[0], snapshot.pinch_center_task[1])
            )
            self.pinch_label_text.set_visible(True)
        else:
            self.pinch_point.set_data([], [])
            self.pinch_label_text.set_visible(False)
        if snapshot.block_center_task is not None and snapshot.block_size is not None:
            center = snapshot.block_center_task
            size = snapshot.block_size
            self.block_patch.set_x(center[0] - size[0] * 0.5)
            self.block_patch.set_y(center[1] - size[1] * 0.5)
            self.block_patch.set_width(size[0])
            self.block_patch.set_height(size[1])
            self.block_patch.set_edgecolor(color)
            self.block_center_point.set_data([center[0]], [center[1]])
            self.block_label_text.set_position((center[0], center[1]))
            self.block_label_text.set_visible(True)
        else:
            self.block_center_point.set_data([], [])
            self.block_label_text.set_visible(False)

        self._update_guidance_overlay(snapshot, guidance)
        self._expand_axes_to_points([snapshot.pinch_center_task, snapshot.block_center_task])

        self.status_text.set_text(build_status_text(snapshot))
        self.main_text.set_text(snapshot.main_state_label)
        self.main_text.set_color(color)
        self.ax.set_title(f"Live Visual Preview - {snapshot.main_state_label}", color=color)
        self.fig.canvas.draw_idle()
        self.fig.canvas.flush_events()
        self.plt.pause(0.001)

    def close(self) -> None:
        self.plt.close(self.fig)

    def _setup_track_axes(self) -> None:
        self.ax.set_aspect("equal", adjustable="box")
        self.ax.set_xlabel("task x (m)")
        self.ax.set_ylabel("task y (m)")
        self.ax.grid(True, alpha=0.25)
        xs: list[float] = []
        ys: list[float] = []
        for box in getattr(self.map_config, "track_boxes", []) or []:
            self._add_box(box, edgecolor="#64748b", label=getattr(box, "label", None) or getattr(box, "id", "track"))
            xs.extend([box.min[0], box.max[0]])
            ys.extend([box.min[1], box.max[1]])
        target = getattr(self.map_config, "target_region", None)
        if target is not None:
            self._add_box(target, edgecolor="#22c55e", linestyle="--", label="target")
            xs.extend([target.min[0], target.max[0]])
            ys.extend([target.min[1], target.max[1]])
        if xs and ys:
            margin = 0.2
            self.ax.set_xlim(min(xs) - margin, max(xs) + margin)
            self.ax.set_ylim(min(ys) - margin, max(ys) + margin)

    def _add_box(
        self,
        box: Any,
        *,
        edgecolor: str,
        label: str,
        linestyle: str = "-",
    ) -> None:
        patch = self.Rectangle(
            (box.min[0], box.min[1]),
            box.max[0] - box.min[0],
            box.max[1] - box.min[1],
            fill=False,
            linewidth=1.6,
            edgecolor=edgecolor,
            linestyle=linestyle,
            label=label,
        )
        self.ax.add_patch(patch)

    @staticmethod
    def _update_line(line: Any, history: deque[list[float]]) -> None:
        if not history:
            line.set_data([], [])
            return
        line.set_data([point[0] for point in history], [point[1] for point in history])

    def _update_guidance_overlay(
        self,
        snapshot: DashboardSnapshot,
        guidance: GuidanceInfo,
    ) -> None:
        if snapshot.pinch_center_task is None or snapshot.block_center_task is None:
            self.guidance_line.set_data([], [])
            self.guidance_arrow.set_visible(False)
            self.guidance_text.set_text(guidance.message)
            return
        pinch = snapshot.pinch_center_task
        block = snapshot.block_center_task
        self.guidance_line.set_data([pinch[0], block[0]], [pinch[1], block[1]])
        self.guidance_arrow.xy = (block[0], block[1])
        self.guidance_arrow.set_position((pinch[0], pinch[1]))
        self.guidance_arrow.set_visible(True)
        if guidance.footprint_edge_distance_xy is None:
            edge_text = "edge_xy=NA edge_xyz=NA"
        else:
            edge_text = (
                f"edge_xy={guidance.footprint_edge_distance_xy:.3f}m "
                f"edge_xyz={guidance.block_edge_distance_xyz:.3f}m"
            )
        self.guidance_text.set_text(f"{guidance.message}\n{edge_text}")

    def _expand_axes_to_points(self, points: list[Any]) -> None:
        finite_points = [_xy_point(point) for point in points]
        finite_points = [point for point in finite_points if point is not None]
        if not finite_points:
            return

        xlim = list(self.ax.get_xlim())
        ylim = list(self.ax.get_ylim())
        width = max(abs(xlim[1] - xlim[0]), 1.0)
        height = max(abs(ylim[1] - ylim[0]), 1.0)
        margin = max(width, height) * 0.12
        changed = False
        for x, y in finite_points:
            if x < xlim[0] + margin:
                xlim[0] = x - margin
                changed = True
            if x > xlim[1] - margin:
                xlim[1] = x + margin
                changed = True
            if y < ylim[0] + margin:
                ylim[0] = y - margin
                changed = True
            if y > ylim[1] - margin:
                ylim[1] = y + margin
                changed = True
        if changed:
            self.ax.set_xlim(xlim[0], xlim[1])
            self.ax.set_ylim(ylim[0], ylim[1])


def create_live_visual_display(
    *,
    show_visual: bool,
    visual_mode: str,
    map_config: Any,
    visual_history: int = 300,
    print_every: int = 30,
) -> Any:
    """Create a visual display with text fallback."""

    if not show_visual:
        return NullLiveVisualDisplay()
    fallback = TextLiveVisualDisplay(print_every=print_every)
    if visual_mode == "text":
        return fallback
    try:
        primary = MatplotlibLiveVisualDisplay(
            map_config=map_config,
            history_size=visual_history,
        )
    except Exception as exc:
        print(f"[VISUAL] matplotlib unavailable; using text mode: {exc}")
        return fallback
    return BestEffortLiveVisualDisplay(primary, fallback)


def guidance_from_snapshot(snapshot: DashboardSnapshot) -> GuidanceInfo:
    """Compute display-only guidance from pinch point to block footprint."""

    pinch = snapshot.pinch_center_task
    block = snapshot.block_center_task
    if pinch is None:
        return GuidanceInfo(False, "GUIDE: HAND NOT VISIBLE")
    if block is None:
        return GuidanceInfo(False, "GUIDE: BLOCK NOT AVAILABLE")
    if len(pinch) < 3 or len(block) < 3:
        return GuidanceInfo(False, "GUIDE: POSITION INCOMPLETE")

    dx = float(block[0]) - float(pinch[0])
    dy = float(block[1]) - float(pinch[1])
    dz = float(block[2]) - float(pinch[2])
    center_distance = math.sqrt(dx * dx + dy * dy + dz * dz)

    half_x = 0.0
    half_y = 0.0
    half_z = 0.0
    if snapshot.block_size is not None and len(snapshot.block_size) >= 2:
        half_x = max(0.0, float(snapshot.block_size[0]) * 0.5)
        half_y = max(0.0, float(snapshot.block_size[1]) * 0.5)
        if len(snapshot.block_size) >= 3:
            half_z = max(0.0, float(snapshot.block_size[2]) * 0.5)
    gap_x = max(abs(dx) - half_x, 0.0)
    gap_y = max(abs(dy) - half_y, 0.0)
    gap_z = max(abs(dz) - half_z, 0.0)
    edge_distance = math.hypot(gap_x, gap_y)
    block_edge_distance = math.sqrt(gap_x * gap_x + gap_y * gap_y + gap_z * gap_z)
    inside_footprint = gap_x <= 0.0 and gap_y <= 0.0
    inside_block = inside_footprint and gap_z <= 0.0

    if inside_block:
        message = "GUIDE: HAND INSIDE BLOCK, PINCH TO GRAB"
    else:
        axes: list[str] = []
        if gap_x > GUIDANCE_AXIS_EPSILON:
            axes.append("+X" if dx > 0.0 else "-X")
        if gap_y > GUIDANCE_AXIS_EPSILON:
            axes.append("+Y" if dy > 0.0 else "-Y")
        if gap_z > GUIDANCE_AXIS_EPSILON:
            axes.append("+Z" if dz > 0.0 else "-Z")
        if axes:
            message = "GUIDE: MOVE " + " ".join(axes)
        else:
            message = "GUIDE: NEAR BLOCK EDGE"

    return GuidanceInfo(
        True,
        message,
        dx=dx,
        dy=dy,
        dz=dz,
        center_distance=center_distance,
        footprint_edge_distance_xy=edge_distance,
        block_edge_distance_xyz=block_edge_distance,
        pinch_inside_footprint_xy=inside_footprint,
        pinch_inside_block_aabb=inside_block,
    )


def build_status_text(snapshot: DashboardSnapshot) -> str:
    """Build the multiline text shown by matplotlib status panel."""

    guidance = guidance_from_snapshot(snapshot)
    return "\n".join(
        [
            f"MAIN STATE: {snapshot.main_state_label}",
            f"CONTACT: {snapshot.contact_label}",
            f"PINCH: {snapshot.pinch_label}",
            f"MOTION: {snapshot.interaction_label}",
            f"STOP: {snapshot.stop_reason}",
            f"TRACK: {snapshot.track_state}",
            snapshot.feedback_label,
            f"PINCH TASK: {_format_point(snapshot.pinch_center_task)}",
            f"BLOCK TASK: {_format_point(snapshot.block_center_task)}",
            f"TO BLOCK: {_format_guidance_delta(guidance)}",
            guidance.message,
            f"tracker_valid={int(snapshot.tracker_valid)} hand_valid={int(snapshot.hand_valid)}",
            f"slip_active={int(snapshot.slip_active)} blocked_force={int(snapshot.blocked_force_active)}",
            f"processing_latency_ms={snapshot.processing_latency_ms}",
        ]
    )


def build_compact_status_line(snapshot: DashboardSnapshot) -> str:
    """Build the compact text-mode status line."""

    distance = "NA" if snapshot.pinch_distance is None else f"{snapshot.pinch_distance:.3f}m"
    guidance = guidance_from_snapshot(snapshot)
    return (
        f"frame={snapshot.frame_index} "
        f"MAIN={_compact(snapshot.main_state_label)} "
        f"CONTACT={_compact(snapshot.contact_label)} "
        f"PINCH={snapshot.pinch_state} dist={distance} "
        f"MOTION={snapshot.block_motion_state} "
        f"STOP={snapshot.stop_reason} "
        f"FEEDBACK={snapshot.logical_haptic_label} "
        f"PINCH_POS={_format_point(snapshot.pinch_center_task, compact=True)} "
        f"BLOCK_POS={_format_point(snapshot.block_center_task, compact=True)} "
        f"{_format_guidance_delta(guidance, compact=True)} "
        f"{guidance.message}"
    )


def color_for_snapshot(snapshot: DashboardSnapshot) -> str:
    """Return auxiliary color for a snapshot; labels remain authoritative."""

    state = snapshot.main_state_label
    if state == "TRACKING INVALID":
        return "#111827"
    if state == "LARGE DELTA":
        return "#7c3aed"
    if state == "BLOCKED":
        return "#dc2626"
    if state in {"SLIP", "PINCH INSUFFICIENT"}:
        return "#f97316"
    if state == "MOVING":
        return "#16a34a"
    if state == "CONTACT RELEASE":
        return "#6b7280"
    return "#2563eb"


def _format_guidance_delta(guidance: GuidanceInfo, *, compact: bool = False) -> str:
    if not guidance.available:
        return "TO_BLOCK=NA" if compact else "dx=NA dy=NA dz=NA edge=NA center=NA"
    if compact:
        return (
            f"TO_BLOCK=dx:{guidance.dx:.3f},dy:{guidance.dy:.3f},"
            f"dz:{guidance.dz:.3f},edge_xy:{guidance.footprint_edge_distance_xy:.3f},"
            f"edge_xyz:{guidance.block_edge_distance_xyz:.3f}"
        )
    return (
        f"dx={guidance.dx:.3f} m, dy={guidance.dy:.3f} m, dz={guidance.dz:.3f} m, "
        f"edge_xy={guidance.footprint_edge_distance_xy:.3f} m, "
        f"edge_xyz={guidance.block_edge_distance_xyz:.3f} m, "
        f"center={guidance.center_distance:.3f} m"
    )


def _format_point(point: Any, *, compact: bool = False) -> str:
    prefix = "" if compact else "x/y/z="
    if point is None:
        return "NA"
    try:
        values = [float(point[0]), float(point[1]), float(point[2])]
    except (TypeError, ValueError, IndexError):
        return "NA"
    if compact:
        return f"({values[0]:.3f},{values[1]:.3f},{values[2]:.3f})"
    return f"{prefix}({values[0]:.3f}, {values[1]:.3f}, {values[2]:.3f}) m"


def _xy_point(point: Any) -> tuple[float, float] | None:
    if point is None:
        return None
    try:
        x = float(point[0])
        y = float(point[1])
    except (TypeError, ValueError, IndexError):
        return None
    if not math.isfinite(x) or not math.isfinite(y):
        return None
    return x, y


def _compact(value: str) -> str:
    return value.replace(" ", "_")
