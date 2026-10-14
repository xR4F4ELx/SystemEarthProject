# Evaluation (step 6)

14 Oct 2026. Numbers from `scripts/evaluate.py` → `data/results/evaluation.json` and `scripts/ee_benchmark.py` → `data/results/ee_benchmark.json`. AOI 2,756 km², 500 stratified synthetic plots, manifest `aoi-ci_tai_soubre-2026-10-13`. Measured on an M1 MacBook Air (local files) and Earth Engine (noncommercial project `systemearthproject`).

## Headline

| Rubric | Result |
|---|---|
| Storage | **140 kB** for all decision layers vs **6.05 MB** raw compressed (**43×**); about 50 bytes per km² |
| Query | One plot: **32 ms, 1 tile, 0.9 kB read** vs **0.51 s** on Earth Engine (median of 20). 500 plots: **0.34 s** vs **7.9 s** on Earth Engine (batched) |
| Update | Re-checks **39 of 500 plots** (the exact set that can change) in 0.11 s vs re-checking all 500 |
| Fidelity | **100% decision fidelity** in every stratum, before and after the update; every output field identical; area difference 0 ha; **100% pixel fidelity** on 32.4 M cells |

## 1. Storage

| | Bytes | Notes |
|---|---|---|
| Raw GFC2020 | 418,786 | 32.4 MB uncompressed |
| Raw Hansen (4 bands) | 4,244,090 | 14.4 MB uncompressed; `treecover2000` dominates and is dropped |
| Raw RADD (2 bands) | 959,429 | 223 MB uncompressed (int32 as exported) |
| Raw WDPA | 431,169 | Full park outlines, all attributes |
| **Raw total** | **6,053,474** | ≈ 270 MB uncompressed |
| Base raster (uint16 COG) | 95,784 | Forest bit + first date + sources per cell |
| Event batch (3 months RADD + Hansen 2025) | 22,670 | 3,989 events |
| Protected areas (national, clipped) | 18,751 | |
| Manifest | ≈ 2,600 | |
| **Compact layers total** | **139,800** | **43× smaller** than raw compressed |
| Plot index (RLE) + PA overlaps | 73,765 | ≈ 148 bytes per plot; scales with plots, not area |

- Compare against **raw compressed** (43×). The 1,935× ratio against uncompressed is inflated by RADD being exported as int32; don't headline it.
- Format choices were measured: ZSTD without predictor 95.8 kB; with predictor 110.8 kB; DEFLATE 191 kB; LZW 370 kB. Run-length plot index 70 kB vs 977 kB per-cell.

## 2. Fidelity

| Comparison | Decision fidelity | Field fidelity | Max area difference |
|---|---|---|---|
| Before update (held-back data) | 100% | 100% on every field | 0 ha |
| After incremental update | 100% | 100% on every field | 0 ha |
| By stratum (after) | 100% in all five strata | | |
| Pixel fidelity (base + events vs raw), 32,417,402 cells | forest 100%, date 100%, sources 100% | | |

What 100% does and does not prove: both pipelines share the spec's per-cell rule, the pixel-centre rule and the protected-area overlay. The 100% verifies independently that the encoding loses nothing (packing, compression, tiles), that the event log reproduces the full data, that the run-length index is exact, and that the rules re-implemented in SQL match the Python rules. It says nothing about whether GFC2020 or RADD are right on the ground.

## 3. Query performance

