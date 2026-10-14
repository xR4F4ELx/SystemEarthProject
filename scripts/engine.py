"""
DuckDB rule engine over the compact store (step 4): docs/decision-spec.md as SQL.

A check reads only the base-raster tiles under the plots' cells, applies the event log on top,
and evaluates the rules in SQL. No geometry is touched: plots are cell lists from the plot index.

Modes:
  check   evaluate all indexed plots (or --plots a,b,c) against base + all recorded event batches
  update  re-check only plots whose cells appear in one event batch, and merge into earlier verdicts
Run:
    python scripts/engine.py check --out data/results/compact_verdicts_before_update.csv
    python scripts/engine.py update --batch 2026-10-07 \\
        --previous data/results/compact_verdicts_before_update.csv --out data/results/compact_verdicts.csv
"""

import argparse
import time
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa

from baseline import (
    C2_INDICATION,
    C2_NONE,
    COMPLIANT,
    D1_NONE,
    D1_OVERLAP,
    D2_NONE,
    D2_OVERLAP,
    DEFAULT_EDGE_OVERLAP_SHARE,
    DEFAULT_MIN_DISTURBED_CELLS,
    NEEDS_REVIEW,
    NON_COMPLIANT,
    REASON_EDGE_OVERLAP,
    REASON_SMALL_OR_WEAK,
    RESULTS_DIR,
    SOURCE_HANSEN,
    SOURCE_RADD_HIGH,
    SOURCE_RADD_LOW,
)
from compact import (
    BASE_PATH,
    DAY_BITS,
    DAY_ZERO,
    FOREST_BIT,
    PLOT_INDEX_PATH,
    PLOT_PA_PATH,
    SOURCE_BITS,
    SOURCE_SHIFT,
    STORE_DIR,
    load_manifest,
    read_cells,
)
from index_plots import manifest_grid
from layers import M2_PER_HA, M_PER_DEG_LAT, M_PER_DEG_LON_AT_EQUATOR

CELL_STATE_SQL = f"""
CREATE OR REPLACE TEMP VIEW cell_state AS
WITH ev AS (
    SELECT cell_id, min(date) AS ev_date, bit_or(source) AS ev_sources
    FROM events WHERE cell_id IN (SELECT cell_id FROM base_cells) GROUP BY cell_id
), decoded AS (
    SELECT b.cell_id,
           (b.base >> {FOREST_BIT}) & 1 = 1 AS forest,
           CASE WHEN b.base & {DAY_BITS} > 0
                THEN DATE '{DAY_ZERO.isoformat()}' + CAST(b.base & {DAY_BITS} AS INTEGER) END AS base_date,
           (b.base >> {SOURCE_SHIFT}) & {SOURCE_BITS} AS base_sources,
           ev.ev_date, ev.ev_sources
    FROM base_cells b LEFT JOIN ev USING (cell_id)
)
SELECT cell_id, forest,
       least(base_date, ev_date) AS first_date,  -- least() ignores NULLs in DuckDB
       base_sources | coalesce(ev_sources, 0) AS sources
FROM decoded
"""


