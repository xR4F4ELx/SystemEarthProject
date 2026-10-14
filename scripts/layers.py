"""
Shared readers for the raw layers and plot footprints, following docs/decision-spec.md.

Everything is put on the GFC2020 grid. Both the raw baseline pipeline and the compact pipeline
use these readers, so a fidelity mismatch can only come from the encoding, not from how the
layers were read.
"""

from dataclasses import dataclass
from pathlib import Path

import geopandas as gpd
import numpy as np
import rasterio
import shapely
from affine import Affine
from pyproj import Geod
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union
from shapely.validation import make_valid

ROOT = Path(__file__).resolve().parents[1]
RAW_DIR = ROOT / "data" / "raw"
PLOTS_DIR = ROOT / "data" / "plots"
RAW_PREFIX = "ci_tai_soubre_"

AOI_BOUNDS = (-7.1, 5.6, -6.6, 6.05)  # west, south, east, north (degrees)
HANSEN_FIRST_POST_CUTOFF_YEAR = 21  # lossyear 21 = 2021
RADD_FIRST_POST_CUTOFF_DATE = 21001  # YYDDD, 1 Jan 2021
RADD_HIGH_CONFIDENCE = 3
STRICT_IUCN = frozenset({"Ia", "Ib", "II", "III", "IV"})
NATIONAL_DESIGNATION = "National"

M_PER_DEG_LAT = 110_574.0
M_PER_DEG_LON_AT_EQUATOR = 111_320.0
M2_PER_HA = 10_000.0
GEOD = Geod(ellps="WGS84")


@dataclass(frozen=True)
class Grid:
    """The GFC2020 grid every layer is put on."""

    transform: Affine
    height: int
    width: int

    @property
    def shape(self):
        return (self.height, self.width)

    def cell_centres(self):
        """Longitude of each column centre and latitude of each row centre."""
        lon = self.transform.c + (np.arange(self.width) + 0.5) * self.transform.a
        lat = self.transform.f + (np.arange(self.height) + 0.5) * self.transform.e
        return lon, lat


def raw_path(name):
    return RAW_DIR / f"{RAW_PREFIX}{name}"


def load_forest_2020():
    """GFC2020 forest mask and its grid. Non-forest is masked in the source and becomes False."""
    with rasterio.open(raw_path("gfc2020.tif")) as src:
        forest = src.read(1) == 1
        return forest, Grid(src.transform, src.height, src.width)


def resample_to_grid(path, band_name, grid):
    """Nearest neighbour by cell-centre lookup: each grid cell takes the source value at its centre.

    Cells whose centre falls outside the source raster get 0.
    """
    with rasterio.open(path) as src:
        if band_name not in src.descriptions:
            raise ValueError(f"{path.name} has no band {band_name!r}; bands are {src.descriptions}")
        data = src.read(src.descriptions.index(band_name) + 1)
        src_transform = src.transform

    lon, lat = grid.cell_centres()
    cols = np.floor((lon - src_transform.c) / src_transform.a).astype(np.int64)
    rows = np.floor((lat - src_transform.f) / src_transform.e).astype(np.int64)
    valid_cols = (cols >= 0) & (cols < data.shape[1])
    valid_rows = (rows >= 0) & (rows < data.shape[0])

    out = np.zeros(grid.shape, dtype=data.dtype)
    out[np.ix_(valid_rows, valid_cols)] = data[np.ix_(rows[valid_rows], cols[valid_cols])]
    return out


def load_hansen_lossyear(grid):
    return resample_to_grid(raw_path("hansen.tif"), "lossyear", grid)


def load_radd(grid):
    """RADD alert confidence (2 = low, 3 = high) and date (YYDDD), on the GFC2020 grid."""
    path = raw_path("radd.tif")
    return resample_to_grid(path, "Alert", grid), resample_to_grid(path, "Date", grid)


def polygon_parts(geom):
    """Valid polygonal part of a geometry. Taï park records arrive as mixed geometry collections."""
    valid = make_valid(geom)
    parts = [g for g in getattr(valid, "geoms", [valid]) if isinstance(g, (Polygon, MultiPolygon))]
    return unary_union(parts)


def pa_class(desig_type, iucn_cat):
    """d1 'strict', d2 'restricted', or excluded (international designations; see spec d2)."""
    if desig_type != NATIONAL_DESIGNATION:
        return "excluded_international"
    return "strict" if iucn_cat in STRICT_IUCN else "restricted"


def load_protected_areas():
    """Cleaned WDPA polygons with their d1/d2 class."""
    raw = gpd.read_file(raw_path("wdpa.geojson"))
    cleaned = raw.assign(
        geometry=raw.geometry.apply(polygon_parts),
        pa_class=[pa_class(t, c) for t, c in zip(raw["DESIG_TYPE"], raw["IUCN_CAT"])],
    )
    columns = ["NAME", "DESIG_ENG", "DESIG_TYPE", "IUCN_CAT", "pa_class", "geometry"]
    return cleaned.loc[~cleaned.geometry.is_empty, columns].reset_index(drop=True)


def metres_to_degrees(dx, dy, lat):
    return dx / (M_PER_DEG_LON_AT_EQUATOR * np.cos(np.radians(lat))), dy / M_PER_DEG_LAT


def point_footprint(lon, lat, area_ha, n_vertices=64):
    """Circle of the declared area around a point plot (spec: point plots)."""
    radius = np.sqrt(area_ha * M2_PER_HA / np.pi)
    angles = np.linspace(0, 2 * np.pi, n_vertices, endpoint=False)
    dlon, dlat = metres_to_degrees(radius * np.cos(angles), radius * np.sin(angles), lat)
    return Polygon(zip(lon + dlon, lat + dlat))


def geodesic_area_ha(geom):
    return abs(GEOD.geometry_area_perimeter(geom)[0]) / M2_PER_HA


def plot_footprint(geometry, area_ha):
    """The area a plot covers: its polygon, or a circle of the declared area for a point plot."""
    if geometry.geom_type == "Point":
        return point_footprint(geometry.x, geometry.y, area_ha)
    return geometry


def plot_cells(footprint, grid):
    """Rows and columns of the grid cells whose centre lies inside the plot (spec: pixel in plot)."""
    west, south, east, north = footprint.bounds
    inverse = ~grid.transform
    col_min, row_min = inverse @ (west, north)
    col_max, row_max = inverse @ (east, south)
    cols = np.arange(max(int(np.floor(col_min)), 0), min(int(np.ceil(col_max)), grid.width))
    rows = np.arange(max(int(np.floor(row_min)), 0), min(int(np.ceil(row_max)), grid.height))
    lon = grid.transform.c + (cols + 0.5) * grid.transform.a
    lat = grid.transform.f + (rows + 0.5) * grid.transform.e
    inside = shapely.contains_xy(footprint, *np.meshgrid(lon, lat))
    row_idx, col_idx = np.nonzero(inside)
    return rows[row_idx], cols[col_idx]


def cell_area_ha(rows, grid):
    """Area of each cell in the given rows, at that row's latitude (spec: areas)."""
    lat = grid.transform.f + (np.asarray(rows) + 0.5) * grid.transform.e
    width_m = grid.transform.a * M_PER_DEG_LON_AT_EQUATOR * np.cos(np.radians(lat))
    height_m = abs(grid.transform.e) * M_PER_DEG_LAT
    return width_m * height_m / M2_PER_HA
