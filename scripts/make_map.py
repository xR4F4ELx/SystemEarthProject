"""
Slide-ready map of the AOI (1920x1080 PNG): plot verdicts over the 2020 forest, plus a zoom on one plot
whose status the simulated update changed.

Left: 2020 forest (GFC2020), post-cut-off disturbance on it, national protected areas, all plots coloured by
final status after the update; plots whose status changed are ringed.
Right: one changed plot at full resolution: forest cells, disturbance already known before the update,
and disturbance the update added.

Needs the demo flow to have run (compact verdicts before and after the update).
Output: docs/figures/aoi_map.png
Run:
    python scripts/make_map.py
"""

import argparse
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.colors import ListedColormap, to_rgba  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402
from matplotlib.patches import Patch, Rectangle  # noqa: E402

from baseline import NEEDS_REVIEW, NON_COMPLIANT, COMPLIANT, RESULTS_DIR, raw_disturbance  # noqa: E402
from compact import PA_PATH  # noqa: E402
from layers import (  # noqa: E402
    M_PER_DEG_LON_AT_EQUATOR,
    PLOTS_DIR,
    ROOT,
    load_forest_2020,
    load_hansen_lossyear,
    load_radd,
    plot_footprint,
)

OUT_PATH = ROOT / "docs" / "figures" / "aoi_map.png"
POOL = 6  # overview cells per screen pixel along each axis
ZOOM_HALF_DEG = 0.012  # about 1.3 km each side of the zoomed plot
HELD_BACK = {"hansen_max_year": 24, "radd_until": 26181}  # the state before the simulated update

BACKGROUND = "#F7F5EF"
INK = "#17332B"
FOREST = "#9CC3A0"
DISTURBED_BEFORE = "#4B2A6B"
DISTURBED_NEW = "#E0A100"
STATUS_STYLE = {  # colour plus marker, so status never relies on colour alone
    COMPLIANT: ("#2F6FA3", "o", "Compliant"),
    NON_COMPLIANT: ("#C2652A", "s", "Non-compliant"),
    NEEDS_REVIEW: ("#6B6F76", "^", "Needs review"),
}


def pool(mask, factor, how):
    h, w = mask.shape[0] // factor * factor, mask.shape[1] // factor * factor
    blocks = mask[:h, :w].reshape(h // factor, factor, w // factor, factor)
    return (blocks.mean(axis=(1, 3)) if how == "mean" else blocks.max(axis=(1, 3))), (h, w)


def extent(grid, rows, cols, row0=0, col0=0):
    t = grid.transform
    return [t.c + col0 * t.a, t.c + (col0 + cols) * t.a, t.f + (row0 + rows) * t.e, t.f + row0 * t.e]


def load_inputs():
    forest, grid = load_forest_2020()
    lossyear = load_hansen_lossyear(grid)
    alert, radd_date = load_radd(grid)
    full_date, _ = raw_disturbance(forest, lossyear, alert, radd_date)
    before_date, _ = raw_disturbance(forest, lossyear, alert, radd_date, HELD_BACK["hansen_max_year"],
                                     HELD_BACK["radd_until"])
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    plots["footprint"] = [plot_footprint(g, a) for g, a in zip(plots.geometry, plots["Area"])]
    before = pd.read_csv(RESULTS_DIR / "compact_verdicts_before_update.csv", keep_default_na=False)
    after = pd.read_csv(RESULTS_DIR / "compact_verdicts.csv", keep_default_na=False)
    status = before.set_index("plot_id")[["final_status"]].join(
        after.set_index("plot_id")[["final_status"]], lsuffix="_before")
    plots = plots.join(status, on="ProductionPlace")
    plots["changed"] = plots["final_status_before"] != plots["final_status"]
    return forest, grid, full_date != 0, before_date != 0, plots, gpd.read_parquet(PA_PATH)


def draw_protected_areas(ax, protected):
    strict = protected[protected.pa_class == "strict"]
    restricted = protected[protected.pa_class == "restricted"]
    strict.boundary.plot(ax=ax, color=INK, linewidth=1.6)
    restricted.boundary.plot(ax=ax, color=INK, linewidth=1.4, linestyle=(0, (4, 3)))


def draw_overview(ax, forest, grid, disturbed_full, disturbed_before, plots, protected, zoom_box):
    forest_frac, (h, w) = pool(forest, POOL, "mean")
    known, _ = pool(disturbed_before, POOL, "max")
    added, _ = pool(disturbed_full & ~disturbed_before, POOL, "max")
    box = extent(grid, h, w)
    ax.imshow(forest_frac, extent=box, cmap=ListedColormap([BACKGROUND, FOREST]), vmin=0, vmax=1,
              interpolation="nearest")
    overlay = np.zeros(known.shape + (4,))
    overlay[known] = to_rgba(DISTURBED_BEFORE)
    overlay[added] = to_rgba(DISTURBED_NEW)
    ax.imshow(overlay, extent=box, interpolation="nearest")
    draw_protected_areas(ax, protected)

    centroids = plots["footprint"].map(lambda f: f.centroid)
    for status, (colour, marker, _) in STATUS_STYLE.items():
        pts = centroids[plots["final_status"] == status]
        ax.scatter(pts.map(lambda p: p.x), pts.map(lambda p: p.y), s=26, c=colour, marker=marker,
                   edgecolors="white", linewidths=0.5, zorder=3)
    changed = centroids[plots["changed"]]
    ax.scatter(changed.map(lambda p: p.x), changed.map(lambda p: p.y), s=200, facecolors="none",
               edgecolors=INK, linewidths=1.8, zorder=4)
    x0, y0, x1, y1 = zoom_box
    ax.add_patch(Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor=DISTURBED_NEW, linewidth=2.2, zorder=5))
    add_scale_bar(ax, box, km=10)
    ax.set_xlim(box[0], box[1])
    ax.set_ylim(box[2], box[3])
    ax.set_title("Côte d'Ivoire AOI, 2,756 km²: 500 plots after the update", loc="left", fontsize=15, color=INK)


