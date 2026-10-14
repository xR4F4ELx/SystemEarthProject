"""
Evaluate the compact pipeline against the raw layers (step 6): storage, fidelity, query, update, sensitivity.

Needs the demo flow to have run (raw baselines before/after the update; compact store built on held-back
data plus one event batch; compact verdicts before/after). Earth Engine timings come from ee_benchmark.py.

Output: data/results/evaluation.json (all numbers; docs/evaluation.md is written from it)
Run:
    python scripts/evaluate.py
"""

import json
import statistics
import tempfile
import time
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import rasterio
from shapely.geometry import box

import baseline as bl
import engine
from compact import BASE_PATH, EVENTS_DIR, STORE_DIR, load_manifest, pack, unpack, write_cog
from layers import (
    AOI_BOUNDS,
    PLOTS_DIR,
    RAW_DIR,
    geodesic_area_ha,
    load_forest_2020,
    load_hansen_lossyear,
    load_radd,
)

RESULTS = bl.RESULTS_DIR
OUT_PATH = RESULTS / "evaluation.json"
LAYER_FILES = ["base.tif", "protected_areas.parquet", "manifest.json"]
PLOT_FILES = ["plot_index.parquet", "plot_pa.parquet"]
DECISION_FIELDS = ["c2_deforestation_spatial_analysis_result", "d1_strict_protected_area_spatial_analysis_result",
                   "d2_restricted_use_area_spatial_analysis_result", "final_status"]
AREA_FIELDS = ["forest_2020_ha", "disturbed_after_cutoff_ha"]
SINGLE_PLOT_SAMPLE = 50
MIN_CELLS_OPTIONS = [1, 3, 5, 10]
EDGE_SHARE_OPTIONS = [0.005, 0.01, 0.02]


def uncompressed_bytes(path):
    with rasterio.open(path) as src:
        return src.width * src.height * sum(np.dtype(d).itemsize for d in src.dtypes)


def storage():
    raw = {}
    for path in sorted(RAW_DIR.glob("ci_tai_soubre_*")):
        entry = {"bytes": path.stat().st_size}
        if path.suffix == ".tif":
            entry["uncompressed_bytes"] = uncompressed_bytes(path)
        raw[path.name] = entry
    layers = {name: (STORE_DIR / name).stat().st_size for name in LAYER_FILES}
    layers.update({f"events/{p.name}": p.stat().st_size for p in sorted(EVENTS_DIR.glob("*.parquet"))})
    plot_side = {name: (STORE_DIR / name).stat().st_size for name in PLOT_FILES}

    raw_total = sum(e["bytes"] for e in raw.values())
    raw_uncompressed = sum(e.get("uncompressed_bytes", e["bytes"]) for e in raw.values())
    compact_total = sum(layers.values())
    aoi_km2 = geodesic_area_ha(box(*AOI_BOUNDS)) / 100
    return {
        "aoi_km2": aoi_km2, "raw_files": raw, "compact_layer_files": layers, "plot_side_files": plot_side,
        "raw_compressed_bytes": raw_total, "raw_uncompressed_bytes": raw_uncompressed,
        "compact_layer_bytes": compact_total,
        "ratio_vs_raw_compressed": raw_total / compact_total,
        "ratio_vs_raw_uncompressed": raw_uncompressed / compact_total,
        "compact_bytes_per_km2": compact_total / aoi_km2, "raw_bytes_per_km2": raw_total / aoi_km2,
        "plot_side_bytes_per_plot": sum(plot_side.values()) / 500,
    }


def compare(raw_file, compact_file, strata):
    raw = pd.read_csv(RESULTS / raw_file, keep_default_na=False).set_index("plot_id").sort_index()
    comp = pd.read_csv(RESULTS / compact_file, keep_default_na=False).set_index("plot_id").sort_index()
    text_fields = [c for c in raw.columns if c not in AREA_FIELDS]
    decision_match = (raw[DECISION_FIELDS] == comp[DECISION_FIELDS]).all(axis=1)
    per_stratum = decision_match.groupby(strata.reindex(raw.index)).mean().to_dict()
    return {
        "plots": len(raw),
        "decision_fidelity": float(decision_match.mean()),
        "decision_fidelity_by_stratum": {k: float(v) for k, v in per_stratum.items()},
        "field_fidelity": {c: float((raw[c].astype(str) == comp[c].astype(str)).mean()) for c in text_fields},
        "max_area_diff_ha": {c: float((raw[c] - comp[c]).abs().max()) for c in AREA_FIELDS},
        "mean_area_diff_ha": {c: float((raw[c] - comp[c]).abs().mean()) for c in AREA_FIELDS},
    }


