"""Render a MapConfig JSON directly to an x-y preview image.

This utility is intentionally independent from replay sessions. It validates a
map JSON, renders configured track geometry, and optionally writes a small
summary JSON for quick inspection.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from map_config import MapBoxSpec, load_map_config, validate_map_config


def render_map_preview(
    *,
    map_config_path: Path,
    out_path: Path,
    show_target_region: bool = True,
    show_box_labels: bool = True,
    show_box_order: bool = True,
    show_configured_block: bool = True,
    padding: float = 0.1,
    title: str | None = None,
    annotate_centers: bool = False,
    summary_out: Path | None = None,
) -> dict[str, Any]:
    """Validate and render a MapConfig preview image."""

    config = load_map_config(map_config_path)
    validation = validate_map_config(config)
    summary = {
        "map_id": config.map_id,
        "track_box_count": len(config.track_boxes),
        "target_region_present": config.target_region is not None,
        "validation_errors": list(validation.errors),
        "validation_warnings": list(validation.warnings),
        "out": str(out_path),
    }
    if summary_out is not None:
        _write_json(summary_out, summary)
    if validation.errors:
        raise ValueError("map validation failed: " + "; ".join(validation.errors))

    plt = _load_pyplot()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.figure(figsize=(7, 7))
    extents: list[tuple[float, float, float, float]] = []

    boxes = sorted(config.track_boxes, key=_box_sort_key)
    for index, box in enumerate(boxes):
        _draw_box(
            plt,
            box,
            edgecolor="tab:gray",
            facecolor="tab:gray",
            alpha=0.12,
            label="track boxes" if index == 0 else None,
        )
        extents.append(_xy_extent(box))
        if show_box_labels or show_box_order:
            _annotate_box(plt, box, show_label=show_box_labels, show_order=show_box_order)
        if annotate_centers:
            center = _box_center(box)
            plt.scatter([center[0]], [center[1]], s=10, color="black")

    if show_target_region and config.target_region is not None:
        _draw_box(
            plt,
            config.target_region,
            edgecolor="tab:green",
            facecolor="none",
            alpha=1.0,
            linestyle="--",
            linewidth=1.8,
            label="target region",
        )
        extents.append(_xy_extent(config.target_region))

    if show_configured_block:
        center = config.block_initial_center_task
        if len(center) == 3:
            plt.scatter([center[0]], [center[1]], marker="*", s=90, label="configured block")
            extents.append((center[0], center[0], center[1], center[1]))
            if len(config.block_size) == 3:
                _draw_footprint(
                    plt,
                    center,
                    config.block_size,
                    edgecolor="tab:blue",
                    label="configured block footprint",
                )
                extents.append(_footprint_extent(center, config.block_size))

    _apply_limits(plt, extents, padding)
    plt.xlabel("task x")
    plt.ylabel("task y")
    plt.axis("equal")
    plt.title(title or f"Map preview: {config.map_id}")
    plt.legend(loc="best")
    plt.tight_layout()
    plt.savefig(out_path)
    plt.close()

    if summary_out is not None:
        _write_json(summary_out, summary)
    return summary


def _draw_box(
    plt: Any,
    box: MapBoxSpec,
    *,
    edgecolor: str,
    facecolor: str,
    alpha: float,
    label: str | None,
    linestyle: str = "-",
    linewidth: float = 1.0,
) -> None:
    xs = [box.min[0], box.max[0], box.max[0], box.min[0], box.min[0]]
    ys = [box.min[1], box.min[1], box.max[1], box.max[1], box.min[1]]
    plt.fill(xs, ys, facecolor=facecolor, edgecolor=edgecolor, alpha=alpha, label=label)
    plt.plot(xs, ys, color=edgecolor, linestyle=linestyle, linewidth=linewidth)


def _draw_footprint(
    plt: Any,
    center: list[float],
    size: list[float],
    *,
    edgecolor: str,
    label: str | None,
) -> None:
    x0, x1, y0, y1 = _footprint_extent(center, size)
    xs = [x0, x1, x1, x0, x0]
    ys = [y0, y0, y1, y1, y0]
    plt.fill(xs, ys, facecolor=edgecolor, edgecolor=edgecolor, alpha=0.2, label=label)
    plt.plot(xs, ys, color=edgecolor, linewidth=1.2)


def _annotate_box(
    plt: Any,
    box: MapBoxSpec,
    *,
    show_label: bool,
    show_order: bool,
) -> None:
    parts: list[str] = []
    if show_order and box.order is not None:
        parts.append(str(box.order))
    if show_label:
        parts.append(str(box.label or box.id))
    if not parts:
        return
    center = _box_center(box)
    plt.text(center[0], center[1], " ".join(parts), ha="center", va="center", fontsize=7)


def _box_sort_key(box: MapBoxSpec) -> tuple[int, str]:
    if box.order is None:
        return (10_000_000, box.id)
    return (box.order, box.id)


def _box_center(box: MapBoxSpec) -> tuple[float, float]:
    return ((box.min[0] + box.max[0]) * 0.5, (box.min[1] + box.max[1]) * 0.5)


def _xy_extent(box: MapBoxSpec) -> tuple[float, float, float, float]:
    return (box.min[0], box.max[0], box.min[1], box.max[1])


def _footprint_extent(center: list[float], size: list[float]) -> tuple[float, float, float, float]:
    return (
        center[0] - size[0] * 0.5,
        center[0] + size[0] * 0.5,
        center[1] - size[1] * 0.5,
        center[1] + size[1] * 0.5,
    )


def _apply_limits(plt: Any, extents: list[tuple[float, float, float, float]], padding: float) -> None:
    if not extents:
        return
    x_min = min(extent[0] for extent in extents)
    x_max = max(extent[1] for extent in extents)
    y_min = min(extent[2] for extent in extents)
    y_max = max(extent[3] for extent in extents)
    span = max(x_max - x_min, y_max - y_min, 1e-6)
    pad = max(0.0, padding) * span
    plt.xlim(x_min - pad, x_max + pad)
    plt.ylim(y_min - pad, y_max + pad)


def _load_pyplot() -> Any:
    import matplotlib.pyplot as plt

    return plt


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Preview a MapConfig JSON as a PNG.")
    parser.add_argument("--map-config", required=True)
    parser.add_argument("--out", default="map_preview.png")
    parser.add_argument("--show-target-region", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--show-box-labels", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--show-box-order", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--show-configured-block", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--padding", type=float, default=0.1)
    parser.add_argument("--title", default=None)
    parser.add_argument("--annotate-centers", action="store_true")
    parser.add_argument("--summary-out", default=None)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        summary = render_map_preview(
            map_config_path=Path(args.map_config),
            out_path=Path(args.out),
            show_target_region=args.show_target_region,
            show_box_labels=args.show_box_labels,
            show_box_order=args.show_box_order,
            show_configured_block=args.show_configured_block,
            padding=args.padding,
            title=args.title,
            annotate_centers=args.annotate_centers,
            summary_out=Path(args.summary_out) if args.summary_out is not None else None,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    for warning in summary.get("validation_warnings", []):
        print(f"warning: {warning}", file=sys.stderr)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