def draw_zoom(ax, forest, grid, disturbed_full, disturbed_before, plots, protected, zoom_box):
    t = grid.transform
    x0, y0, x1, y1 = zoom_box
    col0, col1 = int((x0 - t.c) / t.a), int(np.ceil((x1 - t.c) / t.a))
    row0, row1 = int((y1 - t.f) / t.e), int(np.ceil((y0 - t.f) / t.e))
    window = (slice(row0, row1), slice(col0, col1))
    image = np.zeros((row1 - row0, col1 - col0, 4))
    image[:] = to_rgba(BACKGROUND)
    image[forest[window]] = to_rgba(FOREST)
    image[disturbed_before[window]] = to_rgba(DISTURBED_BEFORE)
    image[disturbed_full[window] & ~disturbed_before[window]] = to_rgba(DISTURBED_NEW)
    ax.imshow(image, extent=extent(grid, row1 - row0, col1 - col0, row0, col0), interpolation="nearest")
    draw_protected_areas(ax, protected)

    in_view = plots[plots["footprint"].map(lambda f: f.intersects(box_geometry(zoom_box)))]
    for status, (colour, _, _) in STATUS_STYLE.items():
        subset = in_view[in_view["final_status"] == status]
        if len(subset):
            gpd.GeoSeries(subset["footprint"].tolist()).boundary.plot(ax=ax, color=colour, linewidth=2.4)
    changed = in_view[in_view["changed"]]
    changed = changed.iloc[np.argsort([-f.centroid.y for f in changed["footprint"]])]
    label_x = x0 + 0.58 * (x1 - x0)
    for i, (_, plot) in enumerate(changed.iterrows()):  # labels stacked in a column on the right, with leaders
        c = plot["footprint"].centroid
        label_y = y1 - (0.16 + 0.13 * i) * (y1 - y0)
        ax.annotate(f"{plot['ProductionPlace']}\n{plot['final_status_before']} → {plot['final_status']}",
                    (c.x, c.y), xytext=(label_x, label_y), textcoords="data", fontsize=12, color=INK,
                    bbox=dict(boxstyle="round,pad=0.3", fc="white", ec=INK, lw=0.8),
                    arrowprops=dict(arrowstyle="-", color=INK, lw=1))
    add_scale_bar(ax, [x0, x1, y0, y1], km=0.5, corner="top_right")
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_title(f"Zoom: {len(changed)} plots the update changed (10 m cells)", loc="left", fontsize=15, color=INK)


def box_geometry(zoom_box):
    from shapely.geometry import box
    return box(*zoom_box)


