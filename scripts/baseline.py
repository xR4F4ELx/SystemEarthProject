"""
Raw baseline pipeline (step 3): docs/decision-spec.md applied directly to the raw layers.

This is the ground truth the compact pipeline's fidelity is measured against, and the "original
layers" side of the query comparison: every plot is overlaid on the full rasters and polygons.

Input:  data/plots/plots.geojson (customer schema), data/raw/* layers, manifest.json
Output: data/results/baseline_verdicts.csv, one row per plot in the spec's output schema
        (list fields joined with ";")
Run:
    python scripts/baseline.py                         # all data in the export
    python scripts/baseline.py --radd-until 2026-06-30 --hansen-max-year 24 \\
        --out data/results/baseline_verdicts_before_update.csv   # state before the simulated update
"""

import argparse
import datetime as dt
import json
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.ops import unary_union
from shapely.prepared import prep

from layers import (
    HANSEN_FIRST_POST_CUTOFF_YEAR,
    PLOTS_DIR,
    RADD_FIRST_POST_CUTOFF_DATE,
    RADD_HIGH_CONFIDENCE,
    ROOT,
    cell_area_ha,
    geodesic_area_ha,
    load_forest_2020,
    load_hansen_lossyear,
    load_protected_areas,
    load_radd,
    plot_cells,
    plot_footprint,
)

RESULTS_DIR = ROOT / "data" / "results"
MANIFEST_PATH = ROOT / "manifest.json"

# Output values exactly as in SystemEarth's due-diligence CSV (data/sample/), incl. its capital "I".
C2_NONE = "a. No Indication of deforestation"
C2_INDICATION = "b. Indication of deforestation"
D1_NONE = "a. No overlap with strict PA"
D1_OVERLAP = "b. Overlap with strict PA"
D2_NONE = "a. No overlap with RA"
D2_OVERLAP = "b. Overlap with RA"
COMPLIANT, NON_COMPLIANT, NEEDS_REVIEW = "Compliant", "Non-compliant", "Needs review"
REASON_SMALL_OR_WEAK = "small_or_weak_signal"
REASON_EDGE_OVERLAP = "edge_overlap"

DEFAULT_MIN_DISTURBED_CELLS = 3  # spec: D >= 3 cells (about 0.026 ha) with a strong signal
DEFAULT_EDGE_OVERLAP_SHARE = 0.01  # spec: overlap under 1% of the plot is likely GPS/boundary error
HANSEN_LAST_YEAR = 25  # lossyear 25 = 2025, the latest in v1.13
RADD_NO_LIMIT = 99366

SOURCE_HANSEN, SOURCE_RADD_HIGH, SOURCE_RADD_LOW = 1, 2, 4  # bit flags per cell
SOURCE_NAMES = {SOURCE_HANSEN: "hansen", SOURCE_RADD_HIGH: "radd_high", SOURCE_RADD_LOW: "radd_low"}
NO_DATE = 0  # days since 1970-01-01; no valid disturbance can fall on that day


def to_epoch_days(years, day_of_year):
    year_start = (np.asarray(years) - 1970).astype("datetime64[Y]").astype("datetime64[D]")
    return (year_start + (np.asarray(day_of_year) - 1)).astype(np.int64)


def date_to_yyddd(date):
    return (date.year - 2000) * 1000 + date.timetuple().tm_yday


