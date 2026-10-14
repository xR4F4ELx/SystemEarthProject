"""
Export raw EUDR layers for the Côte d'Ivoire cocoa area of interest from Google Earth Engine.

What it exports (to a Google Drive folder, as GeoTIFF / GeoJSON):
  1. gfc2020.tif        JRC GFC2020 v4, 10 m, forest baseline at the cut-off
  2. hansen.tif         Hansen GFC v1.13, native ~30 m: treecover2000, loss, lossyear, datamask
  3. radd.tif           RADD alerts (Africa), 10 m: Alert (confidence) + Date
  4. wdpa.geojson       WDPA protected-area polygons intersecting the AOI
  5. manifest.json      Written locally: dataset IDs, versions, AOI, export date

These are the RAW layers (the "original" side of the evaluation). Masking, resampling to the
10 m grid and dropping pre-cut-off data happen later, in the baseline and encoder scripts,
so storage can be compared fairly against what you start from.

Setup (once):
    pip install earthengine-api
    earthengine authenticate
Run:
    python scripts/export_layers.py --project YOUR_GCP_PROJECT_ID
Then watch progress at https://code.earthengine.google.com/tasks (or the 'Tasks' tab).
"""

import argparse
import datetime as dt
import json

import ee

# ---------------------------------------------------------------------------
# Configuration — change the AOI here after checking it on a map.
# ---------------------------------------------------------------------------
AOI_BOUNDS = [-7.1, 5.6, -6.6, 6.05]  # [west, south, east, north], degrees (~55 x 50 km)
AOI_NAME = "ci_tai_soubre"

DATASETS = {
    "gfc2020": "JRC/GFC2020/V4",
    "hansen": "UMD/hansen/global_forest_change_2025_v1_13",
    "radd": "projects/radar-wur/raddalert/v1",  # VERIFY: community asset, see check below
    "wdpa": "WCMC/WDPA/current/polygons",
}
HANSEN_BANDS = ["treecover2000", "loss", "lossyear", "datamask"]  # drop imagery bands (b30..b70)


def get_radd_africa():
    """Return the latest RADD Africa alert image, or None with a message if the asset differs."""
    try:
        col = (
            ee.ImageCollection(DATASETS["radd"])
            .filterMetadata("layer", "contains", "alert")
            .filterMetadata("geography", "equals", "africa")
            .sort("system:time_end", False)
        )
        img = ee.Image(col.first())
        bands = img.bandNames().getInfo()
        print(f"[radd] bands: {bands}")
        return img, img.get("system:index").getInfo()
    except Exception as exc:  # asset path or metadata changed
        print(f"[radd] could not load RADD ({exc}). Check the asset path and metadata, then re-run.")
        return None, None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, help="Google Cloud project registered for Earth Engine")
    parser.add_argument("--folder", default="systemearth_raw", help="Google Drive folder for exports")
    parser.add_argument("--dry-run", action="store_true", help="Print checks only, start no exports")
    args = parser.parse_args()

    ee.Initialize(project=args.project)
    aoi = ee.Geometry.Rectangle(AOI_BOUNDS)

    # --- 1. GFC2020: check band name and values before trusting the spec ---
    gfc = ee.Image(DATASETS["gfc2020"])
    gfc_bands = gfc.bandNames().getInfo()
    print(f"[gfc2020] bands: {gfc_bands}")
    gfc_hist = gfc.select(0).reduceRegion(
        ee.Reducer.frequencyHistogram(), aoi, scale=10, maxPixels=1e10, bestEffort=True
    ).getInfo()
    print(f"[gfc2020] value histogram in AOI (expect 1 = forest): {gfc_hist}")
    gfc_proj = gfc.select(0).projection()  # 10 m grid reused for 10 m exports

    # --- 2. Hansen: keep analytic bands only, native resolution ---
    hansen = ee.Image(DATASETS["hansen"]).select(HANSEN_BANDS)
    hansen_proj = hansen.select("lossyear").projection()

    # --- 3. RADD ---
    radd, radd_id = get_radd_africa()

    # --- 4. WDPA ---
    wdpa = ee.FeatureCollection(DATASETS["wdpa"]).filterBounds(aoi)
    n_pa = wdpa.size().getInfo()
    print(f"[wdpa] protected areas intersecting AOI: {n_pa}")
    if n_pa:
        names = wdpa.aggregate_array("NAME").getInfo()
        cats = wdpa.aggregate_array("IUCN_CAT").getInfo()
        for n, c in zip(names, cats):
            print(f"        - {n} (IUCN {c})")

    if args.dry_run:
        print("Dry run: no exports started.")
        return

    tasks = []

    def raster_task(image, name, proj):
        # Export on the layer's own native grid (same CRS and pixel alignment) so "raw" stays raw.
        info = proj.getInfo()
        print(f"[{name}] native grid: {info['crs']} {info['transform']}")
        task = ee.batch.Export.image.toDrive(
            image=image.clip(aoi),
            description=f"{AOI_NAME}_{name}",
            folder=args.folder,
            fileNamePrefix=f"{AOI_NAME}_{name}",
            region=aoi,
            crs=info["crs"],
            crsTransform=info["transform"],
            maxPixels=1e10,
            fileFormat="GeoTIFF",
            formatOptions={"cloudOptimized": True},
        )
        task.start()
        tasks.append(name)

    
    raster_task(gfc.toUint8(), "gfc2020", gfc_proj)
    raster_task(hansen.toUint8(), "hansen", hansen_proj)
    if radd is not None:
        raster_task(radd.toInt32(), "radd", radd.select(0).projection())

    wdpa_task = ee.batch.Export.table.toDrive(
        collection=wdpa,
        description=f"{AOI_NAME}_wdpa",
        folder=args.folder,
        fileNamePrefix=f"{AOI_NAME}_wdpa",
        fileFormat="GeoJSON",
    )
    wdpa_task.start()
    tasks.append("wdpa")

    # --- 5. Manifest: what was used, so every later verdict can be reproduced ---
    manifest = {
        "manifest_id": f"aoi-{AOI_NAME}-{dt.date.today().isoformat()}",
        "aoi_bounds_wsen": AOI_BOUNDS,
        "export_date_utc": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "cutoff_date": "2020-12-31",
        "datasets": {
            "gfc2020": {"id": DATASETS["gfc2020"], "bands": gfc_bands},
            "hansen": {"id": DATASETS["hansen"], "bands": HANSEN_BANDS},
            "radd": {"id": DATASETS["radd"], "image": radd_id},
            "wdpa": {"id": DATASETS["wdpa"], "features_in_aoi": n_pa},
        },
    }
    with open("manifest.json", "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"Started exports: {tasks}. Manifest written to manifest.json.")


if __name__ == "__main__":
    main()
