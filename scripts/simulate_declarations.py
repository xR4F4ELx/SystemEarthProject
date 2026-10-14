"""
Simulate the supplier's due-diligence declaration for each synthetic plot (step 5, demo scaffolding).

In production the declaration arrives in the supplier's CSV; we have no real one for Côte d'Ivoire, so
we reproduce the method the sample supplier declared (data/sample/, column c1): any Hansen tree cover
loss 2021-2023 in the plot = "b. Indication of deforestation". No 2020 forest baseline, no alerts.
Only c2 is simulated: the sample says nothing about how suppliers check protected areas.

This reads raw Hansen on purpose: the compact store dropped loss outside 2020 forest, because our
decision never needs it, so it cannot reproduce the supplier's method.

Output: data/plots/supplier_declarations.csv in SystemEarth's CSV column names
Run:
    python scripts/simulate_declarations.py
"""

import argparse
from pathlib import Path

import geopandas as gpd
import pandas as pd

from baseline import C2_INDICATION, C2_NONE
from layers import PLOTS_DIR, load_forest_2020, load_hansen_lossyear, plot_cells, plot_footprint

SUPPLIER_LAYER = "Hansen Tree Cover Loss 2021-2023"  # as declared in the sample CSV, column c1
SUPPLIER_FIRST_YEAR, SUPPLIER_LAST_YEAR = 21, 23  # Hansen lossyear values for 2021-2023


def simulate(plots):
    _, grid = load_forest_2020()
    lossyear = load_hansen_lossyear(grid)
    supplier_loss = (lossyear >= SUPPLIER_FIRST_YEAR) & (lossyear <= SUPPLIER_LAST_YEAR)
    declared = []
    for geom, area in zip(plots.geometry, plots["Area"]):
        rows, cols = plot_cells(plot_footprint(geom, area), grid)
        declared.append(C2_INDICATION if supplier_loss[rows, cols].any() else C2_NONE)
    return pd.DataFrame({
        "a3_plot_id": plots["ProductionPlace"],
        "c1_indicate_deforestation_analysis_layer_used": SUPPLIER_LAYER,
        "c2_deforestation_spatial_analysis_result": declared,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plots", type=Path, default=PLOTS_DIR / "plots.geojson")
    parser.add_argument("--out", type=Path, default=PLOTS_DIR / "supplier_declarations.csv")
    args = parser.parse_args()

    declarations = simulate(gpd.read_file(args.plots))
    declarations.to_csv(args.out, index=False)
    print(declarations["c2_deforestation_spatial_analysis_result"].value_counts().to_string())
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
