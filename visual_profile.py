"""Display-only visual profile normalization for debug and experiment views."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


DEBUG_ALL = "debug_all"
EXPERIMENT_VISIBILITY_FEEDBACK = "experiment_visibility_feedback"
EXPERIMENT_BLANK = "experiment_blank"
DEPRECATED_MARKERS_ALIAS = "experiment_markers_when_hidden"

VISUAL_PROFILE_CHOICES = (
    DEBUG_ALL,
    EXPERIMENT_VISIBILITY_FEEDBACK,
    EXPERIMENT_BLANK,
    DEPRECATED_MARKERS_ALIAS,
)
DISPLAY_CONTROL_CHOICES = ("auto", "show", "hide")


@dataclass(frozen=True)
class VisualProfileSettings:
    """Normalized visual profile and effective display controls."""

    requested_visual_profile: str
    visual_profile: str
    effective_visual_profile: str
    status_panel: str
    effective_status_panel_visible: bool
    show_axes: str
    effective_axes_visible: bool
    show_grid: str
    effective_grid_visible: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_visual_profile(
    requested_visual_profile: str = DEBUG_ALL,
    *,
    status_panel: str = "auto",
    show_axes: str = "auto",
    show_grid: str = "auto",
) -> VisualProfileSettings:
    """Validate controls and normalize deprecated profile aliases."""

    requested = str(requested_visual_profile)
    if requested not in VISUAL_PROFILE_CHOICES:
        raise ValueError(
            "visual_profile must be one of: " + ", ".join(VISUAL_PROFILE_CHOICES)
        )
    for name, value in (
        ("status_panel", status_panel),
        ("show_axes", show_axes),
        ("show_grid", show_grid),
    ):
        if value not in DISPLAY_CONTROL_CHOICES:
            raise ValueError(f"{name} must be auto, show, or hide.")

    effective = (
        EXPERIMENT_VISIBILITY_FEEDBACK
        if requested == DEPRECATED_MARKERS_ALIAS
        else requested
    )
    debug_default = effective == DEBUG_ALL
    return VisualProfileSettings(
        requested_visual_profile=requested,
        visual_profile=effective,
        effective_visual_profile=effective,
        status_panel=status_panel,
        effective_status_panel_visible=_resolve_control(status_panel, debug_default),
        show_axes=show_axes,
        effective_axes_visible=_resolve_control(show_axes, debug_default),
        show_grid=show_grid,
        effective_grid_visible=_resolve_control(show_grid, debug_default),
    )


def _resolve_control(value: str, auto_default: bool) -> bool:
    if value == "show":
        return True
    if value == "hide":
        return False
    return bool(auto_default)
