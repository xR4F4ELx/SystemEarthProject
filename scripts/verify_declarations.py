"""
Verify supplier declarations against our verdicts (step 5): SystemEarth's real workflow, per the CTO.

The customer submits plots plus a due-diligence CSV; SystemEarth checks whether its own results match
what the supplier declared. Only c2 (deforestation) is compared. This is not a fidelity metric:
fidelity compares our compact store with the raw layers, this compares us with the supplier.

Input:  data/plots/supplier_declarations.csv (SystemEarth CSV columns), our verdicts CSV
Output: data/results/verification.csv, one row per plot with a reason for every mismatch
Run:
    python scripts/verify_declarations.py
"""

import argparse
from pathlib import Path

import pandas as pd

from baseline import C2_INDICATION, C2_NONE, RESULTS_DIR
from layers import PLOTS_DIR

REASONS = {
    "no_2020_forest_in_plot": "Supplier flagged tree cover loss, but the plot had no forest at the cut-off",
    "loss_not_on_2020_forest": "Supplier flagged loss on parts of the plot that were not forest at the cut-off",
    "radd_alert_outside_declared_layer": "We found a RADD radar alert; the supplier's declared layer has no alerts",
    "loss_after_declared_window": "We found loss after 2023; the supplier's declared layer stops at 2023",
}


def mismatch_reason(declared_c2, ours):
    """Why our c2 differs from the declaration ('' when they agree). `ours` is one verdict row."""
    our_c2 = ours["c2_deforestation_spatial_analysis_result"]
    if declared_c2 == our_c2:
        return ""
    if declared_c2 == C2_INDICATION and our_c2 == C2_NONE:
        return "no_2020_forest_in_plot" if ours["forest_2020_ha"] == 0 else "loss_not_on_2020_forest"
    if declared_c2 == C2_NONE and our_c2 == C2_INDICATION:
        if "hansen" not in ours["disturbance_sources"].split(";"):
            return "radd_alert_outside_declared_layer"
        return "loss_after_declared_window"
    raise ValueError(f"Unexpected c2 values: declared {declared_c2!r}, ours {our_c2!r}")


def verify(declarations, verdicts):
    merged = declarations.rename(columns={"a3_plot_id": "plot_id"}).merge(
        verdicts, on="plot_id", how="outer", indicator=True, validate="one_to_one",
        suffixes=("_declared", ""))
    unmatched = merged[merged["_merge"] != "both"]
    if len(unmatched):
        raise ValueError(f"Plots missing from declarations or verdicts: {unmatched['plot_id'].tolist()[:10]}")

    declared = merged["c2_deforestation_spatial_analysis_result_declared"]
    reasons = [mismatch_reason(d, row) for d, (_, row) in zip(declared, merged.iterrows())]
    return pd.DataFrame({
        "plot_id": merged["plot_id"],
        "declared_layer": merged["c1_indicate_deforestation_analysis_layer_used"],
        "declared_c2": declared,
        "our_c2": merged["c2_deforestation_spatial_analysis_result"],
        "our_final_status": merged["final_status"],
        "matches_declaration": [reason == "" for reason in reasons],
        "mismatch_reason": reasons,
        "forest_2020_ha": merged["forest_2020_ha"],
        "disturbed_after_cutoff_ha": merged["disturbed_after_cutoff_ha"],
        "first_disturbance_date": merged["first_disturbance_date"],
        "disturbance_sources": merged["disturbance_sources"],
        "manifest_id": merged["manifest_id"],
    }).sort_values("plot_id").reset_index(drop=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--declarations", type=Path, default=PLOTS_DIR / "supplier_declarations.csv")
    parser.add_argument("--verdicts", type=Path, default=RESULTS_DIR / "compact_verdicts.csv")
    parser.add_argument("--out", type=Path, default=RESULTS_DIR / "verification.csv")
    args = parser.parse_args()

    result = verify(pd.read_csv(args.declarations, keep_default_na=False),
                    pd.read_csv(args.verdicts, keep_default_na=False))
    result.to_csv(args.out, index=False)
    print(f"Agreement with supplier on c2: {result['matches_declaration'].mean():.1%} of {len(result)} plots")
    print(pd.crosstab(result["declared_c2"], result["our_c2"]).to_string())
    for reason, count in result.loc[~result["matches_declaration"], "mismatch_reason"].value_counts().items():
        print(f"  {count:4d}  {reason}: {REASONS[reason]}")
    print(f"Wrote {args.out}")


if __name__ == "__main__":
    main()
