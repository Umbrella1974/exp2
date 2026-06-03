"""PySide6 + pyqtgraph debug GUI for DashboardSnapshot view models.

Imports are intentionally lazy. Importing this module does not require GUI
dependencies; only run_debug_gui() does.
"""

from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Any, Callable

from cue_feedback import LatestCueStore
from debug_view_model import DebugRuntimeStats, DebugSceneView, snapshot_to_debug_view_model
from latest_snapshot_store import LatestSnapshotStore
from visual_profile import DEBUG_ALL


INSTALL_GUI_DEPS_MESSAGE = "Missing GUI dependencies. Install with: pip install PySide6 pyqtgraph"


class GuiDependencyError(RuntimeError):
    """Raised when optional GUI dependencies are not installed."""


RuntimeStatsGetter = Callable[[], dict[str, Any]]
RenderCallback = Callable[[Any, float], None]
CloseCallback = Callable[[], None]
CloseWhen = Callable[[], bool]


def preflight_gui_dependencies() -> None:
    """Fail early if optional GUI dependencies are missing."""

    _load_gui_deps()


def run_debug_gui(
    *,
    snapshot_store: LatestSnapshotStore,
    scene: DebugSceneView | None = None,
    mode: str = "replay",
    gui_fps: float = 30.0,
    title: str = "Exp2 Debug GUI",
    runtime_stats_getter: RuntimeStatsGetter | None = None,
    log_path: str | Path | None = None,
    render_callback: RenderCallback | None = None,
    cue_store: LatestCueStore | None = None,
    close_callback: CloseCallback | None = None,
    close_when: CloseWhen | None = None,
    visual_profile: str = DEBUG_ALL,
    status_panel: str = "auto",
    show_axes: str = "auto",
    show_grid: str = "auto",
) -> int:
    """Run the debug GUI event loop."""

    QtCore, QtGui, QtWidgets, pg = _load_gui_deps()
    app = QtWidgets.QApplication.instance()
    owns_app = app is None
    if app is None:
        app = QtWidgets.QApplication([])
    window = _DebugGuiWindow(
        QtCore=QtCore,
        QtGui=QtGui,
        QtWidgets=QtWidgets,
        pg=pg,
        snapshot_store=snapshot_store,
        scene=scene,
        mode=mode,
        gui_fps=gui_fps,
        title=title,
        runtime_stats_getter=runtime_stats_getter,
        log_path=Path(log_path) if log_path is not None else None,
        render_callback=render_callback,
        cue_store=cue_store,
        close_callback=close_callback,
        close_when=close_when,
        visual_profile=visual_profile,
        status_panel=status_panel,
        show_axes=show_axes,
        show_grid=show_grid,
    )
    window.show()
    if owns_app:
        return int(app.exec())
    return 0


def _load_gui_deps() -> tuple[Any, Any, Any, Any]:
    try:
        from PySide6 import QtCore, QtGui, QtWidgets
        import pyqtgraph as pg
    except ImportError as exc:
        raise GuiDependencyError(INSTALL_GUI_DEPS_MESSAGE) from exc
    return QtCore, QtGui, QtWidgets, pg