| Path | One plot | 500 plots | Update with 39 affected plots |
|---|---|---|---|
| **Earth Engine, one request per plot** (SystemEarth's path) | 0.51 s median (0.40–6.9 s); 0.17 EECU-s | ≈ 4 min if sequential (estimate) | all plots re-checked |
| **Earth Engine, one batched request** | — | 7.9 s; 17.9 EECU-s (0.036 per plot) | all plots re-checked |
| Raw layers, local (our baseline) | — | 1.85 s to load 5.6 MB of rasters, then 0.17 s | all plots |
| **Compact store, local** | **32 ms**, 1 tile, 0.9 kB read | **0.34 s**, 112 tiles, 86 kB read | **0.11 s**, 39 plots, 30 kB read |

- Earth Engine answers the same question: c2 identical on all 500 plots; disturbed-cell counts identical on 99.4%; forest-cell counts identical on 81% (Earth Engine rasterises polygon edges slightly differently).
- The compact single-plot time is mostly fixed overhead (opening DuckDB, reading the manifest); a long-running service would be faster.
- Local raw evaluation is fast once loaded (0.35 ms per plot). The raw approach loses on what it must load (whole rasters), not on per-plot compute.

**Why updates touch so few plots:** events can only occur on cells that were forest in 2020. In the landscape (5,000 random 2 ha plots, `docs/business-case.md`) only 1.3% of plots on unprotected land touch any 2020 forest, so roughly 99% of ordinary cocoa plots can never be changed by a deforestation update. Today SystemEarth's users decide manually or by schedule which past lots to reanalyse (PM, 14 Oct); the affected-plot query tells them exactly which lots can have changed, so reanalysis is targeted instead of guessed. Because events carry dates, a lot can also be checked only against events before its harvest date, which is the case the PM described.

## 4. Update vs rebuild

| | Incremental (our design) | Rebuild |
|---|---|---|
| Write | one 22.7 kB event file | re-encode the base: 3.9 s, 101 kB rewritten |
| Re-check | 39 plots, 0.11 s | all 500 plots, 0.31 s |

At this size both are cheap; the point is the scaling: rebuild cost grows with the area and the number of plots, incremental cost with the amount of change. Periodically folding events into a fresh base (e.g. quarterly) keeps the event log short.

## 5. Threshold sensitivity

Final-status counts across the 3-cell threshold and the 1% edge rule (default 3 cells, 1%: 248 Compliant / 168 Non-compliant / 84 Needs review):

| Min disturbed cells | Edge 0.5% | Edge 1% | Edge 2% |
|---|---|---|---|
| 1 | 8 plots change | 4 | 5 |
| 3 | 4 | **default** | 1 |
| 5 | 7 | 3 | 4 |
| 10 | 15 | 11 | 12 |

**Compliant stays at 248 in every setting.** The thresholds only move plots between Non-compliant and Needs review: they decide how much goes to a human, never whether a plot is cleared. That makes them safe to tune per customer.

## 6. Running-cost budget

Scale assumption: all of Côte d'Ivoire (322,463 km²), 1 million plots, weekly RADD updates (≈ 4.3 a month). Linear scaling by area is an overestimate for the layers (the AOI is 36% forest because of Taï; the country is much less).

**Our design**

| Item | Estimate | Basis |
|---|---|---|
| Decision layers | ≈ 16 MB | 50.7 bytes/km² × 322,463 km² |
| Plot index | ≈ 148 MB | 148 bytes × 1 M plots |
| Event log | ≈ 3 MB per quarter | AOI batch × area ratio |
| Object storage | **< $0.01 / month** | ≈ 0.17 GB at ≈ $0.02/GB-month (Standard storage; approximate, verify) |
| Full re-check of 1 M plots | ≈ 11 CPU-minutes | 0.67 ms per plot measured |
| Incremental re-checks | a few % of plots per update | only plots with 2020 forest can be affected |
| Compute | **< $1 / month** | small batch job at typical vCPU-hour prices (approximate, verify) |
| Extraction from source per release | **not yet measured** | one Earth Engine batch export per release, or direct download from the publishers (Hansen, GFC2020 are public files). Check EECU in the Earth Engine Tasks tab |

**SystemEarth today (from our Earth Engine measurements)**

Per the PM (14 Oct): rasters stored in Google Cloud Storage and served via GeoServer, vectors in PostGIS, analysis on Earth Engine under Enterprise Basic pricing; past lots are reanalysed manually or on a schedule chosen by the user, not automatically.

Earth Engine **Enterprise Basic** (cloud.google.com/earth-engine/pricing, read 14 Oct 2026): **$500/month** platform fee, including 100 batch and 33 online EECU-hours; beyond that **$0.40 per EECU-hour** (online and batch, first 10,000 hours/month; $0.28 above). Limit: **20 concurrent high-volume API requests per project**.

| Pattern | EECU per reanalysis of 1 M plots | Usage cost per reanalysis | Wall time per reanalysis |
|---|---|---|---|
| One request per plot | ≈ 47 EECU-hours (0.17 EECU-s per plot) | ≈ $19 gross; ≈ $6 after the 33 online credit-hours | ≈ 7 hours at the 20-request cap (0.5 s each) |
| Batched requests | ≈ 10 EECU-hours (0.036 EECU-s per plot) | ≈ $4 gross; inside the monthly credit | depends on batch sizes and quotas |
| **Our compact store** | none | ≈ $0 (local compute) | **≈ 11 minutes** single-threaded (0.67 ms per plot) |

Scenario, weekly reanalysis of 1 M plots one request at a time: ≈ 204 online EECU-hours/month → (204 − 33) × $0.40 ≈ **$68/month usage on top of the $500 fee**. Real volume depends on how often users schedule reanalysis, which we don't know.

**Honest reading (changes the cost pitch):** Earth Engine compute per check is cheap: about **$19 per million single-plot checks** (0.17 EECU-s × $0.40/EECU-hour). Storage is cheap on both sides too. The real costs of the current path are:

1. **The fixed $500/month ($6,000/year) platform fee.** Our design only needs Earth Engine to extract a release, and Hansen and GFC2020 are also downloadable directly, so the check path could drop Earth Engine entirely. Whether SystemEarth can drop the plan depends on its other Earth Engine uses (unknown).
2. **Throughput and latency.** 0.5 s per request and a 20-request concurrency cap mean ≈ 7 hours to reanalyse a million plots, against ≈ 11 minutes locally. That gap matters for interactive reanalysis and for scheduled runs over a growing plot base.

Do not pitch "big EECU savings": at measured rates they are tens of dollars a month. Pitch speed, no concurrency cap, targeted reanalysis, and the option to take Earth Engine out of the check path. The total infrastructure for our design is small enough that engineering time, not cloud bills, is the real cost.

## Caveats a judge will raise

| Challenge | Answer |
|---|---|
| "Your area is small." | Ratios and per-km² / per-plot figures are reported so it scales; national numbers are labelled estimates |
| "Synthetic plots." | Stratified to hit every rule path; landscape rates come from 5,000 random plots, not these 500 |
| "Earth Engine timings depend on load." | Median of 20 single requests, one batch run; range 0.40–6.9 s reported |
| "Prices unverified." | Infrastructure prices are approximate and marked; the conclusion holds even if they are 10× off |
| "Retracted RADD alerts?" | Not handled in the demo; needs retract events (slide) |