def add_scale_bar(ax, box, km, corner="bottom_left"):
    west, east, south, north = box
    lat = (south + north) / 2
    length = km * 1000 / (M_PER_DEG_LON_AT_EQUATOR * np.cos(np.radians(lat)))
    if corner == "top_right":
        x, y = east - 0.04 * (east - west) - length, north - 0.08 * (north - south)
    else:
        x, y = west + 0.04 * (east - west), south + 0.05 * (north - south)
    ax.plot([x, x + length], [y, y], color=INK, linewidth=3, solid_capstyle="butt", zorder=6)
    label = f"{km:g} km" if km >= 1 else f"{km * 1000:g} m"
    ax.text(x + length / 2, y + 0.015 * (north - south), label, ha="center", va="bottom", fontsize=12, color=INK)


def pick_zoom(plots):
    """The changed plot outside the park with the most other plots nearby; falls back to any changed plot."""
    changed = plots[plots["changed"]]
    if changed.empty:
        raise ValueError("No plot changed status; run the demo flow (build, check, events, update) first")
    centres = plots["footprint"].map(lambda f: f.centroid)
    def neighbours(c):
        return sum(abs(o.x - c.x) < ZOOM_HALF_DEG and abs(o.y - c.y) < ZOOM_HALF_DEG for o in centres)
    best = max(changed.index, key=lambda i: neighbours(centres[i]) - 100 * (plots.at[i, "final_status_before"] != "Compliant"))
    c = centres[best]
    return (c.x - ZOOM_HALF_DEG, c.y - ZOOM_HALF_DEG, c.x + ZOOM_HALF_DEG, c.y + ZOOM_HALF_DEG)


def legend_handles():
    handles = [Line2D([], [], marker=m, linestyle="", markersize=10, markerfacecolor=c, markeredgecolor="white", label=l)
               for c, m, l in STATUS_STYLE.values()]
    handles += [
        Line2D([], [], marker="o", linestyle="", markersize=14, markerfacecolor="none", markeredgecolor=INK,
               markeredgewidth=1.8, label="Status changed by the update"),
        Patch(facecolor=FOREST, label="Forest on 31 Dec 2020 (GFC2020)"),
        Patch(facecolor=DISTURBED_BEFORE, label="Disturbed after cut-off (known before update)"),
        Patch(facecolor=DISTURBED_NEW, label="Disturbed: added by the update"),
        Line2D([], [], color=INK, linewidth=1.6, label="Taï National Park (d1, strict)"),
        Line2D([], [], color=INK, linewidth=1.4, linestyle=(0, (4, 3)), label="Classified forests (d2)"),
    ]
    return handles


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    args = parser.parse_args()

    forest, grid, disturbed_full, disturbed_before, plots, protected = load_inputs()
    zoom_box = pick_zoom(plots)
    fig = plt.figure(figsize=(16, 9), dpi=120, facecolor=BACKGROUND)
    grid_spec = fig.add_gridspec(2, 2, width_ratios=[1.12, 1], height_ratios=[1, 0.2], wspace=0.08, hspace=0.02,
                                 left=0.03, right=0.98, top=0.93, bottom=0.03)
    overview, zoom = fig.add_subplot(grid_spec[0, 0]), fig.add_subplot(grid_spec[0, 1])
    for ax in (overview, zoom):
        ax.set_facecolor(BACKGROUND)
        ax.set_xticks([])
        ax.set_yticks([])
        ax.set_aspect(1 / np.cos(np.radians(5.8)))
    draw_overview(overview, forest, grid, disturbed_full, disturbed_before, plots, protected, zoom_box)
    draw_zoom(zoom, forest, grid, disturbed_full, disturbed_before, plots, protected, zoom_box)
    for ax in (overview, zoom):
        ax.set_xlabel("")
        ax.set_ylabel("")
    legend_ax = fig.add_subplot(grid_spec[1, :])
    legend_ax.axis("off")
    legend_ax.legend(handles=legend_handles(), loc="center", ncol=5, frameon=False, fontsize=12)
    fig.text(0.98, 0.006, "Sources: JRC GFC2020 v4, Hansen GFC v1.13, RADD (WUR), WDPA; synthetic plots. "
             "Verdicts: compact store, identical to raw-layer pipeline.", ha="right", fontsize=10, color="#4A5568")

    args.out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.out, facecolor=BACKGROUND)
    print(f"Wrote {args.out}: {int(plots['changed'].sum())} plots changed status; zoom box {np.round(zoom_box, 4)}")


if __name__ == "__main__":
    main()
