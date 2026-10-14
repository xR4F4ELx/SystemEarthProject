"""
Time plot checks on Google Earth Engine, SystemEarth's current path (step 6), for the query comparison.

Per plot, Earth Engine counts forest-2020 cells and post-cut-off disturbed cells on the GFC2020 grid,
using an unweighted reducer (pixel-centre rule, like our pipelines). Two access patterns:
  single  one reduceRegion request per plot, sequential (an interactive re-check)
  batch   one reduceRegions request for all plots (a bulk re-check)
EECU use is captured with Earth Engine's profiler. Counts are compared with the raw baseline to show
both answer the same question; small differences can come from Earth Engine's own resampling of RADD.

Runs on the caller's Earth Engine project and uses its quota.
Output: data/results/ee_benchmark.json, data/results/ee_profile.txt
Run:
    python scripts/ee_benchmark.py --project YOUR_PROJECT [--single-sample 20]
"""

import argparse
import io
import json
import statistics
import time

import ee
import geopandas as gpd
import pandas as pd
from shapely.geometry import mapping

from baseline import RESULTS_DIR
from export_layers import DATASETS, get_radd_africa
from layers import (
    HANSEN_FIRST_POST_CUTOFF_YEAR,
    PLOTS_DIR,
    RADD_FIRST_POST_CUTOFF_DATE,
    plot_footprint,
)

OUT_PATH = RESULTS_DIR / "ee_benchmark.json"
PROFILE_PATH = RESULTS_DIR / "ee_profile.txt"
MAX_PIXELS = 1e9


def decision_image():
    """forest (GFC2020) and disturbed (forest with Hansen 2021+ loss or RADD alert after the cut-off)."""
    gfc = ee.Image(DATASETS["gfc2020"]).select("Map")
    forest = gfc.unmask(0).eq(1)
    lossyear = ee.Image(DATASETS["hansen"]).select("lossyear").unmask(0)
    radd, _ = get_radd_africa()
    if radd is None:
        raise RuntimeError("RADD image could not be loaded from Earth Engine")
    radd_post = radd.select("Date").unmask(0).gte(RADD_FIRST_POST_CUTOFF_DATE)
    disturbed = forest.And(lossyear.gte(HANSEN_FIRST_POST_CUTOFF_YEAR).Or(radd_post))
    return forest.rename("forest").addBands(disturbed.rename("disturbed")), gfc.projection()


def ee_geometry(footprint):
    return ee.Geometry(mapping(footprint), "EPSG:4326", False)  # planar edges, like our pipelines


def time_single(image, transform, footprints, plot_ids):
    timings, counts = [], {}
    reducer = ee.Reducer.sum().unweighted()
    for plot_id, footprint in zip(plot_ids, footprints):
        started = time.perf_counter()
        result = image.reduceRegion(reducer, ee_geometry(footprint), crs="EPSG:4326", crsTransform=transform,
                                    maxPixels=MAX_PIXELS).getInfo()
        timings.append(time.perf_counter() - started)
        counts[plot_id] = result
    return timings, counts


def time_batch(image, transform, footprints, plot_ids):
    features = ee.FeatureCollection([ee.Feature(ee_geometry(f), {"plot_id": pid})
                                     for pid, f in zip(plot_ids, footprints)])
    profile = io.StringIO()
    started = time.perf_counter()
    with ee.profilePrinting(destination=profile):
        result = image.reduceRegions(features, ee.Reducer.sum().unweighted(), crs="EPSG:4326",
                                     crsTransform=transform).getInfo()
    elapsed = time.perf_counter() - started
    counts = {f["properties"]["plot_id"]: f["properties"] for f in result["features"]}
    return elapsed, counts, profile.getvalue()


def agreement(counts, baseline):
    ee_counts = pd.DataFrame.from_dict(counts, orient="index")
    joined = baseline.join(ee_counts, how="inner")
    return {"plots": len(joined),
            "forest_cells_exact": float((joined["forest"].round() == joined["n_forest_cells"]).mean()),
            "disturbed_cells_exact": float((joined["disturbed"].round() == joined["n_disturbed_cells"]).mean()),
            "c2_same": float(((joined["disturbed"] > 0) == (joined["n_disturbed_cells"] > 0)).mean())}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--project", required=True, help="Google Cloud project registered for Earth Engine")
    parser.add_argument("--single-sample", type=int, default=20, help="plots to time one request at a time")
    args = parser.parse_args()

    ee.Initialize(project=args.project)
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    footprints = [plot_footprint(g, a) for g, a in zip(plots.geometry, plots["Area"])]
    plot_ids = plots["ProductionPlace"].tolist()
    image, projection = decision_image()
    transform = projection.getInfo()["transform"]
    baseline = pd.read_csv(RESULTS_DIR / "baseline_verdicts.csv", keep_default_na=False).set_index("plot_id")

    sample = plots.sample(args.single_sample, random_state=0).index
    single_times, single_counts = time_single(image, transform, [footprints[i] for i in sample],
                                              [plot_ids[i] for i in sample])
    batch_s, batch_counts, profile = time_batch(image, transform, footprints, plot_ids)
    PROFILE_PATH.write_text(profile, encoding="utf-8")

    results = {
        "project": args.project,
        "single": {"sample": len(single_times), "median_s": statistics.median(single_times),
                   "min_s": min(single_times), "max_s": max(single_times),
                   "agreement_with_baseline": agreement(single_counts, baseline)},
        "batch": {"plots": len(plot_ids), "total_s": batch_s, "per_plot_ms": 1000 * batch_s / len(plot_ids),
                  "agreement_with_baseline": agreement(batch_counts, baseline)},
        "profile_file": PROFILE_PATH.name,
    }
    OUT_PATH.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(json.dumps(results, indent=2))
    print(f"Earth Engine profile (EECU use) written to {PROFILE_PATH}")


if __name__ == "__main__":
    main()