class _DebugGuiWindow:
    def __init__(
        self,
        *,
        QtCore: Any,
        QtGui: Any,
        QtWidgets: Any,
        pg: Any,
        snapshot_store: LatestSnapshotStore,
        scene: DebugSceneView | None,
        mode: str,
        gui_fps: float,
        title: str,
        runtime_stats_getter: RuntimeStatsGetter | None,
        log_path: Path | None,
        render_callback: RenderCallback | None,
        cue_store: LatestCueStore | None,
        close_callback: CloseCallback | None,
        close_when: CloseWhen | None,
        visual_profile: str,
        status_panel: str,
        show_axes: str,
        show_grid: str,
    ) -> None:
        self.QtCore = QtCore
        self.QtGui = QtGui
        self.QtWidgets = QtWidgets
        self.pg = pg
        self.snapshot_store = snapshot_store
        self.scene = scene
        self.mode = mode
        self.gui_fps = float(gui_fps)
        self.runtime_stats_getter = runtime_stats_getter
        self.log_path = log_path
        self.render_callback = render_callback
        self.cue_store = cue_store
        self.close_callback = close_callback
        self.close_when = close_when
        self.visual_profile = visual_profile
        self.status_panel = status_panel
        self.show_axes = show_axes
        self.show_grid = show_grid
        self._last_refresh_time: float | None = None
        self._last_gui_fps: float | None = None
        self._last_log_time = 0.0
        self._log_header_written = False

        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(title)
        self.window.closeEvent = self._close_event
        central = QtWidgets.QWidget()
        layout = QtWidgets.QHBoxLayout(central)
        self.plot = pg.PlotWidget()
        self.plot.setAspectLocked(True)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.setLabel("bottom", "task x", units="m")
        self.plot.setLabel("left", "task y", units="m")
        self.status_label = QtWidgets.QLabel("Waiting for trial snapshot...")
        self.status_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.status_label.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        self.status_label.setMinimumWidth(360)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.plot, stretch=4)
        layout.addWidget(self.status_label, stretch=1)
        self.window.setCentralWidget(central)
        self.window.resize(1200, 760)
        self._close_shortcuts: list[Any] = []
        if self.mode == "replay":
            for key in ("Q", "Escape"):
                shortcut = QtGui.QShortcut(QtGui.QKeySequence(key), self.window)
                shortcut.activated.connect(self.window.close)
                self._close_shortcuts.append(shortcut)

        self.timer = QtCore.QTimer(self.window)
        self.timer.timeout.connect(self._refresh)
        interval_ms = max(1, int(1000.0 / self.gui_fps))
        self.timer.start(interval_ms)

    def show(self) -> None:
        self.window.show()

    def _close_event(self, event: Any) -> None:
        self.snapshot_store.mark_gui_closed()
        if self.close_callback is not None:
            try:
                self.close_callback()
            except Exception:
                pass
        event.accept()

    def _refresh(self) -> None:
        if self.close_when is not None:
            try:
                if self.close_when():
                    self.window.close()
                    return
            except Exception:
                pass
        started = time.monotonic()
        if self._last_refresh_time is not None:
            elapsed = max(1e-9, started - self._last_refresh_time)
            self._last_gui_fps = 1.0 / elapsed
        self._last_refresh_time = started
        snapshot = self.snapshot_store.get_latest()
        if snapshot is None:
            close_message = (
                "Close window / Q / Esc stops replay."
                if self.mode == "replay"
                else "Close window closes display only."
            )
            self.status_label.setText(
                "Waiting for trial snapshot...\n"
                "Calibration / waiting stages do not create fake DashboardSnapshot.\n"
                + close_message
            )
            return

        runtime_payload = self.runtime_stats_getter() if self.runtime_stats_getter is not None else {}
        runtime = DebugRuntimeStats(
            mode=str(runtime_payload.get("mode", self.mode)),
            snapshot_age_seconds=self.snapshot_store.snapshot_age_seconds(),
            gui_fps=self._last_gui_fps,
            render_lag_ms=None,
            total_received_frames=_optional_int(runtime_payload.get("total_received_frames")),
            parse_error_count=_optional_int(runtime_payload.get("parse_error_count")),
            raw_dropped_frame_count=_optional_int(runtime_payload.get("raw_dropped_frame_count")),
            overwritten_snapshot_count=_optional_int(
                runtime_payload.get(
                    "overwritten_snapshot_count",
                    runtime_payload.get("dropped_gui_snapshot_count"),
                )
            ),
            receive_fps=_optional_float(runtime_payload.get("receive_fps")),
            warnings=tuple(runtime_payload.get("warnings", ()) or ()),
        )
        view_model = snapshot_to_debug_view_model(
            snapshot,
            scene=self.scene,
            runtime=runtime,
            visual_profile=self.visual_profile,
            status_panel=self.status_panel,
            show_axes=self.show_axes,
            show_grid=self.show_grid,
        )
        cue_display = self.cue_store.get_active() if self.cue_store is not None else None
        self._draw(view_model, cue_display=cue_display)
        rendered = time.monotonic()
        if cue_display is not None and self.cue_store is not None:
            self.cue_store.mark_rendered(
                cue_display.cue_id,
                frame_index=view_model.frame_index,
                monotonic_ms=rendered * 1000.0,
            )
        self._safe_render_callback(snapshot, rendered)
        render_lag_ms = (rendered - started) * 1000.0
        self._log(view_model, render_lag_ms)

    def _safe_render_callback(self, snapshot: Any, rendered_monotonic: float) -> None:
        if self.render_callback is None:
            return
        try:
            self.render_callback(snapshot, rendered_monotonic)
        except Exception:
            return

    def _draw(self, view_model: Any, *, cue_display: Any | None = None) -> None:
        self.plot.clear()
        if view_model.scene is not None:
            if view_model.show_track:
                for box in view_model.scene.track_boxes:
                    self._plot_box(box, color=(100, 150, 220), width=2)
            if view_model.show_target and view_model.scene.target_region is not None:
                self._plot_box(view_model.scene.target_region, color=(80, 190, 120), width=2, style="dash")
            if (
                view_model.show_initial_block
                and view_model.scene.block_initial_center_task
                and view_model.scene.block_size
            ):
                self._plot_center_box(
                    view_model.scene.block_initial_center_task,
                    view_model.scene.block_size,
                    color=(180, 180, 180),
                    width=1,
                    style="dot",
                )
        if view_model.show_block and view_model.block_center_task and view_model.block_size:
            self._plot_center_box(view_model.block_center_task, view_model.block_size, color=(240, 150, 40), width=3)
        if view_model.show_pinch and view_model.pinch_center_task:
            self.plot.plot(
                [view_model.pinch_center_task[0]],
                [view_model.pinch_center_task[1]],
                pen=None,
                symbol="o",
                symbolSize=12,
                symbolBrush=self.pg.mkBrush(40, 220, 240),
                symbolPen=self.pg.mkPen(10, 90, 100),
            )
        if (
            view_model.show_block_pinch_line
            and view_model.pinch_center_task
            and view_model.block_center_task
        ):
            self.plot.plot(
                [view_model.block_center_task[0], view_model.pinch_center_task[0]],
                [view_model.block_center_task[1], view_model.pinch_center_task[1]],
                pen=self.pg.mkPen(240, 220, 120, width=1),
            )
        view_range = view_model.view_range
        self.plot.setXRange(view_range.x_min, view_range.x_max, padding=0.0)
        self.plot.setYRange(view_range.y_min, view_range.y_max, padding=0.0)
        self.plot.showAxis("bottom", view_model.axes_visible)
        self.plot.showAxis("left", view_model.axes_visible)
        self.plot.showGrid(
            x=view_model.grid_visible,
            y=view_model.grid_visible,
            alpha=0.25 if view_model.grid_visible else 0.0,
        )
        self.status_label.setVisible(view_model.status_panel_visible)
        self.status_label.setText("\n".join(view_model.status_lines))
        if cue_display is not None:
            cue_text = self.pg.TextItem(
                text=cue_display.message,
                color=(255, 245, 210),
                anchor=(0.5, 0.5),
            )
            cue_font = self.QtGui.QFont("Segoe UI", 22)
            cue_font.setBold(True)
            cue_text.setFont(cue_font)
            cue_text.setPos(
                (view_range.x_min + view_range.x_max) * 0.5,
                (view_range.y_min + view_range.y_max) * 0.5,
            )
            self.plot.addItem(cue_text)

    def _plot_box(self, box: Any, *, color: tuple[int, int, int], width: int, style: str = "solid") -> None:
        pen = self._pen(color=color, width=width, style=style)
        xs = [box.min_x, box.max_x, box.max_x, box.min_x, box.min_x]
        ys = [box.min_y, box.min_y, box.max_y, box.max_y, box.min_y]
        self.plot.plot(xs, ys, pen=pen)

    def _plot_center_box(
        self,
        center: list[float],
        size: list[float],
        *,
        color: tuple[int, int, int],
        width: int,
        style: str = "solid",
    ) -> None:
        half_x = float(size[0]) * 0.5
        half_y = float(size[1]) * 0.5
        box = type(
            "_Box",
            (),
            {
                "min_x": float(center[0]) - half_x,
                "max_x": float(center[0]) + half_x,
                "min_y": float(center[1]) - half_y,
                "max_y": float(center[1]) + half_y,
            },
        )
        self._plot_box(box, color=color, width=width, style=style)

    def _pen(self, *, color: tuple[int, int, int], width: int, style: str) -> Any:
        qt_style = self.QtCore.Qt.SolidLine
        if style == "dash":
            qt_style = self.QtCore.Qt.DashLine
        elif style == "dot":
            qt_style = self.QtCore.Qt.DotLine
        return self.pg.mkPen(*color, width=width, style=qt_style)

    def _log(self, view_model: Any, render_lag_ms: float) -> None:
        if self.log_path is None:
            return
        now = time.monotonic()
        if now - self._last_log_time < 0.5:
            return
        self._last_log_time = now
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=[
                    "wall_time",
                    "mode",
                    "frame_index",
                    "snapshot_age_seconds",
                    "gui_fps",
                    "render_lag_ms",
                    "main_state_label",
                ],
            )
            if not self._log_header_written and self.log_path.stat().st_size == 0:
                writer.writeheader()
                self._log_header_written = True
            writer.writerow(
                {
                    "wall_time": time.time(),
                    "mode": view_model.mode,
                    "frame_index": view_model.frame_index,
                    "snapshot_age_seconds": view_model.runtime.snapshot_age_seconds,
                    "gui_fps": view_model.runtime.gui_fps,
                    "render_lag_ms": render_lag_ms,
                    "main_state_label": view_model.main_state_label,
                }
            )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
