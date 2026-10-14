"""Checks that the synthetic plots match SystemEarth's schema and the stratum design (CLAUDE.md)."""

import sys
from pathlib import Path

import numpy as np
import pytest
from affine import Affine
from shapely.geometry import box

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import generate_plots as gp  # noqa: E402
from layers import (  # noqa: E402
    AOI_BOUNDS,
    Grid,
    geodesic_area_ha,
    load_forest_2020,
    load_protected_areas,
    pa_class,
    point_footprint,
    resample_to_grid,
)

CUSTOMER_COLUMNS = ["ProducerName", "ProducerCountry", "ProductionPlace", "Area", "geometry"]


@pytest.fixture(scope="module")
def generated():
    plots, strata = gp.generate(seed=42)
    footprints = [
        point_footprint(g.x, g.y, a) if g.geom_type == "Point" else g for g, a in zip(plots.geometry, plots["Area"])
    ]
    return plots, strata, footprints


@pytest.fixture(scope="module")
def landscape():
    forest, grid = load_forest_2020()
    return gp.build_landscape(grid, forest, load_protected_areas())


def test_plots_use_only_the_customer_geojson_schema(generated):
    plots, _, _ = generated
    assert list(plots.columns) == CUSTOMER_COLUMNS
    assert set(plots["ProducerCountry"]) == {"CI"}
    assert plots["ProductionPlace"].is_unique


def test_stratum_counts_match_the_design(generated):
    _, strata, _ = generated
    assert strata["stratum"].value_counts().to_dict() == gp.STRATUM_COUNTS


def test_points_are_only_used_for_plots_up_to_4_ha(generated):
    plots, strata, _ = generated
    points = plots[plots.geom_type == "Point"]
    assert (points["Area"] < gp.POINT_MAX_AREA_HA).all()
    assert 0.2 < len(points) / len(plots) < 0.4
    assert (strata["geometry_type"] == np.where(plots.geom_type == "Point", "point", "polygon")).all()


def test_areas_are_smallholder_sized_and_match_polygon_geometry(generated):
    plots, _, _ = generated
    assert plots["Area"].between(gp.MIN_AREA_HA, gp.MAX_AREA_HA).all()
    polygons = plots[plots.geom_type == "Polygon"]
    for geom, area in zip(polygons.geometry, polygons["Area"]):
        assert geodesic_area_ha(geom) == pytest.approx(area, abs=0.01)


def test_plots_do_not_overlap_and_stay_inside_the_aoi(generated):
    _, _, footprints = generated
    inner_aoi = box(*gp.shrink(AOI_BOUNDS, gp.AOI_MARGIN_DEG))
    assert all(inner_aoi.contains(f) for f in footprints)
    for i, a in enumerate(footprints):
        assert not any(a.intersects(b) for b in footprints[i + 1 :])


def test_every_plot_satisfies_its_stratum_condition(generated, landscape):
    _, strata, footprints = generated
    for stratum, footprint in zip(strata["stratum"], footprints):
        assert gp.fits_stratum(stratum, footprint, landscape), stratum


def test_same_seed_gives_identical_plots(generated):
    plots, _, _ = generated
    again, _ = gp.generate(seed=42)
    assert plots.geometry.geom_equals(again.geometry).all()
    assert (plots["Area"] == again["Area"]).all()


def test_point_footprint_has_the_declared_area():
    footprint = point_footprint(-6.8, 5.8, 2.5)
    assert geodesic_area_ha(footprint) == pytest.approx(2.5, rel=0.01)


@pytest.mark.parametrize(
    ("desig_type", "iucn", "expected"),
    [
        ("National", "II", "strict"),
        ("National", "IV", "strict"),
        ("National", "Not Reported", "restricted"),
        ("International", "Not Applicable", "excluded_international"),
    ],
)
def test_pa_class_follows_the_spec(desig_type, iucn, expected):
    assert pa_class(desig_type, iucn) == expected


def test_resample_takes_the_source_value_at_each_cell_centre(tmp_path):
    import rasterio

    source = np.array([[1, 2], [3, 4]], dtype="uint8")
    path = tmp_path / "src.tif"
    profile = dict(driver="GTiff", width=2, height=2, count=1, dtype="uint8", crs="EPSG:4326",
                   transform=Affine(1.0, 0, 0, 0, -1.0, 2.0))
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(source, 1)
        dst.set_band_description(1, "value")

    grid = Grid(Affine(0.5, 0, 0, 0, -0.5, 2.0), height=4, width=4)  # each source cell becomes 2 x 2
    expected = np.repeat(np.repeat(source, 2, axis=0), 2, axis=1)
    assert (resample_to_grid(path, "value", grid) == expected).all()
