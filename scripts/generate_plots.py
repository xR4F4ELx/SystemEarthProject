"""
Generate ~500 stratified synthetic cocoa plots in the AOI (step 2; decision recorded in CLAUDE.md).

Plots are spread over the cases where the decision is hard, not over the landscape, so fidelity is
tested on every rule path. Agreement must be reported per stratum; landscape-wide flag rates come
from random plots instead (docs/business-case.md).

Outputs:
  data/plots/plots.geojson     SystemEarth's customer schema only: ProducerName, ProducerCountry,
                               ProductionPlace (plot ID), Area (ha). Some plots under 4 ha are points.
  data/plots/plot_strata.csv   plot_id, stratum, geometry_type. For evaluation only; kept out of the
                               GeoJSON so neither pipeline can see it.
Run:
    python scripts/generate_plots.py [--seed 42]
"""

import argparse

import geopandas as gpd
import numpy as np
import pandas as pd
from rasterio.features import rasterize
from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union
from shapely.prepared import prep

from layers import (
    AOI_BOUNDS,
    HANSEN_FIRST_POST_CUTOFF_YEAR,
    M2_PER_HA,
    PLOTS_DIR,
    RADD_FIRST_POST_CUTOFF_DATE,
    geodesic_area_ha,
    load_forest_2020,
    load_hansen_lossyear,
    load_protected_areas,
    load_radd,
    metres_to_degrees,
    point_footprint,
)

STRATUM_COUNTS = {
    "forest_fragment": 150,  # 2020 forest outside protected areas: c2 alone decides
    "cocoa_land": 150,  # non-forest farmland, often with Hansen loss: supplier-style false flags
    "classified_forest": 75,  # d2
    "park_boundary": 75,  # d1 and its 1% edge_overlap rule
    "inside_park": 50,  # d1, and the RADD update (RADD fires almost only in the park)
}
MEDIAN_AREA_HA = 2.5
AREA_LOG_SIGMA = 0.7
MIN_AREA_HA, MAX_AREA_HA = 0.5, 10.0
POINT_MAX_AREA_HA = 4.0  # EUDR allows a point instead of a polygon up to 4 ha
POINT_SHARE_OF_SMALL = 0.4  # about 30% of all plots end up as points
MAX_ASPECT = 2.5
VERTEX_JITTER = 0.1  # share of the shorter side each corner may move
AOI_MARGIN_DEG = 0.002  # keep plots about 200 m inside the AOI edge
BOUNDARY_OFFSET_M = 120  # edge plots are centred within this distance of a protected-area boundary
MAX_ATTEMPTS_PER_PLOT = 5000
COOPERATIVES = [f"CI-COOP-{i:02d}" for i in range(1, 6)]
PRODUCER_COUNTRY = "CI"


def rasterize_mask(geoms, grid):
    shapes = [(g, 1) for g in geoms if not g.is_empty]
    if not shapes:
        return np.zeros(grid.shape, dtype=bool)
    return rasterize(shapes, out_shape=grid.shape, transform=grid.transform, dtype="uint8") == 1


def build_landscape(grid, forest, protected):
    """Masks and geometries the samplers and acceptance rules need."""
    lossyear = load_hansen_lossyear(grid)
    _, radd_date = load_radd(grid)
    hansen_post = lossyear >= HANSEN_FIRST_POST_CUTOFF_YEAR
    radd_post = radd_date >= RADD_FIRST_POST_CUTOFF_DATE

    strict = unary_union(protected.loc[protected.pa_class == "strict", "geometry"].tolist())
    restricted = unary_union(protected.loc[protected.pa_class == "restricted", "geometry"].tolist())
    any_pa = unary_union(protected.geometry.tolist())  # incl. international, so "outside" means outside all
    any_pa_mask = rasterize_mask([any_pa], grid)
    strict_mask = rasterize_mask([strict], grid)

    return {
        "grid": grid,
        "strict_prep": prep(strict),
        "restricted_prep": prep(restricted),
        "any_pa_prep": prep(any_pa),
        "pools": {
            "forest_fragment": [
                cell_pool(forest & ~any_pa_mask, 2 / 3),
                cell_pool(forest & (hansen_post | radd_post) & ~any_pa_mask, 1 / 3),
            ],
            "cocoa_land": [
                cell_pool(~forest & hansen_post & ~any_pa_mask, 1 / 2),
                cell_pool(~forest & ~any_pa_mask, 1 / 2),
            ],
            # a third on the edge, so d2's edge_overlap rule is exercised too
            "classified_forest": [cell_pool(rasterize_mask([restricted], grid), 2 / 3), edge_pool(restricted, 1 / 3)],
            "park_boundary": [edge_pool(strict, 1.0)],
            "inside_park": [
                cell_pool(strict_mask, 1 / 2),
                cell_pool(strict_mask & forest & radd_post, 1 / 2),
            ],
        },
    }


