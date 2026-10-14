"""Checks the supplier-declaration verification (step 5)."""

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from baseline import C2_INDICATION, C2_NONE, run  # noqa: E402
from layers import PLOTS_DIR  # noqa: E402
from simulate_declarations import simulate  # noqa: E402
from verify_declarations import mismatch_reason, verify  # noqa: E402


def verdict(c2, forest_ha=0.0, sources=""):
    return {"c2_deforestation_spatial_analysis_result": c2, "forest_2020_ha": forest_ha,
            "disturbance_sources": sources}


@pytest.mark.parametrize(
    ("declared", "ours", "expected"),
    [
        (C2_NONE, verdict(C2_NONE), ""),
        (C2_INDICATION, verdict(C2_INDICATION, 1.0, "hansen"), ""),
        (C2_INDICATION, verdict(C2_NONE, 0.0), "no_2020_forest_in_plot"),
        (C2_INDICATION, verdict(C2_NONE, 0.4), "loss_not_on_2020_forest"),
        (C2_NONE, verdict(C2_INDICATION, 2.0, "radd_high;radd_low"), "radd_alert_outside_declared_layer"),
        (C2_NONE, verdict(C2_INDICATION, 2.0, "hansen"), "loss_after_declared_window"),
    ],
)
def test_mismatch_reason(declared, ours, expected):
    assert mismatch_reason(declared, ours) == expected


def test_verify_refuses_plots_missing_on_either_side():
    declarations = pd.DataFrame({"a3_plot_id": ["CI-0001"], "c1_indicate_deforestation_analysis_layer_used": ["x"],
                                 "c2_deforestation_spatial_analysis_result": [C2_NONE]})
    verdicts = pd.DataFrame({"plot_id": ["CI-0002"], "c2_deforestation_spatial_analysis_result": [C2_NONE]})
    with pytest.raises(ValueError, match="missing"):
        verify(declarations, verdicts)


@pytest.fixture(scope="module")
def verification():
    plots = gpd.read_file(PLOTS_DIR / "plots.geojson")
    return verify(simulate(plots), run(plots)[0])


def test_every_mismatch_has_a_reason_and_every_match_has_none(verification):
    assert (verification["matches_declaration"] == (verification["mismatch_reason"] == "")).all()
    assert len(verification) == 500


def test_supplier_misses_only_come_from_data_outside_their_declared_layer(verification):
    # Hansen 2021-2023 loss on 2020 forest is inside the supplier's layer, so they cannot miss it
    missed = verification[(verification["declared_c2"] == C2_NONE) & (verification["our_c2"] == C2_INDICATION)]
    assert set(missed["mismatch_reason"]) <= {"radd_alert_outside_declared_layer", "loss_after_declared_window"}
