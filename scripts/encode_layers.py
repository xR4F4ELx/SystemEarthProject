"""
Encode the raw layers into the compact store (step 4). See scripts/compact.py for the layout.

Modes:
  build   base raster + protected areas + a fresh manifest, from the raw layers at a data state.
          Replaces any earlier store, including its event batches (they belong to the old base).
  events  extract the detections published since the manifest's data state as one event batch,
          and record it in the manifest. Only cells that were forest in 2020 can produce events.

Simulated update used in the demo:
    python scripts/encode_layers.py build --radd-until 2026-06-30 --hansen-max-year 24
    python scripts/encode_layers.py events --batch 2026-10-07
"""

import argparse
import datetime as dt
import json
import shutil

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import box

from baseline import (
    HANSEN_LAST_YEAR,
    RADD_NO_LIMIT,
    SOURCE_HANSEN,
    SOURCE_RADD_HIGH,
    SOURCE_RADD_LOW,
    date_to_yyddd,
    load_manifest_id,
    raw_disturbance,
    to_epoch_days,
)
from compact import (
    BASE_PATH,
    COG_BLOCKSIZE,
    COG_COMPRESS,
    COG_LEVEL,
    DAY_ZERO,
    EVENTS_DIR,
    PA_PATH,
    STORE_DIR,
    file_record,
    load_manifest,
    pack,
    save_manifest,
    write_cog,
)
from layers import (
    AOI_BOUNDS,
    HANSEN_FIRST_POST_CUTOFF_YEAR,
    RADD_FIRST_POST_CUTOFF_DATE,
    RADD_HIGH_CONFIDENCE,
    ROOT,
    load_forest_2020,
    load_hansen_lossyear,
    load_protected_areas,
    load_radd,
)

RAW_MANIFEST_PATH = ROOT / "manifest.json"
PARQUET_COMPRESSION = "zstd"


def write_protected_areas():
    """National d1/d2 areas only (international designations are excluded by the spec), clipped to the AOI."""
    protected = load_protected_areas()
    kept = protected[protected.pa_class.isin(["strict", "restricted"])]
    clipped = kept.assign(geometry=kept.geometry.intersection(box(*AOI_BOUNDS)))
    clipped[["NAME", "pa_class", "geometry"]].to_parquet(PA_PATH, compression=PARQUET_COMPRESSION)


def state_record(radd_until, hansen_max_year):
    return {"radd_until_yyddd": radd_until, "hansen_max_year": 2000 + hansen_max_year}


def build(radd_until, hansen_max_year):
    forest, grid = load_forest_2020()
    alert, radd_date = load_radd(grid)
    first_date, sources = raw_disturbance(forest, load_hansen_lossyear(grid), alert, radd_date,
                                          hansen_max_year, radd_until)
    if EVENTS_DIR.exists():
        shutil.rmtree(EVENTS_DIR)
    STORE_DIR.mkdir(parents=True, exist_ok=True)
    write_cog(pack(forest, first_date, sources), grid.transform, BASE_PATH)
    write_protected_areas()

    manifest = {
        "manifest_id": load_manifest_id(radd_until, hansen_max_year),
        "built_utc": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "source_export": json.loads(RAW_MANIFEST_PATH.read_text(encoding="utf-8")),
        "grid": {"crs": "EPSG:4326", "transform": list(grid.transform)[:6], "width": grid.width,
                 "height": grid.height},
        "encoding": {"base": "uint16: bit15 forest_2020, bits12-14 sources (1 hansen, 2 radd_high, "
                             "4 radd_low), bits0-11 days after " + DAY_ZERO.isoformat() + " (0 = none)",
                     "cog": {"compress": COG_COMPRESS, "level": COG_LEVEL, "blocksize": COG_BLOCKSIZE}},
        "base_state": state_record(radd_until, hansen_max_year),
        "state": state_record(radd_until, hansen_max_year),
        "event_batches": [],
        "files": [file_record(BASE_PATH), file_record(PA_PATH)],
    }
    save_manifest(manifest)
    return manifest