def verdict_sql(grid):
    lat = f"({grid.transform.f} + ((i.cell_id // {grid.width}) + 0.5) * {grid.transform.e})"
    cell_area = (f"({grid.transform.a} * {M_PER_DEG_LON_AT_EQUATOR} * cos(radians({lat}))"
                 f" * {abs(grid.transform.e)} * {M_PER_DEG_LAT} / {M2_PER_HA})")
    strong = SOURCE_HANSEN | SOURCE_RADD_HIGH
    return f"""
WITH per_cell AS (
    SELECT i.plot_id, s.forest, s.first_date, s.sources,
           s.forest AND s.first_date IS NOT NULL AS disturbed, {cell_area} AS area_ha
    FROM plot_index i JOIN targets USING (plot_id) JOIN cell_state s USING (cell_id)
), plot AS (
    SELECT plot_id, count(*) AS n_cells,
           count(*) FILTER (WHERE forest) AS n_forest_cells,
           count(*) FILTER (WHERE disturbed) AS n_disturbed_cells,
           coalesce(sum(area_ha) FILTER (WHERE forest), 0) AS forest_ha,
           coalesce(sum(area_ha) FILTER (WHERE disturbed), 0) AS disturbed_ha,
           min(first_date) FILTER (WHERE disturbed) AS first_date,
           coalesce(bit_or(sources) FILTER (WHERE disturbed), 0) AS sources
    FROM per_cell GROUP BY plot_id
), pa AS (
    SELECT plot_id,
           coalesce(max(class_share) FILTER (WHERE pa_class = 'strict'), 0) AS d1_share,
           coalesce(max(class_share) FILTER (WHERE pa_class = 'restricted'), 0) AS d2_share,
           string_agg(DISTINCT pa_name, ';' ORDER BY pa_name) AS pa_names
    FROM plot_pa JOIN targets USING (plot_id) GROUP BY plot_id
), flags AS (
    SELECT p.*, coalesce(pa.d1_share, 0) AS d1_share, coalesce(pa.d2_share, 0) AS d2_share,
           coalesce(pa.pa_names, '') AS pa_names,
           n_disturbed_cells > 0 AS c2_indication,
           n_disturbed_cells > 0
               AND NOT (n_disturbed_cells >= $min_cells AND sources & {strong} > 0) AS c2_review
    FROM plot p LEFT JOIN pa USING (plot_id)
), rules AS (
    SELECT *, d1_share > 0 AS d1_overlap, d1_share > 0 AND d1_share < $edge_share AS d1_review,
              d2_share > 0 AS d2_overlap, d2_share > 0 AND d2_share < $edge_share AS d2_review
    FROM flags
)
SELECT plot_id,
       CASE WHEN c2_indication THEN '{C2_INDICATION}' ELSE '{C2_NONE}' END
           AS c2_deforestation_spatial_analysis_result,
       CASE WHEN d1_overlap THEN '{D1_OVERLAP}' ELSE '{D1_NONE}' END
           AS d1_strict_protected_area_spatial_analysis_result,
       CASE WHEN d2_overlap THEN '{D2_OVERLAP}' ELSE '{D2_NONE}' END
           AS d2_restricted_use_area_spatial_analysis_result,
       CASE WHEN (c2_indication AND NOT c2_review) OR (d1_overlap AND NOT d1_review) THEN '{NON_COMPLIANT}'
            WHEN c2_review OR d1_review OR d2_review OR d2_overlap THEN '{NEEDS_REVIEW}'
            ELSE '{COMPLIANT}' END AS final_status,
       concat_ws(';', CASE WHEN d1_review OR d2_review THEN '{REASON_EDGE_OVERLAP}' END,
                      CASE WHEN c2_review THEN '{REASON_SMALL_OR_WEAK}' END) AS review_reasons,
       round(forest_ha, 4) AS forest_2020_ha,
       round(disturbed_ha, 4) AS disturbed_after_cutoff_ha,
       coalesce(strftime(first_date, '%Y-%m-%d'), '') AS first_disturbance_date,
       concat_ws(';', CASE WHEN sources & {SOURCE_HANSEN} > 0 THEN 'hansen' END,
                      CASE WHEN sources & {SOURCE_RADD_HIGH} > 0 THEN 'radd_high' END,
                      CASE WHEN sources & {SOURCE_RADD_LOW} > 0 THEN 'radd_low' END) AS disturbance_sources,
       pa_names AS pa_overlap_names, n_cells, n_forest_cells, n_disturbed_cells
FROM rules ORDER BY plot_id
"""


def connect(manifest):
    con = duckdb.connect()
    width = manifest["grid"]["width"]
    con.execute(  # expand the run-length encoded index back to one row per cell
        f"CREATE VIEW plot_index AS SELECT plot_id, "
        f"CAST(row AS BIGINT) * {width} + unnest(range(col_start, col_end + 1)) AS cell_id "
        f"FROM read_parquet('{PLOT_INDEX_PATH}')")
    con.execute(f"CREATE VIEW plot_pa AS SELECT * FROM read_parquet('{PLOT_PA_PATH}')")
    batches = [str(STORE_DIR / b["path"]) for b in manifest["event_batches"]]
    if batches:
        con.execute(f"CREATE VIEW events AS SELECT * FROM read_parquet({batches})")
    else:
        con.execute("CREATE TABLE events (cell_id UINTEGER, date DATE, source UTINYINT)")
    return con


