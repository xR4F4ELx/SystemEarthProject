#!/usr/bin/env bash
# Live demo, one step per Enter key: build the compact store on held-back data, check 500 plots,
# publish an update, re-check only affected plots, show what changed (table + map), verify supplier
# declarations, and prove fidelity with the test suite.
#
# Run from anywhere:   bash scripts/demo.sh
# Set DEMO_OPEN=true to skip opening the map image (e.g. when rehearsing over SSH).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=.venv/bin/python
OPEN_CMD=${DEMO_OPEN:-open}

step() {
  echo
  echo "=================================================================="
  echo "  $1"
  echo "=================================================================="
  read -r -p "  [Enter] to run " _
}

step "1/8  Build the compact store from layers held back to 30 Jun 2026"
$PY scripts/encode_layers.py build --radd-until 2026-06-30 --hansen-max-year 24

step "2/8  Index the 500 plots (cell runs + protected-area overlaps, once per upload)"
$PY scripts/index_plots.py

step "3/8  Check all 500 plots against the compact store"
$PY scripts/engine.py check --out data/results/compact_verdicts_before_update.csv

step "4/8  A new release arrives: 3 months of RADD alerts + Hansen 2025, as one event batch"
$PY scripts/encode_layers.py events --batch 2026-10-07

step "5/8  Re-check only the plots this release can affect"
$PY scripts/engine.py update --batch 2026-10-07 \
  --previous data/results/compact_verdicts_before_update.csv --out data/results/compact_verdicts.csv

step "6/8  Which plots changed status, and where"
$PY - <<'EOF'
import pandas as pd
before = pd.read_csv("data/results/compact_verdicts_before_update.csv", keep_default_na=False).set_index("plot_id")
after = pd.read_csv("data/results/compact_verdicts.csv", keep_default_na=False).set_index("plot_id")
changed = before["final_status"] != after["final_status"]
print(pd.DataFrame({"before": before.loc[changed, "final_status"], "after": after.loc[changed, "final_status"],
                    "first_disturbance": after.loc[changed, "first_disturbance_date"],
                    "sources": after.loc[changed, "disturbance_sources"]}).to_string())
print(f"\n{int(changed.sum())} of {len(after)} plots changed final status")
EOF
$PY scripts/make_map.py
$OPEN_CMD docs/figures/aoi_map.png

step "7/8  Verify the supplier's declarations against our verdicts"
$PY scripts/verify_declarations.py

step "8/8  Proof: compact store matches the raw-layer pipeline (tests)"
$PY -m pytest tests -q

echo
echo "Demo complete. Results: docs/evaluation.md"
