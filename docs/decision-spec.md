# EUDR Plot Decision Spec – Côte d'Ivoire Cocoa

13 Oct 2026 · Rafael. Updated 14 Oct 2026 with corrections from the exported data (grids, RADD encoding, d2 scope, geometry cleaning). This file is the working copy; the original Claude Doc (https://claude.ai/code/artifact/2cfeab3f-721f-4246-aeb0-e8c212a2f5ca) is now out of date.

## Purpose

This spec is the contract between the raw-layer pipeline and the compact pipeline: both must implement exactly these rules, and fidelity means they produce the same outputs.

It fixes every choice that can change a verdict (grid, pixel rule, dates, thresholds) before any encoding work starts. If a rule changes, both pipelines change together and the evaluation is re-run.

## Scope

Cocoa plots in south-west Côte d'Ivoire, judged against the EUDR cut-off of 31 December 2020.

| Item | Value |
| --- | --- |
| Commodity | Cocoa |
| Area of interest | About 50 × 50 km, 6.6–7.1°W, 5.6–6.05°N (eastern edge of Taï National Park towards Soubré); to confirm on a map |
| Cut-off date | 31 December 2020 (EUDR Article 2) |
| Plots | Synthetic polygons and points with realistic smallholder sizes, some placed on forest edges and inside protected areas |
| Outputs | Deforestation result (c2), strict protected-area overlap (d1), restricted-area overlap (d2), plus a needs-review flag |

Out of scope: forest degradation (standard layers cannot detect it), the full legality check (land tenure, labour, tax), live data updates (simulated once), and a user interface.

## Input layers

Four sources drive the three outputs; every pixel or record dated before the cut-off is dropped, except the 2020 forest baseline itself.

| Layer | Earth Engine ID | Native resolution | Used for | Keep / simplify / drop |
| --- | --- | --- | --- | --- |
| [JRC GFC2020 v4](https://developers.google.com/earth-engine/datasets/catalog/JRC_GFC2020_V4) | `JRC/GFC2020/V4`, band `Map` (1 = forest, otherwise masked) | 1/12000° (≈ 9.2 m; cell ≈ 85 m² at 5.8°N) | Forest on 31 Dec 2020 | Keep as a 1-bit mask |
| [Hansen Global Forest Change v1.13](https://developers.google.com/earth-engine/datasets/catalog/UMD_hansen_global_forest_change_2025_v1_13) | `UMD/hansen/global_forest_change_2025_v1_13` | 1/4000° (≈ 28 m), exactly 3 × 3 GFC2020 cells, aligned | Loss year 2021–2025 (`lossyear` 21–25) | Simplify to loss year, only inside 2020 forest; drop `treecover2000`, loss years 1–20, imagery bands |
| RADD alerts (Wageningen University) | `projects/radar-wur/raddalert/v1`, image `africa_20261007`; `Alert`: 2 = low, 3 = high confidence; `Date`: YYDDD (e.g. 21001 = 1 Jan 2021) | 1/11132° (≈ 10 m) on its own grid, **not aligned** with GFC2020 | Alert date and confidence after 31 Dec 2020 | Simplify to first post-cut-off date + confidence, only inside 2020 forest |
| WDPA protected areas | `WCMC/WDPA/current/polygons` | Vector | Strict (IUCN I–IV) and other nationally designated areas | Keep as clipped, cleaned polygons with dataset date recorded |

GLAD-L Landsat alerts are left out: they come from the same Landsat source as Hansen loss, and RADD already gives 10 m alerts across this region. Classified forests (forêts classées) appear in WDPA (No. 62 and Nibi Hana in the AOI), so no separate national layer is needed.

## Processing conventions

All rasters share the GFC2020 grid (≈ 9.2 m cells), and a pixel belongs to a plot when its centre falls inside the polygon.

| Convention | Rule | Why |
| --- | --- | --- |
| Grid | EPSG:4326 snapped to the GFC2020 grid; Hansen resampled by nearest neighbour (each Hansen pixel becomes exactly 3 × 3 cells) | Exact pixel-to-pixel comparison; nearest neighbour never invents values |
| RADD resampling | RADD resampled to the GFC2020 grid by nearest neighbour: each GFC2020 cell takes the RADD value at its cell centre | RADD sits on its own grid with cells about 8% larger per side, so some RADD values fill two GFC2020 cells. Both pipelines must resample identically, or fidelity breaks for reasons unrelated to the encoding |
| Forest mask | GFC2020 non-forest is masked (no value) in the source; treated as 0 = not forest | No-data is not "unknown" here: GFC2020 only stores forest |
| Geometry cleaning | WDPA geometries are cleaned before overlay: polygon parts extracted from geometry collections, invalid rings repaired. Both pipelines use the same cleaned file | Both Taï park records arrive as mixed geometry collections |
| Pixel in plot | Pixel centre inside the polygon | Deterministic and standard; identical in both pipelines |
| Point plots | Plots given as a point are buffered to a circle of the declared area | EUDR allows points for plots up to 4 ha; a point alone covers no pixels |
| Areas | Pixel count × pixel area at the plot's latitude | 10 m in degrees is not exactly 100 m² away from the equator |
| Hansen dates | Loss year 2021 → 2021-01-01, and so on | Annual product has no day; earliest date in the year is the conservative choice |
| RADD dates | YYDDD converted to calendar date; only dates after 2020-12-31 kept. Dates come in 12-day steps (Sentinel-1 revisit) | Cut-off applies to the detection date |
| Disturbance date | Earliest post-cut-off date across Hansen and RADD | One date per pixel is all the decision needs |
| Minimum forest | No 0.5 ha minimum applied to GFC2020 pixels | GFC2020 already applies the forest definition; re-filtering would double-count |

## Decision rules

A plot is flagged for deforestation only where land that was forest in 2020 shows a disturbance after the cut-off; small or weak signals go to review instead of being decided automatically.

Definitions, per plot:

- **F** = pixels inside the plot that GFC2020 marks as forest.
- **D** = pixels in F with a disturbance date after 2020-12-31 (Hansen loss or RADD alert).
- **Strong signal** = at least one pixel in D comes from Hansen loss or a high-confidence RADD alert (`Alert` = 3).

**c2 Deforestation result**

1. D = 0 → *a. No indication of deforestation*.
2. D ≥ 3 cells (about 0.026 ha; cells ≈ 85 m²) and a strong signal → *b. Indication of deforestation*.
3. Otherwise (D is 1–2 pixels, or only low-confidence RADD) → *b. Indication of deforestation*, with review reason `small_or_weak_signal`.

**d1 Strict protected area**

1. Plot polygon intersects a WDPA area with IUCN category Ia, Ib, II, III or IV → *b. Overlap with strict PA*.
2. If that overlap is under 1% of the plot area, add review reason `edge_overlap` (likely GPS or boundary error).
3. Otherwise → *a. No overlap with strict PA*.

**d2 Restricted-use area**

1. Same test against all other WDPA areas with a **national** designation (`DESIG_TYPE` = National; IUCN V, VI, Not Reported, Not Assigned, Not Applicable), which includes the classified forests → *b. Overlap with RA*, with the same `edge_overlap` rule.
2. International designations (World Heritage, Ramsar, UNESCO biosphere reserves) are excluded: in this AOI the Taï World Heritage record lies inside the national park, so counting it would only double-count plots that d1 already rejects. Limitation: where an international designation extends beyond any national one, this rule would miss the extra area; such regions would need the international layer clipped to areas outside d1.
3. Otherwise → *a. No overlap with RA*.

**Final status**

1. c2 Indication with no review reason, or d1 Overlap with no review reason → *Non-compliant*.
2. Any review reason, or d2 Overlap → *Needs review*.
3. Otherwise → *Compliant*.

The 3-pixel threshold and the 1% edge rule are settings, not constants: the evaluation reports results at the default and at least one alternative so the trade-off between missed and false flags is visible.

Open comment (unresolved): should 1–2 disturbed pixels count as an indication (sent to review) or as no indication? Current choice leans towards catching them.

## Output schema

Each plot returns SystemEarth's three spatial columns unchanged, plus the evidence and data versions behind them.

| Field | Type | Example | Purpose |
| --- | --- | --- | --- |
| `plot_id` | text | CI-0042 | Join key |
| `c2_deforestation_spatial_analysis_result` | text | b. Indication of deforestation | Same values as SystemEarth's CSV |
| `d1_strict_protected_area_spatial_analysis_result` | text | a. No overlap with strict PA | Same values as SystemEarth's CSV |
| `d2_restricted_use_area_spatial_analysis_result` | text | a. No overlap with RA | Same values as SystemEarth's CSV |
| `final_status` | text | Needs review | Compliant / Non-compliant / Needs review |
| `review_reasons` | list | small_or_weak_signal | Why a plot was not decided automatically |
| `forest_2020_ha` | number | 1.84 | Area of F |
| `disturbed_after_cutoff_ha` | number | 0.12 | Area of D |
| `first_disturbance_date` | date | 2022-03-14 | Earliest date in D |
| `disturbance_sources` | list | hansen, radd_high | Which layers fired |
| `pa_overlap_names` | list | Parc National de Taï | Which areas overlapped |
| `manifest_id` | text | aoi-ci-2026-10-13 | Links to the record of dataset IDs, versions and download dates used |

The manifest is what makes a verdict reproducible: re-running a plot against the same manifest must return the same row.

## Fidelity definition

The compact pipeline passes when every plot's outputs match the raw pipeline run on the same plots, with the same manifest.

| Metric | Definition | Target |
| --- | --- | --- |
| Decision fidelity | % of plots where c2, d1, d2 and final status all match the raw pipeline | 100% |
| Field fidelity | % match per output field, reported separately | 100% each |
| Area agreement | Mean absolute difference in `forest_2020_ha` and `disturbed_after_cutoff_ha` | 0 ha (same grid and rule) |
| Pixel fidelity | % of pixels in the area of interest where the decoded forest bit and disturbance date equal the raw-derived values | 100% for kept information |
| Dropped information | Bits per pixel kept vs the raw stack, listed by layer | Reported, not scored |

Any mismatch is listed plot by plot with its cause. A lossy option, if tested, is reported with its fidelity cost rather than hidden.

## Open questions and judge challenges

- [x] Confirm the GFC2020 v4 band name and forest value → `Map`, 1 = forest, non-forest masked (13 Oct dry run).
- [x] Confirm the RADD asset path, Africa image, and its alert and date encoding → `projects/radar-wur/raddalert/v1`, image `africa_20261007`, `Alert` 2/3, `Date` YYDDD.
- [x] Check whether classified forests near Taï appear in WDPA → yes: Classified Forest No. 62 and Nibi Hana.

| Likely challenge | Answer |
| --- | --- |
| Why drop all data before 2021? | The cut-off is fixed in Article 2; earlier loss cannot change a c2 result. The 2020 baseline keeps the one fact needed from before. |
| Isn't GFC2020 wrong on shade cocoa? | Yes, it can be: its forest commission error was 18% in v2. That is why it is a risk signal, and disagreements go to review rather than auto-reject. |
| Why 3 pixels? | It is a setting. Results are shown at more than one value so the cost of missed versus false flags is visible. |
| Why resample Hansen to 10 m? | Exact pixel agreement with GFC2020 and RADD. It stores no new information and compresses to almost nothing after masking. |

Sources: [GFC2020 v4](https://developers.google.com/earth-engine/datasets/catalog/JRC_GFC2020_V4), [Hansen v1.13](https://developers.google.com/earth-engine/datasets/catalog/UMD_hansen_global_forest_change_2025_v1_13), [GFC2020 accuracy assessment](https://publications.cirad.fr/une_notice.php?dk=612881), SystemEarth sample CSV (`data/sample/`).
