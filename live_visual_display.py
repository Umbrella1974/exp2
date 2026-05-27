"""Best-effort live visual/text display for MVP live preview."""

from __future__ import annotations

from collections import deque
from typing import Any

from dashboard_snapshot import DashboardSnapshot


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
        self.pinch_point, = self.ax.plot([], [], marker="o", color="#2563eb", markersize=7, label="pinch cursor")
        self.block_patch = self.Rectangle((0.0, 0.0), 0.0, 0.0, fill=False, linewidth=2.5)
        self.ax.add_patch(self.block_patch)
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
        if snapshot.block_center_task is not None and snapshot.block_size is not None:
            center = snapshot.block_center_task
            size = snapshot.block_size
            self.block_patch.set_x(center[0] - size[0] * 0.5)
            self.block_patch.set_y(center[1] - size[1] * 0.5)
            self.block_patch.set_width(size[0])
            self.block_patch.set_height(size[1])
            self.block_patch.set_edgecolor(color)

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


def build_status_text(snapshot: DashboardSnapshot) -> str:
    """Build the multiline text shown by matplotlib status panel."""

    return "\n".join(
        [
            f"MAIN STATE: {snapshot.main_state_label}",
            f"CONTACT: {snapshot.contact_label}",
            f"PINCH: {snapshot.pinch_label}",
            f"MOTION: {snapshot.interaction_label}",
            f"STOP: {snapshot.stop_reason}",
            f"TRACK: {snapshot.track_state}",
            f"FEEDBACK: {snapshot.feedback_label}",
            f"tracker_valid={int(snapshot.tracker_valid)} hand_valid={int(snapshot.hand_valid)}",
            f"slip_active={int(snapshot.slip_active)} blocked_force={int(snapshot.blocked_force_active)}",
            f"processing_latency_ms={snapshot.processing_latency_ms}",
        ]
    )


def build_compact_status_line(snapshot: DashboardSnapshot) -> str:
    """Build the compact text-mode status line."""

    distance = "NA" if snapshot.pinch_distance is None else f"{snapshot.pinch_distance:.3f}m"
    return (
        f"frame={snapshot.frame_index} "
        f"MAIN={_compact(snapshot.main_state_label)} "
        f"CONTACT={_compact(snapshot.contact_label)} "
        f"PINCH={snapshot.pinch_state} dist={distance} "
        f"MOTION={snapshot.block_motion_state} "
        f"STOP={snapshot.stop_reason} "
        f"FEEDBACK={snapshot.logical_haptic_label}"
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


def _compact(value: str) -> str:
    return value.replace(" ", "_")
