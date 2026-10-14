"""Checks the raw baseline implements docs/decision-spec.md rule by rule."""

import datetime as dt
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import baseline as bl  # noqa: E402
from layers import PLOTS_DIR  # noqa: E402

NO_REASON = None


def epoch_days(year, month, day):
    return (dt.date(year, month, day) - dt.date(1970, 1, 1)).days


@pytest.mark.parametrize(
    ("n_disturbed", "strong", "expected"),
    [
        (0, False, (bl.C2_NONE, NO_REASON)),
        (3, True, (bl.C2_INDICATION, NO_REASON)),
        (2, True, (bl.C2_INDICATION, bl.REASON_SMALL_OR_WEAK)),  # too small
        (50, False, (bl.C2_INDICATION, bl.REASON_SMALL_OR_WEAK)),  # only low-confidence RADD
    ],
)
def test_c2_follows_the_spec(n_disturbed, strong, expected):
    assert bl.c2_result(n_disturbed, strong) == expected


def test_c2_threshold_is_a_setting():
    assert bl.c2_result(2, True, min_cells=1) == (bl.C2_INDICATION, NO_REASON)


@pytest.mark.parametrize(
    ("share", "expected"),
    [
        (0.0, (bl.D2_NONE, NO_REASON)),
        (0.005, (bl.D2_OVERLAP, bl.REASON_EDGE_OVERLAP)),  # d2 edge case the synthetic plots lack
        (0.01, (bl.D2_OVERLAP, NO_REASON)),
        (0.6, (bl.D2_OVERLAP, NO_REASON)),
    ],
)
def test_d2_overlap_and_edge_rule(share, expected):
    assert bl.pa_result(share, bl.D2_NONE, bl.D2_OVERLAP) == expected


def test_d1_small_overlap_goes_to_review():
    assert bl.pa_result(0.002, bl.D1_NONE, bl.D1_OVERLAP) == (bl.D1_OVERLAP, bl.REASON_EDGE_OVERLAP)


CLEAN_C2 = (bl.C2_NONE, NO_REASON)
CLEAN_D1 = (bl.D1_NONE, NO_REASON)
CLEAN_D2 = (bl.D2_NONE, NO_REASON)


@pytest.mark.parametrize(
    ("c2", "d1", "d2", "expected"),
    [
        (CLEAN_C2, CLEAN_D1, CLEAN_D2, bl.COMPLIANT),
        ((bl.C2_INDICATION, NO_REASON), CLEAN_D1, CLEAN_D2, bl.NON_COMPLIANT),
        (CLEAN_C2, (bl.D1_OVERLAP, NO_REASON), CLEAN_D2, bl.NON_COMPLIANT),
        ((bl.C2_INDICATION, bl.REASON_SMALL_OR_WEAK), CLEAN_D1, CLEAN_D2, bl.NEEDS_REVIEW),
        (CLEAN_C2, (bl.D1_OVERLAP, bl.REASON_EDGE_OVERLAP), CLEAN_D2, bl.NEEDS_REVIEW),
        (CLEAN_C2, CLEAN_D1, (bl.D2_OVERLAP, NO_REASON), bl.NEEDS_REVIEW),
        # a clean deforestation indication outranks a d2 overlap
        ((bl.C2_INDICATION, NO_REASON), CLEAN_D1, (bl.D2_OVERLAP, NO_REASON), bl.NON_COMPLIANT),
    ],
)
def test_final_status_follows_the_spec(c2, d1, d2, expected):
    assert bl.final_status(c2, d1, d2) == expected


def tiny_layers():
    """Four cells: forest with Hansen 2022, forest with RADD + Hansen, non-forest with loss, forest pre-cut-off."""
    forest = np.array([[True, True, False, True]])
    lossyear = np.array([[22, 23, 22, 19]], dtype=np.uint8)
    alert = np.array([[0, 2, 3, 3]], dtype=np.int32)
    radd_date = np.array([[0, 22100, 22050, 20100]], dtype=np.int32)
    return forest, lossyear, alert, radd_date


def test_raw_disturbance_takes_the_earliest_post_cutoff_date_inside_forest_only():
    first_date, sources = bl.raw_disturbance(*tiny_layers())
    assert first_date[0, 0] == epoch_days(2022, 1, 1)  # Hansen year -> 1 Jan
    assert first_date[0, 1] == epoch_days(2022, 4, 10)  # RADD day 100 of 2022 beats Hansen 2023
    assert first_date[0, 2] == bl.NO_DATE  # not forest in 2020
    assert first_date[0, 3] == bl.NO_DATE  # loss and alert both before the cut-off
    assert sources[0, 1] == bl.SOURCE_HANSEN | bl.SOURCE_RADD_LOW


def test_raw_disturbance_respects_held_back_data():
    first_date, _ = bl.raw_disturbance(*tiny_layers(), hansen_max_year=22, radd_until=22099)
    assert first_date[0, 0] == epoch_days(2022, 1, 1)
    assert first_date[0, 1] == bl.NO_DATE  # Hansen 2023 and RADD day 100 are both held back


def test_date_to_yyddd():
    assert bl.date_to_yyddd(dt.date(2026, 6, 30)) == 26181


@pytest.fixture(scope="module")
def verdicts():
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    return plots, bl.run(plots)[0], bl.run(plots, hansen_max_year=24, radd_until=26181)[0]


def test_every_plot_gets_one_verdict_with_consistent_evidence(verdicts):
    plots, full, _ = verdicts
    assert list(full["plot_id"]) == list(plots["ProductionPlace"])
    assert (full["n_cells"] > 0).all()
    assert (full["n_disturbed_cells"] <= full["n_forest_cells"]).all()
    indicated = full["c2_deforestation_spatial_analysis_result"] == bl.C2_INDICATION
    assert ((full["n_disturbed_cells"] > 0) == indicated).all()
    assert (full.loc[indicated, "first_disturbance_date"] > "2020-12-31").all()


def test_holding_back_data_never_adds_disturbance(verdicts):
    _, full, before = verdicts
    assert (before["n_disturbed_cells"] <= full["n_disturbed_cells"]).all()
    assert before["manifest_id"].iloc[0].endswith("|radd<=26181|hansen<=2024")


def test_baseline_is_deterministic(verdicts):
    plots, full, _ = verdicts
    assert bl.run(plots)[0].equals(full)