def cell_pool(mask, weight):
    return ("cells", np.flatnonzero(mask), weight)


def edge_pool(area, weight):
    return ("edge", boundary_parts(area), weight)


def boundary_parts(area):
    """Edge lines of a protected area inside the AOI (away from its border), with length weights."""
    inner_aoi = box(*shrink(AOI_BOUNDS, AOI_MARGIN_DEG))
    edge = area.boundary.intersection(inner_aoi)
    parts = [p for p in getattr(edge, "geoms", [edge]) if p.length > 0]
    if not parts:
        raise RuntimeError("Protected area has no boundary inside the AOI")
    lengths = np.array([p.length for p in parts])
    return parts, lengths / lengths.sum()


def shrink(bounds, margin):
    west, south, east, north = bounds
    return (west + margin, south + margin, east - margin, north - margin)


def cell_centre(flat_index, grid, rng):
    row, col = divmod(int(flat_index), grid.width)
    lon = grid.transform.c + (col + rng.uniform()) * grid.transform.a
    lat = grid.transform.f + (row + rng.uniform()) * grid.transform.e
    return lon, lat


def near_edge(boundary, rng):
    parts, weights = boundary
    line = parts[rng.choice(len(parts), p=weights)]
    anchor = line.interpolate(rng.uniform(0, line.length))
    dlon, dlat = metres_to_degrees(*rng.uniform(-BOUNDARY_OFFSET_M, BOUNDARY_OFFSET_M, 2), anchor.y)
    return anchor.x + dlon, anchor.y + dlat


def sample_centre(stratum, landscape, rng):
    pools = landscape["pools"][stratum]
    weights = np.array([0.0 if kind == "cells" and len(payload) == 0 else w for kind, payload, w in pools])
    if weights.sum() == 0:
        raise RuntimeError(f"No candidate cells for stratum {stratum!r}")
    kind, payload, _ = pools[rng.choice(len(pools), p=weights / weights.sum())]
    if kind == "edge":
        return near_edge(payload, rng)
    return cell_centre(rng.choice(payload), landscape["grid"], rng)


def draw_area_ha(rng):
    area = rng.lognormal(np.log(MEDIAN_AREA_HA), AREA_LOG_SIGMA)
    return round(float(np.clip(area, MIN_AREA_HA, MAX_AREA_HA)), 2)


def make_polygon(lon, lat, area_ha, rng):
    """Jittered, rotated rectangle scaled to the exact geodesic area."""
    area_m2 = area_ha * M2_PER_HA
    width = np.sqrt(area_m2 * rng.uniform(1.0, MAX_ASPECT))
    height = area_m2 / width
    corners = np.array([[-width, -height], [width, -height], [width, height], [-width, height]]) / 2
    corners = corners + rng.uniform(-VERTEX_JITTER, VERTEX_JITTER, corners.shape) * min(width, height)
    angle = rng.uniform(0, np.pi)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    xy = corners @ rotation.T
    dlon, dlat = metres_to_degrees(xy[:, 0], xy[:, 1], lat)
    polygon = Polygon(zip(lon + dlon, lat + dlat))
    scale = np.sqrt(area_ha / geodesic_area_ha(polygon))
    return affinity.scale(polygon, scale, scale, origin=(lon, lat))


