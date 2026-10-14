"""
The compact store: file layout, the 16-bit cell encoding, tile-level reads and the manifest.

Layout (data/compact/):
  base.tif                 uint16 COG on the GFC2020 grid, one value per cell (bit layout below)
  events/<batch>.parquet   one row per newly detected disturbance: cell_id, date, source
  protected_areas.parquet  cleaned national WDPA areas (d1 strict, d2 restricted), clipped to the AOI
  plot_index.parquet       plot_id -> run-length encoded cells (row, col_start, col_end) whose centre lies in the plot
  plot_pa.parquet          plot_id, pa_class, pa_name, class_share (plot share inside that class)
  manifest.json            source datasets, grid, encoding, data state, event batches, file sizes and hashes

Cell encoding (16 bits): bit 15 = forest on 31 Dec 2020; bits 12-14 = disturbance sources
(1 Hansen, 2 RADD high, 4 RADD low); bits 0-11 = first post-cut-off disturbance date as days after
2020-12-31 (0 = none). 12 bits cover dates up to 2032-03-18; later dates are refused, not wrapped.
"""

import datetime as dt
import hashlib
import json

import numpy as np
import rasterio
from rasterio.io import MemoryFile
from rasterio.shutil import copy as copy_raster
from rasterio.windows import Window

from layers import ROOT

STORE_DIR = ROOT / "data" / "compact"
BASE_PATH = STORE_DIR / "base.tif"
EVENTS_DIR = STORE_DIR / "events"
PA_PATH = STORE_DIR / "protected_areas.parquet"
PLOT_INDEX_PATH = STORE_DIR / "plot_index.parquet"
PLOT_PA_PATH = STORE_DIR / "plot_pa.parquet"
MANIFEST_PATH = STORE_DIR / "manifest.json"

FOREST_BIT = 15
SOURCE_SHIFT = 12
SOURCE_BITS = 0b111
DAY_BITS = 0x0FFF
DAY_ZERO = dt.date(2020, 12, 31)  # the EUDR cut-off; day 1 = 2021-01-01
DAY_ZERO_EPOCH = (DAY_ZERO - dt.date(1970, 1, 1)).days
LAST_ENCODABLE_DATE = DAY_ZERO + dt.timedelta(days=DAY_BITS)

COG_COMPRESS = "ZSTD"
COG_LEVEL = 19
COG_PREDICTOR = "NO"  # differencing hurts bit-packed fields: 95.8 kB vs 110.8 kB with it
COG_BLOCKSIZE = 512


def pack(forest, first_date, sources):
    """Encode per-cell forest flag, first date (epoch days, 0 = none) and source flags into uint16."""
    has_date = first_date != 0
    day = np.where(has_date, first_date.astype(np.int64) - DAY_ZERO_EPOCH, 0)
    if has_date.any() and (day[has_date].min() < 1 or day[has_date].max() > DAY_BITS):
        raise ValueError(f"Disturbance dates must fall between {DAY_ZERO} (exclusive) and {LAST_ENCODABLE_DATE}")
    if (sources > SOURCE_BITS).any():
        raise ValueError("Source flags use more than 3 bits")
    packed = (forest.astype(np.uint16) << FOREST_BIT) | (sources.astype(np.uint16) << SOURCE_SHIFT)
    return packed | day.astype(np.uint16)


def unpack(packed):
    """Inverse of pack: (forest bool, first date in epoch days with 0 = none, source flags)."""
    packed = packed.astype(np.uint16)
    forest = (packed >> FOREST_BIT).astype(bool)
    sources = ((packed >> SOURCE_SHIFT) & SOURCE_BITS).astype(np.uint8)
    day = (packed & DAY_BITS).astype(np.int32)
    return forest, np.where(day > 0, day + DAY_ZERO_EPOCH, 0).astype(np.int32), sources


def write_cog(values, transform, path, compress=COG_COMPRESS, level=COG_LEVEL, predictor=COG_PREDICTOR,
              blocksize=COG_BLOCKSIZE):
    """Write a single-band uint16 Cloud-Optimised GeoTIFF without overviews (decisions, not maps)."""
    height, width = values.shape
    profile = dict(driver="GTiff", dtype="uint16", count=1, width=width, height=height,
                   crs="EPSG:4326", transform=transform)
    options = dict(COMPRESS=compress, PREDICTOR=predictor, BLOCKSIZE=blocksize, OVERVIEWS="NONE")
    if level is not None:
        options["LEVEL"] = level
    path.parent.mkdir(parents=True, exist_ok=True)
    with MemoryFile() as memory:
        with memory.open(**profile) as tmp:
            tmp.write(values, 1)
        with memory.open() as tmp:
            copy_raster(tmp, path, driver="COG", **options)


def read_cells(path, rows, cols):
    """Values at the given cells, reading each needed tile once.

    Returns (values, compressed bytes of the tiles read, number of tiles read) so a query's I/O
    can be reported, as it would be for range requests against object storage.
    """
    rows, cols = np.asarray(rows), np.asarray(cols)
    values = np.empty(len(rows), dtype=np.uint16)
    with rasterio.open(path) as src:
        block_h, block_w = src.block_shapes[0]
        tile_row, tile_col = rows // block_h, cols // block_w
        tiles = np.unique(np.stack([tile_row, tile_col], axis=1), axis=0) if len(rows) else []
        bytes_read = 0
        for tr, tc in tiles:
            window = Window(tc * block_w, tr * block_h, min(block_w, src.width - tc * block_w),
                            min(block_h, src.height - tr * block_h))
            block = src.read(1, window=window)
            in_tile = (tile_row == tr) & (tile_col == tc)
            values[in_tile] = block[rows[in_tile] - tr * block_h, cols[in_tile] - tc * block_w]
            bytes_read += int(src.get_tag_item(f"BLOCK_SIZE_{tc}_{tr}", "TIFF", bidx=1) or 0)
    return values, bytes_read, len(tiles)


def file_record(path):
    data = path.read_bytes()
    return {"path": str(path.relative_to(STORE_DIR)), "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def load_manifest():
    if not MANIFEST_PATH.exists():
        raise FileNotFoundError(f"{MANIFEST_PATH} not found; run scripts/encode_layers.py first")
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(manifest):
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