def pixel_fidelity():
    """Decoded base raster + event log vs raw-derived values, over every cell of the AOI."""
    forest, grid = load_forest_2020()
    alert, radd_date = load_radd(grid)
    raw_date, raw_sources = bl.raw_disturbance(forest, load_hansen_lossyear(grid), alert, radd_date)
    with rasterio.open(BASE_PATH) as src:
        dec_forest, dec_date, dec_sources = unpack(src.read(1))
    dec_date, dec_sources = dec_date.ravel(), dec_sources.ravel()
    for batch in sorted(EVENTS_DIR.glob("*.parquet")):
        events = pq.read_table(batch).to_pandas()
        days = (pd.to_datetime(events["date"]) - pd.Timestamp("1970-01-01")).dt.days
        per_cell = pd.DataFrame({"cell": events["cell_id"].astype(np.int64), "day": days,
                                 "src": events["source"]}).groupby("cell").agg(
            day=("day", "min"), src=("src", lambda s: np.bitwise_or.reduce(s.to_numpy())))
        cells = per_cell.index.to_numpy()
        current = dec_date[cells]
        dec_date[cells] = np.where(current == 0, per_cell["day"], np.minimum(current, per_cell["day"]))
        dec_sources[cells] |= per_cell["src"].to_numpy(np.uint8)
    return {"cells": int(forest.size),
            "forest_bit": float((dec_forest == forest).mean()),
            "disturbance_date": float((dec_date == raw_date.ravel()).mean()),
            "sources": float((dec_sources == raw_sources.ravel()).mean())}


def fidelity():
    strata = pd.read_csv(PLOTS_DIR / "plot_strata.csv").set_index("plot_id")["stratum"]
    return {"before_update": compare("baseline_verdicts_before_update.csv", "compact_verdicts_before_update.csv",
                                     strata),
            "after_incremental_update": compare("baseline_verdicts.csv", "compact_verdicts.csv", strata),
            "pixel": pixel_fidelity()}


def query():
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    _, raw_timing = bl.run(plots)
    _, compact_all = engine.check()
    plot_ids = plots["ProductionPlace"].sample(SINGLE_PLOT_SAMPLE, random_state=0).tolist()
    singles = [engine.check([pid])[1] for pid in plot_ids]
    batch = load_manifest()["event_batches"][-1]["batch"]
    previous = pd.read_csv(RESULTS / "compact_verdicts_before_update.csv", keep_default_na=False)
    _, incremental = engine.update(batch, previous)
    return {
        "raw_local": {**raw_timing, "per_plot_ms": 1000 * raw_timing["evaluate_plots_s"] / len(plots),
                      "bytes_read_to_load": sum(p.stat().st_size for p in RAW_DIR.glob("ci_tai_soubre_*.tif"))},
        "compact_all_plots": compact_all,
        "compact_single_plot": {
            "sample": SINGLE_PLOT_SAMPLE,
            "median_total_ms": 1000 * statistics.median(s["total_s"] for s in singles),
            "median_bytes_read": statistics.median(s["bytes_read"] for s in singles),
            "median_tiles_read": statistics.median(s["tiles_read"] for s in singles),
        },
        "incremental_update": incremental,
    }


def rebuild_cost():
    """What a full rebuild costs instead of appending one event batch: re-encode the base, re-check all."""
    started = time.perf_counter()
    forest, grid = load_forest_2020()
    alert, radd_date = load_radd(grid)
    first_date, sources = bl.raw_disturbance(forest, load_hansen_lossyear(grid), alert, radd_date)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "base.tif"
        write_cog(pack(forest, first_date, sources), grid.transform, path)
        rebuilt_bytes = path.stat().st_size
    encode_s = time.perf_counter() - started
    _, full_check = engine.check()
    return {"encode_s": encode_s, "rewritten_bytes": rebuilt_bytes, "full_recheck": full_check}


def sensitivity():
    default, _ = engine.check()
    default = default.set_index("plot_id")["final_status"]
    runs = []
    for min_cells in MIN_CELLS_OPTIONS:
        for edge in EDGE_SHARE_OPTIONS:
            verdicts, _ = engine.check(settings={"min_cells": min_cells, "edge_share": edge})
            status = verdicts.set_index("plot_id")["final_status"]
            runs.append({"min_cells": min_cells, "edge_share": edge,
                         "status_counts": status.value_counts().to_dict(),
                         "plots_changed_vs_default": int((status != default).sum())})
    return runs


def main():
    results = {"manifest_id": load_manifest()["manifest_id"], "storage": storage(), "fidelity": fidelity(),
               "query": query(), "rebuild": rebuild_cost(), "sensitivity": sensitivity()}
    OUT_PATH.write_text(json.dumps(results, indent=2, default=float), encoding="utf-8")
    s, f, q = results["storage"], results["fidelity"], results["query"]
    print(f"Storage: compact {s['compact_layer_bytes'] / 1e3:.1f} kB vs raw {s['raw_compressed_bytes'] / 1e6:.2f} MB "
          f"({s['ratio_vs_raw_compressed']:.0f}x; {s['ratio_vs_raw_uncompressed']:.0f}x vs uncompressed)")
    print(f"Decision fidelity: {f['before_update']['decision_fidelity']:.1%} before, "
          f"{f['after_incremental_update']['decision_fidelity']:.1%} after update; pixel {f['pixel']}")
    print(f"Single plot (compact): {q['compact_single_plot']['median_total_ms']:.1f} ms, "
          f"{q['compact_single_plot']['median_bytes_read'] / 1e3:.1f} kB read")
    print(f"Wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