def fits_stratum(stratum, footprint, landscape):
    if stratum in ("forest_fragment", "cocoa_land"):
        return not landscape["any_pa_prep"].intersects(footprint)
    if stratum == "classified_forest":
        return landscape["restricted_prep"].intersects(footprint)
    if stratum == "park_boundary":
        return landscape["strict_prep"].intersects(footprint) and not landscape["strict_prep"].contains(footprint)
    if stratum == "inside_park":
        return landscape["strict_prep"].contains(footprint)
    raise ValueError(f"Unknown stratum {stratum!r}")


def place_plot(stratum, landscape, placed_footprints, inner_aoi, rng):
    for _ in range(MAX_ATTEMPTS_PER_PLOT):
        lon, lat = sample_centre(stratum, landscape, rng)
        area_ha = draw_area_ha(rng)
        is_point = area_ha < POINT_MAX_AREA_HA and rng.uniform() < POINT_SHARE_OF_SMALL
        if is_point:
            geometry = Point(lon, lat)
            footprint = point_footprint(lon, lat, area_ha)
        else:
            geometry = make_polygon(lon, lat, area_ha, rng)
            footprint = geometry
            area_ha = round(geodesic_area_ha(geometry), 2)
        if not inner_aoi.contains(footprint) or not fits_stratum(stratum, footprint, landscape):
            continue
        if any(footprint.intersects(other) for other in placed_footprints):
            continue
        return {"stratum": stratum, "geometry": geometry, "footprint": footprint, "area_ha": area_ha,
                "geometry_type": "point" if is_point else "polygon"}
    raise RuntimeError(f"Could not place a {stratum} plot after {MAX_ATTEMPTS_PER_PLOT} attempts")


def generate(seed):
    """Return (plots GeoDataFrame in customer schema, strata DataFrame)."""
    rng = np.random.default_rng(seed)
    forest, grid = load_forest_2020()
    landscape = build_landscape(grid, forest, load_protected_areas())
    inner_aoi = prep(box(*shrink(AOI_BOUNDS, AOI_MARGIN_DEG)))

    placed = []
    for stratum, count in STRATUM_COUNTS.items():
        for _ in range(count):
            placed.append(place_plot(stratum, landscape, [p["footprint"] for p in placed], inner_aoi, rng))

    order = rng.permutation(len(placed))  # IDs must not reveal the stratum
    shuffled = [placed[i] for i in order]
    plot_ids = [f"CI-{i:04d}" for i in range(1, len(shuffled) + 1)]
    plots = gpd.GeoDataFrame(
        {
            "ProducerName": rng.choice(COOPERATIVES, len(shuffled)),
            "ProducerCountry": PRODUCER_COUNTRY,
            "ProductionPlace": plot_ids,
            "Area": [p["area_ha"] for p in shuffled],
        },
        geometry=[p["geometry"] for p in shuffled],
        crs="EPSG:4326",
    )
    strata = pd.DataFrame(
        {
            "plot_id": plot_ids,
            "stratum": [p["stratum"] for p in shuffled],
            "geometry_type": [p["geometry_type"] for p in shuffled],
        }
    )
    return plots, strata


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    plots, strata = generate(args.seed)
    PLOTS_DIR.mkdir(parents=True, exist_ok=True)
    plots.to_file(PLOTS_DIR / "plots.geojson", driver="GeoJSON")
    strata.to_csv(PLOTS_DIR / "plot_strata.csv", index=False)

    summary = strata.assign(area=plots["Area"]).groupby("stratum").agg(
        plots=("plot_id", "size"),
        points=("geometry_type", lambda s: int((s == "point").sum())),
        median_ha=("area", "median"),
    )
    print(summary.to_string())
    print(f"Wrote {len(plots)} plots to {PLOTS_DIR / 'plots.geojson'} (seed {args.seed}).")


if __name__ == "__main__":
    main()