def extract_events(since_radd, since_hansen, until_radd=RADD_NO_LIMIT, until_hansen=HANSEN_LAST_YEAR):
    """New detections inside 2020 forest: one row per (cell, source), sorted by cell."""
    forest, grid = load_forest_2020()
    lossyear = load_hansen_lossyear(grid)
    alert, radd_date = load_radd(grid)
    first_hansen = max(since_hansen + 1, HANSEN_FIRST_POST_CUTOFF_YEAR)
    hansen_new = forest & (lossyear >= first_hansen) & (lossyear <= until_hansen)
    radd_new = forest & (radd_date > since_radd) & (radd_date >= RADD_FIRST_POST_CUTOFF_DATE) & (radd_date <= until_radd)

    hansen_cells = np.flatnonzero(hansen_new)
    radd_cells = np.flatnonzero(radd_new)
    hansen_days = to_epoch_days(2000 + lossyear.ravel()[hansen_cells].astype(np.int64), 1)
    radd_values = radd_date.ravel()[radd_cells]
    radd_days = to_epoch_days(2000 + radd_values // 1000, radd_values % 1000)
    radd_sources = np.where(alert.ravel()[radd_cells] == RADD_HIGH_CONFIDENCE, SOURCE_RADD_HIGH, SOURCE_RADD_LOW)

    cells = np.concatenate([hansen_cells, radd_cells]).astype(np.uint32)
    days = np.concatenate([hansen_days, radd_days]).astype(np.int32)
    sources = np.concatenate([np.full(len(hansen_cells), SOURCE_HANSEN), radd_sources]).astype(np.uint8)
    order = np.lexsort((sources, days, cells))
    return pa.table({"cell_id": pa.array(cells[order], pa.uint32()),
                     "date": pa.array(days[order], pa.date32()),
                     "source": pa.array(sources[order], pa.uint8())})


def publish_events(batch_id, until_radd=RADD_NO_LIMIT, until_hansen=HANSEN_LAST_YEAR):
    manifest = load_manifest()
    since = manifest["state"]
    events = extract_events(since["radd_until_yyddd"], since["hansen_max_year"] - 2000, until_radd, until_hansen)
    EVENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EVENTS_DIR / f"{batch_id}.parquet"
    pq.write_table(events, path, compression=PARQUET_COMPRESSION)

    record = {"batch": batch_id, "rows": events.num_rows, "since": since,
              "until": state_record(until_radd, until_hansen), **file_record(path)}
    manifest["event_batches"].append(record)
    manifest["state"] = state_record(until_radd, until_hansen)
    manifest["manifest_id"] = load_manifest_id(until_radd, until_hansen)
    save_manifest(manifest)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("build", "events"):
        mode = sub.add_parser(name)
        mode.add_argument("--radd-until", type=dt.date.fromisoformat, help="last RADD date to include")
        mode.add_argument("--hansen-max-year", type=int, default=HANSEN_LAST_YEAR, help="last lossyear (21-25)")
    sub.choices["events"].add_argument("--batch", required=True, help="batch id, e.g. the release date")
    args = parser.parse_args()
    radd_until = date_to_yyddd(args.radd_until) if args.radd_until else RADD_NO_LIMIT

    if args.mode == "build":
        manifest = build(radd_until, args.hansen_max_year)
        sizes = ", ".join(f"{f['path']} {f['bytes'] / 1e3:.1f} kB" for f in manifest["files"])
        print(f"Built {manifest['manifest_id']}: {sizes}")
    else:
        record = publish_events(args.batch, radd_until, args.hansen_max_year)
        print(f"Event batch {record['batch']}: {record['rows']} rows, {record['bytes'] / 1e3:.1f} kB")


if __name__ == "__main__":
    main()