def raw_disturbance(forest, lossyear, alert, radd_date, hansen_max_year=HANSEN_LAST_YEAR, radd_until=RADD_NO_LIMIT):
    """Per cell: first post-cut-off disturbance date (epoch days, 0 = none) and source flags.

    Only cells that were forest in 2020 can be disturbed (spec: D is a subset of F).
    """
    hansen = forest & (lossyear >= HANSEN_FIRST_POST_CUTOFF_YEAR) & (lossyear <= hansen_max_year)
    radd = forest & (radd_date >= RADD_FIRST_POST_CUTOFF_DATE) & (radd_date <= radd_until)

    days = np.full(forest.shape, np.iinfo(np.int64).max, dtype=np.int64)
    hansen_idx = np.nonzero(hansen)
    days[hansen_idx] = to_epoch_days(2000 + lossyear[hansen_idx].astype(np.int64), 1)  # spec: 1 Jan
    radd_idx = np.nonzero(radd)
    radd_days = to_epoch_days(2000 + radd_date[radd_idx] // 1000, radd_date[radd_idx] % 1000)
    days[radd_idx] = np.minimum(days[radd_idx], radd_days)
    first_date = np.where(hansen | radd, days, NO_DATE).astype(np.int32)

    sources = np.zeros(forest.shape, dtype=np.uint8)
    sources[hansen] |= SOURCE_HANSEN
    sources[radd & (alert == RADD_HIGH_CONFIDENCE)] |= SOURCE_RADD_HIGH
    sources[radd & (alert != RADD_HIGH_CONFIDENCE)] |= SOURCE_RADD_LOW
    return first_date, sources


def c2_result(n_disturbed, has_strong_signal, min_cells=DEFAULT_MIN_DISTURBED_CELLS):
    """Spec c2: (label, review reason or None)."""
    if n_disturbed == 0:
        return C2_NONE, None
    if n_disturbed >= min_cells and has_strong_signal:
        return C2_INDICATION, None
    return C2_INDICATION, REASON_SMALL_OR_WEAK


def pa_result(overlap_share, none_label, overlap_label, edge_share=DEFAULT_EDGE_OVERLAP_SHARE):
    """Spec d1/d2: (label, review reason or None) from the share of the plot inside the areas."""
    if overlap_share <= 0:
        return none_label, None
    return overlap_label, (REASON_EDGE_OVERLAP if overlap_share < edge_share else None)


def final_status(c2, d1, d2):
    """Spec final status from (label, reason) pairs for c2, d1 and d2."""
    if (c2[0] == C2_INDICATION and c2[1] is None) or (d1[0] == D1_OVERLAP and d1[1] is None):
        return NON_COMPLIANT
    if any(reason for _, reason in (c2, d1, d2)) or d2[0] == D2_OVERLAP:
        return NEEDS_REVIEW
    return COMPLIANT


def protected_overlay(protected):
    """Union, prepared geometry and named parts for the strict (d1) and restricted (d2) classes."""
    overlay = {}
    for pa_class in ("strict", "restricted"):
        areas = protected[protected.pa_class == pa_class]
        union = unary_union(areas.geometry.tolist())
        overlay[pa_class] = {"union": union, "prepared": prep(union),
                             "named": list(zip(areas["NAME"], areas.geometry.map(prep)))}
    return overlay


def overlap(footprint, footprint_ha, layer):
    """Share of the plot inside the layer, and the names of the areas it touches."""
    if not layer["prepared"].intersects(footprint):
        return 0.0, []
    share = geodesic_area_ha(footprint.intersection(layer["union"])) / footprint_ha
    names = [name for name, area in layer["named"] if area.intersects(footprint)]
    return share, names


def evaluate_plot(plot_id, footprint, grid, forest, first_date, sources, overlay, settings):
    rows, cols = plot_cells(footprint, grid)
    areas = cell_area_ha(rows, grid)
    in_forest = forest[rows, cols]
    dates = first_date[rows, cols]
    disturbed = in_forest & (dates != NO_DATE)
    plot_sources = int(np.bitwise_or.reduce(sources[rows, cols][disturbed])) if disturbed.any() else 0
    strong = bool(plot_sources & (SOURCE_HANSEN | SOURCE_RADD_HIGH))

    c2 = c2_result(int(disturbed.sum()), strong, settings["min_disturbed_cells"])
    footprint_ha = geodesic_area_ha(footprint)
    d1_share, d1_names = overlap(footprint, footprint_ha, overlay["strict"])
    d2_share, d2_names = overlap(footprint, footprint_ha, overlay["restricted"])
    d1 = pa_result(d1_share, D1_NONE, D1_OVERLAP, settings["edge_overlap_share"])
    d2 = pa_result(d2_share, D2_NONE, D2_OVERLAP, settings["edge_overlap_share"])
    reasons = sorted({reason for _, reason in (c2, d1, d2) if reason})

    first = int(dates[disturbed].min()) if disturbed.any() else NO_DATE
    return {
        "plot_id": plot_id,
        "c2_deforestation_spatial_analysis_result": c2[0],
        "d1_strict_protected_area_spatial_analysis_result": d1[0],
        "d2_restricted_use_area_spatial_analysis_result": d2[0],
        "final_status": final_status(c2, d1, d2),
        "review_reasons": ";".join(reasons),
        "forest_2020_ha": round(float(areas[in_forest].sum()), 4),
        "disturbed_after_cutoff_ha": round(float(areas[disturbed].sum()), 4),
        "first_disturbance_date": (dt.date(1970, 1, 1) + dt.timedelta(days=first)).isoformat() if first else "",
        "disturbance_sources": ";".join(name for flag, name in SOURCE_NAMES.items() if plot_sources & flag),
        "pa_overlap_names": ";".join(sorted(set(d1_names + d2_names))),
        "n_cells": len(rows),
        "n_forest_cells": int(in_forest.sum()),
        "n_disturbed_cells": int(disturbed.sum()),
    }


def load_manifest_id(radd_until, hansen_max_year):
    """Manifest ID, extended with the data limits so a held-back state is never mistaken for full data."""
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"{MANIFEST_PATH} not found; it is written by scripts/export_layers.py")
    manifest_id = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))["manifest_id"]
    limits = []
    if radd_until != RADD_NO_LIMIT:
        limits.append(f"radd<={radd_until}")
    if hansen_max_year != HANSEN_LAST_YEAR:
        limits.append(f"hansen<={2000 + hansen_max_year}")
    return "|".join([manifest_id, *limits])


