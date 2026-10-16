# Submission memo — Keep the decision, not the data

EUDR verification for Ivory Coast cocoa · Rafael · SystemEarth hackathon, 17 Oct 2026

## What I built

A compact data store and rule engine that checks cocoa farm plots against the EU Deforestation Regulation (EUDR): was the land deforested after 31 December 2020, and does the plot overlap a protected or restricted area? Instead of keeping and querying full satellite layers, it keeps only the facts a verdict needs for each 10 m cell. A plot check takes milliseconds without Earth Engine, every new satellite release names exactly which plots it can change, and the verdicts match a pipeline run on the original layers on every plot. It comes with a demo console that runs each step live.

## How it works

- **Data:** four public layers for a 2,756 km² area on the edge of Taï National Park (south-west Ivory Coast), exported from Google Earth Engine: JRC GFC2020 v4 (forest on 31 Dec 2020), Hansen Global Forest Change v1.13 (yearly tree-cover loss), RADD radar deforestation alerts (Wageningen University) and WDPA protected areas. 500 synthetic farm plots in SystemEarth's GeoJSON schema, spread over five landscape types (forest fragments, cocoa land, classified forests, park edge, park interior) so every decision path is tested; about a third are GPS points, as EUDR allows under 4 ha.
- **Rules first:** the decision rules (deforestation c2, strict protected area d1, restricted area d2, final status) are written as a fixed spec, in SystemEarth's own output schema. Two pipelines implement it: one directly on the raw layers (the ground truth) and one on the compact store.
- **Compact store:** one 16-bit number per cell (forest in 2020, which source detected a disturbance, and the first disturbance date) in a compressed Cloud-Optimised GeoTIFF; one small event file per new data release instead of re-saving layers; a run-length plot index, so each plot's geometry is processed once at upload; a manifest with dataset versions and file hashes.
- **Checks:** SQL in DuckDB over these files, with no server and no geometry at query time. After a release, the event file is joined with the plot index to find and re-check only the affected plots.
- **Tools:** Python 3.12, rasterio/GDAL, GeoPandas/Shapely, Parquet, DuckDB, Google Earth Engine API (extraction and benchmark), Streamlit (demo console), pytest (53 tests).
- **AI:** the compliance decision itself uses no machine learning; the rules are deterministic and auditable. I used Claude Code (Anthropic) as an assistant for research, analysis, code and documentation, and Claude Design to draft the slides. *[Edit to match exactly how you used them.]*

## Results

- **Storage:** 140 kB for all decision layers vs 6.05 MB of raw layers as exported and compressed: **43× smaller**, about 50 bytes per km² (≈ 16 MB for all of Ivory Coast, estimated).
- **Query speed:** one plot in **32 ms** vs **0.51 s** on Earth Engine (≈ 16× faster); 500 plots in 0.34 s vs 7.9 s batched on Earth Engine, with the same deforestation result on all 500 plots.
- **Updates:** after a release of new alerts and the Hansen 2025 loss year, **39 of 500 plots** were re-checked in 0.11 s, exactly the set that can change; 12 verdicts changed.
- **Fidelity:** **100% decision fidelity** (all 500 plots, all five landscape types, before and after the update), every output field identical, and **100% pixel fidelity** on 32.4 million cells. Across 12 threshold settings no Compliant plot ever changed; only the split between Non-compliant and Needs review moved.
- **Why the 2020 baseline matters:** 99.5% of post-2020 tree-cover loss outside protected areas fell on land that was not forest in 2020. A tree-cover-loss-only check flags 22.9% of random 2 ha farms; gating by the 2020 forest map flags 0.12%.
- **Running cost:** reanalysing 1 million plots takes ≈ 11 minutes on one core vs ≈ 7 hours on Earth Engine one request per plot (≈ 4.4 hours batched); infrastructure under $1/month. Earth Engine compute per check is cheap either way, so the value is speed and headroom (no 20-request cap, no need for a larger plan as plot volume grows), not a smaller bill today.

## Limitations and next steps

- **Synthetic plots and one 50 km area.** No open set of real Ivorian farm polygons was available; national figures are extrapolated per km² and per plot. Next: run alongside SystemEarth's current checks on real lots and compare verdicts plot by plot.
- **100% fidelity means the store loses nothing, not that the satellite layers are right.** GFC2020 can map shade cocoa as forest. Next: add a cocoa map as a second opinion and send disagreements to review.
- **The store answers today's rules.** A new question (forest degradation, another cut-off) needs re-extraction from source; the manifest records what to re-fetch.
- **Not built yet:** retraction of withdrawn radar alerts, periodic folding of events into the base, harvest-date-aware checks, automatic triggers from plot uploads and releases, cloud deployment (Google Cloud Storage + Cloud Run), and an explicit error when a plot that isn't indexed is checked.
- **Costs are approximate** (Earth Engine prices read 14 Oct 2026); the cost of extracting a national release is not yet measured.