def check(plot_ids=None, settings=None):
    """Verdicts for the given plots (all indexed plots if None), plus I/O and timing stats."""
    settings = {"min_cells": DEFAULT_MIN_DISTURBED_CELLS, "edge_share": DEFAULT_EDGE_OVERLAP_SHARE,
                **(settings or {})}
    started = time.perf_counter()
    manifest = load_manifest()
    grid = manifest_grid(manifest)
    con = connect(manifest)
    if plot_ids is None:
        plot_ids = [r[0] for r in con.execute("SELECT DISTINCT plot_id FROM plot_index").fetchall()]
    con.register("targets", pa.table({"plot_id": pa.array(sorted(plot_ids), pa.string())}))
    cell_ids = con.execute("SELECT DISTINCT cell_id FROM plot_index JOIN targets USING (plot_id)"
                           ).fetchnumpy()["cell_id"].astype(np.int64)
    indexed = time.perf_counter()

    base, bytes_read, tiles = read_cells(BASE_PATH, cell_ids // grid.width, cell_ids % grid.width)
    con.register("base_cells", pa.table({"cell_id": pa.array(cell_ids.astype(np.uint32)),
                                         "base": pa.array(base, pa.uint16())}))
    read = time.perf_counter()

    con.execute(CELL_STATE_SQL)
    verdicts = con.execute(verdict_sql(grid), settings).df().assign(manifest_id=manifest["manifest_id"])
    done = time.perf_counter()
    stats = {"plots": len(plot_ids), "cells": len(cell_ids), "tiles_read": tiles, "bytes_read": bytes_read,
             "index_s": indexed - started, "tile_read_s": read - indexed, "rules_s": done - read,
             "total_s": done - started}
    return verdicts, stats


def affected_plots(batch_path):
    """Plots with at least one cell in the event batch: the only verdicts the batch can change."""
    con = connect(load_manifest())
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT plot_id FROM plot_index "
        f"JOIN read_parquet('{batch_path}') USING (cell_id) ORDER BY plot_id").fetchall()]


def update(batch_id, previous):
    """Re-check the plots one batch touches and merge them into the previous verdicts."""
    manifest = load_manifest()
    batch = next((b for b in manifest["event_batches"] if b["batch"] == batch_id), None)
    if batch is None:
        raise ValueError(f"Batch {batch_id!r} is not in the manifest; publish it with encode_layers.py events")
    started = time.perf_counter()
    affected = affected_plots(STORE_DIR / batch["path"])
    found = time.perf_counter()
    rechecked, stats = check(affected) if affected else (previous.iloc[0:0], {"total_s": 0.0})
    unchanged = previous[~previous["plot_id"].isin(affected)].assign(manifest_id=manifest["manifest_id"])
    merged = pd.concat([unchanged, rechecked]).sort_values("plot_id").reset_index(drop=True)
    stats = {**stats, "affected_plots": len(affected), "total_plots": len(previous),
             "find_affected_s": found - started, "total_s": found - started + stats["total_s"]}
    return merged, stats


def print_stats(stats):
    print(", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in stats.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    check_mode = sub.add_parser("check")
    check_mode.add_argument("--plots", help="comma-separated plot IDs (default: all indexed plots)")
    check_mode.add_argument("--min-disturbed-cells", type=int, default=DEFAULT_MIN_DISTURBED_CELLS)
    check_mode.add_argument("--edge-overlap-share", type=float, default=DEFAULT_EDGE_OVERLAP_SHARE)
    update_mode = sub.add_parser("update")
    update_mode.add_argument("--batch", required=True)
    update_mode.add_argument("--previous", type=Path, required=True, help="verdicts CSV before the batch")
    for mode in (check_mode, update_mode):
        mode.add_argument("--out", type=Path, default=RESULTS_DIR / "compact_verdicts.csv")
    args = parser.parse_args()

    if args.mode == "check":
        plot_ids = args.plots.split(",") if args.plots else None
        settings = {"min_cells": args.min_disturbed_cells, "edge_share": args.edge_overlap_share}
        verdicts, stats = check(plot_ids, settings)
    else:
        previous = pd.read_csv(args.previous, keep_default_na=False)
        verdicts, stats = update(args.batch, previous)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    verdicts.to_csv(args.out, index=False)
    print(verdicts["final_status"].value_counts().to_string())
    print_stats(stats)
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