def run(plots, hansen_max_year=HANSEN_LAST_YEAR, radd_until=RADD_NO_LIMIT, settings=None):
    """Verdict table for the plots, plus timings (seconds) for loading layers and evaluating plots."""
    settings = {"min_disturbed_cells": DEFAULT_MIN_DISTURBED_CELLS,
                "edge_overlap_share": DEFAULT_EDGE_OVERLAP_SHARE, **(settings or {})}
    started = time.perf_counter()
    forest, grid = load_forest_2020()
    alert, radd_date = load_radd(grid)
    first_date, sources = raw_disturbance(forest, load_hansen_lossyear(grid), alert, radd_date,
                                          hansen_max_year, radd_until)
    overlay = protected_overlay(load_protected_areas())
    loaded = time.perf_counter()

    rows = [
        evaluate_plot(plot_id, plot_footprint(geom, area), grid, forest, first_date, sources, overlay, settings)
        for plot_id, geom, area in zip(plots["ProductionPlace"], plots.geometry, plots["Area"])
    ]
    verdicts = pd.DataFrame(rows).assign(manifest_id=load_manifest_id(radd_until, hansen_max_year))
    return verdicts, {"load_layers_s": loaded - started, "evaluate_plots_s": time.perf_counter() - loaded}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plots", type=Path, default=PLOTS_DIR / "plots.geojson")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "baseline_verdicts.csv")
    parser.add_argument("--radd-until", type=dt.date.fromisoformat, help="ignore RADD alerts after this date")
    parser.add_argument("--hansen-max-year", type=int, default=HANSEN_LAST_YEAR,
                        help="last Hansen lossyear to use (21-25)")
    parser.add_argument("--min-disturbed-cells", type=int, default=DEFAULT_MIN_DISTURBED_CELLS)
    parser.add_argument("--edge-overlap-share", type=float, default=DEFAULT_EDGE_OVERLAP_SHARE)
    args = parser.parse_args()
    if not HANSEN_FIRST_POST_CUTOFF_YEAR <= args.hansen_max_year <= HANSEN_LAST_YEAR:
        parser.error(f"--hansen-max-year must be {HANSEN_FIRST_POST_CUTOFF_YEAR}-{HANSEN_LAST_YEAR}")

    radd_until = date_to_yyddd(args.radd_until) if args.radd_until else RADD_NO_LIMIT
    settings = {"min_disturbed_cells": args.min_disturbed_cells, "edge_overlap_share": args.edge_overlap_share}
    verdicts, timings = run(gpd.read_file(args.plots), args.hansen_max_year, radd_until, settings)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    verdicts.to_csv(args.out, index=False)
    print(verdicts["final_status"].value_counts().to_string())
    print(f"Layers loaded in {timings['load_layers_s']:.2f} s; {len(verdicts)} plots evaluated in "
          f"{timings['evaluate_plots_s']:.2f} s ({1000 * timings['evaluate_plots_s'] / len(verdicts):.1f} ms/plot).")
    print(f"Wrote {args.out} (manifest {verdicts['manifest_id'].iloc[0]}).")


if __name__ == "__main__":
    main()
