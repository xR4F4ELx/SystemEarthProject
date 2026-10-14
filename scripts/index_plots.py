"""
Index customer plots against the compact store's grid and protected areas (step 4).

Runs when plots are uploaded or the protected-area layer changes, not on every check. After this,
checks need no geometry at all: a plot is a list of cell IDs plus precomputed protected-area shares.

Input:  data/plots/plots.geojson (customer schema), data/compact/protected_areas.parquet, manifest grid
Output: data/compact/plot_index.parquet  plot_id, row, col_start, col_end: run-length encoded cells
                                         whose centre lies in the plot (one row per run of a grid row)
        data/compact/plot_pa.parquet     plot_id, pa_class, pa_name, class_share
Run:
    python scripts/index_plots.py
"""

import argparse
from pathlib import Path

import geopandas as gpd
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from affine import Affine

from baseline import overlap, protected_overlay
from compact import PA_PATH, PLOT_INDEX_PATH, PLOT_PA_PATH, file_record, load_manifest, save_manifest
from layers import PLOTS_DIR, Grid, geodesic_area_ha, plot_cells, plot_footprint

PARQUET_COMPRESSION = "zstd"


def manifest_grid(manifest):
    grid = manifest["grid"]
    return Grid(Affine(*grid["transform"]), grid["height"], grid["width"])


def plot_pa_rows(plot_id, footprint, overlay):
    footprint_ha = geodesic_area_ha(footprint)
    rows = []
    for pa_class, layer in overlay.items():
        share, names = overlap(footprint, footprint_ha, layer)
        rows.extend({"plot_id": plot_id, "pa_class": pa_class, "pa_name": name, "class_share": share}
                    for name in names)
    return rows


def cell_runs(rows, cols):
    """Run-length encode a plot's cells as (row, col_start, col_end) runs of consecutive columns."""
    order = np.lexsort((cols, rows))
    rows, cols = rows[order], cols[order]
    breaks = np.flatnonzero((np.diff(rows) != 0) | (np.diff(cols) != 1)) + 1
    starts = np.r_[0, breaks]
    ends = np.r_[breaks, len(rows)] - 1
    return rows[starts], cols[starts], cols[ends]


def build_index(plots, grid, protected):
    """Cell runs and protected-area overlaps for every plot."""
    overlay = protected_overlay(protected)
    columns = {"plot_id": [], "row": [], "col_start": [], "col_end": []}
    pa_rows = []
    for plot_id, geom, area in zip(plots["ProductionPlace"], plots.geometry, plots["Area"]):
        footprint = plot_footprint(geom, area)
        rows, cols = plot_cells(footprint, grid)
        if len(rows) == 0:
            raise ValueError(f"Plot {plot_id} covers no cell centre; it is smaller than one grid cell")
        run_rows, col_start, col_end = cell_runs(rows, cols)
        for name, values in zip(("row", "col_start", "col_end"), (run_rows, col_start, col_end)):
            columns[name].append(values)
        columns["plot_id"].append(np.full(len(run_rows), plot_id, dtype=object))
        pa_rows.extend(plot_pa_rows(plot_id, footprint, overlay))

    index = pa.table({"plot_id": pa.array(np.concatenate(columns["plot_id"]), pa.string()),
                      **{name: pa.array(np.concatenate(columns[name]), pa.uint32())
                         for name in ("row", "col_start", "col_end")}})
    schema = pa.schema([("plot_id", pa.string()), ("pa_class", pa.string()), ("pa_name", pa.string()),
                        ("class_share", pa.float64())])
    plot_pa = pa.Table.from_pylist(pa_rows, schema=schema)
    return index, plot_pa


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plots", type=Path, default=PLOTS_DIR / "plots.geojson")
    args = parser.parse_args()

    manifest = load_manifest()
    index, plot_pa = build_index(gpd.read_file(args.plots), manifest_grid(manifest), gpd.read_parquet(PA_PATH))
    pq.write_table(index, PLOT_INDEX_PATH, compression=PARQUET_COMPRESSION)
    pq.write_table(plot_pa, PLOT_PA_PATH, compression=PARQUET_COMPRESSION)

    indexed = {PLOT_INDEX_PATH.name, PLOT_PA_PATH.name}
    manifest["files"] = [f for f in manifest["files"] if Path(f["path"]).name not in indexed]
    manifest["files"] += [file_record(PLOT_INDEX_PATH), file_record(PLOT_PA_PATH)]
    save_manifest(manifest)
    n_cells = sum(e - s + 1 for s, e in zip(index["col_start"].to_pylist(), index["col_end"].to_pylist()))
    print(f"Indexed {len(set(index['plot_id'].to_pylist()))} plots: {n_cells} cells in {index.num_rows} runs, "
          f"{plot_pa.num_rows} protected-area overlaps")


if __name__ == "__main__":
    main()
