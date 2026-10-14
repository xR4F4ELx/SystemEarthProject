"""Checks the compact store loses nothing the decision needs, and the SQL engine matches the raw baseline.

The engine tests read the store built by the demo flow (encode_layers build + events, index_plots).
"""

import datetime as dt
import sys
from pathlib import Path

import duckdb
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from affine import Affine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import baseline as bl  # noqa: E402
import compact  # noqa: E402
import engine  # noqa: E402
from encode_layers import extract_events  # noqa: E402
from layers import PLOTS_DIR, load_forest_2020, load_hansen_lossyear, load_radd  # noqa: E402

STORE_BUILT = compact.MANIFEST_PATH.exists() and compact.PLOT_INDEX_PATH.exists()
needs_store = pytest.mark.skipif(not STORE_BUILT, reason="compact store not built; run the demo flow first")


def epoch_days(date):
    return (date - dt.date(1970, 1, 1)).days


def test_pack_unpack_round_trip():
    forest = np.array([True, True, False, True])
    first_date = np.array([0, epoch_days(dt.date(2021, 1, 1)), 0, epoch_days(dt.date(2032, 3, 18))], np.int32)
    sources = np.array([0, 1, 0, 7], np.uint8)
    decoded = compact.unpack(compact.pack(forest, first_date, sources))
    for got, expected in zip(decoded, (forest, first_date, sources)):
        assert (got == expected).all()


@pytest.mark.parametrize("date", [dt.date(2020, 12, 31), dt.date(2032, 3, 19)])
def test_pack_refuses_dates_it_cannot_encode(date):
    with pytest.raises(ValueError):
        compact.pack(np.array([True]), np.array([epoch_days(date)], np.int32), np.array([1], np.uint8))


def test_read_cells_matches_a_full_read(tmp_path):
    values = np.random.default_rng(0).integers(0, 2**16, size=(700, 900), dtype=np.uint16)
    path = tmp_path / "base.tif"
    compact.write_cog(values, Affine(1e-4, 0, 0, 0, -1e-4, 0), path, blocksize=256)
    rows = np.random.default_rng(1).integers(0, 700, 500)
    cols = np.random.default_rng(2).integers(0, 900, 500)
    got, bytes_read, tiles = compact.read_cells(path, rows, cols)
    assert (got == values[rows, cols]).all()
    assert bytes_read > 0 and 0 < tiles <= 12


def test_least_ignores_nulls_in_duckdb():
    # the event merge relies on this
    assert duckdb.sql("SELECT least(NULL::DATE, DATE '2022-01-01')").fetchone()[0] == dt.date(2022, 1, 1)


def test_events_on_top_of_held_back_state_reproduce_full_data():
    forest, grid = load_forest_2020()
    lossyear = load_hansen_lossyear(grid)
    alert, radd_date = load_radd(grid)
    before_date, before_src = bl.raw_disturbance(forest, lossyear, alert, radd_date, 24, 26181)
    full_date, full_src = bl.raw_disturbance(forest, lossyear, alert, radd_date)

    events = extract_events(26181, 24).to_pandas()
    days = (pd.to_datetime(events["date"]) - pd.Timestamp("1970-01-01")).dt.days.to_numpy()
    cells = events["cell_id"].to_numpy(np.int64)
    merged_date, merged_src = before_date.ravel().copy(), before_src.ravel().copy()
    for cell, day, source in zip(cells, days, events["source"].to_numpy()):
        merged_date[cell] = day if merged_date[cell] == 0 else min(merged_date[cell], day)
        merged_src[cell] |= source
    assert (merged_date == full_date.ravel()).all()
    assert (merged_src == full_src.ravel()).all()


@needs_store
def test_engine_matches_the_raw_baseline_on_every_field():
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    state = compact.load_manifest()["state"]
    raw, _ = bl.run(plots, state["hansen_max_year"] - 2000, state["radd_until_yyddd"])
    sql, _ = engine.check()
    raw = raw.set_index("plot_id").sort_index()
    sql = sql.set_index("plot_id").sort_index()[raw.columns]
    pd.testing.assert_frame_equal(raw, sql, check_dtype=False)


@needs_store
def test_update_rechecks_exactly_the_plots_the_batch_can_change():
    batches = compact.load_manifest()["event_batches"]
    if not batches:
        pytest.skip("no event batch published")
    affected = set(engine.affected_plots(compact.STORE_DIR / batches[-1]["path"]))
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    since = batches[-1]["since"]
    before, _ = bl.run(plots, since["hansen_max_year"] - 2000, since["radd_until_yyddd"])
    after, _ = bl.run(plots)
    changed = before.drop(columns="manifest_id").ne(after.drop(columns="manifest_id")).any(axis=1)
    assert set(after.loc[changed, "plot_id"]) <= affected
