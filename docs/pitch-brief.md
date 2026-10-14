# Pitch brief for Claude Design

Paste this whole file into Claude Design. All numbers come from `docs/evaluation.md` and `docs/business-case.md` (measured 13–14 Oct 2026); do not change or round them further.

## The brief

- **Event:** SystemEarth hackathon, "Keep the decision, not the data". Pitch on Saturday 17 October 2026.
- **Audience:** SystemEarth judges, including the CTO (Matthew Saw). Technical, geospatial-literate, sceptical. They reward reasoned trade-offs, not new algorithms.
- **Judged on:** business case + storage efficiency, query performance, result fidelity; bonus for a sound running-cost budget. Trade-offs must be explained for each of the three technical criteria.
- **Length:** 12 slides, 16:9, about 8 minutes plus Q&A. Speaker notes on every slide.
- **Look:** institutional/research, calm, data-forward. Suggested palette: dark forest green `#17332B`, warm off-white `#F7F5EF`, cocoa-orange accent `#C2652A`, one blue `#2F6FA3` for "ours vs theirs" contrasts (never colour alone: always label). Suggested type: a serif display face (e.g. Source Serif 4) + a clean sans for text (e.g. IBM Plex Sans). Big numbers, tables and simple diagrams over bullets. One idea per slide.

## Rules for claims (important)

- Say **"removes" flags**, never "proves false": no field data.
- The false-flag reduction comes from the **decision rule** (2020 forest baseline), not the storage design. Pitch the architecture on **query speed, incremental re-checks and running cost**.
- SystemEarth **already records dataset versions and a full audit trail**. Do not say they cannot reproduce verdicts.
- SystemEarth does **not** re-check every past lot automatically: users run manual or scheduled reanalysis (PM, 14 Oct). Do not say "they re-check everything".
- Source of truth for how SystemEarth works today: the **product manager's latest answers** (`docs/systemearth-current-setup.md`).
- Use **43×** for storage (vs raw compressed). Do not headline 1,935× (inflated by RADD exported as int32).
- 100% fidelity means the compact store **loses nothing** versus the raw layers. It does not mean the layers are right on the ground.
- Items marked *verify* below must be checked before the pitch or shown with the qualifier.

## Slides

### 1. Cover
- Title: **Keep the decision, not the data**
- Subtitle: EUDR verification for Côte d'Ivoire cocoa: a compact, query-fast store that reproduces every plot verdict
- Line: Rafael · SystemEarth hackathon · 17 Oct 2026
- Notes: One sentence: we built a store 43× smaller than the raw layers that answers plot checks about 16× faster than Earth Engine, re-checks only what changed, and matches the raw-layer verdicts on every plot.

### 2. The business case: why Côte d'Ivoire cocoa
- Big-number row: **~40%** of world cocoa (*verify*) · **59%** of Ivorian cocoa goes to the EU (EU factsheet 2023) · **30 Dec 2026** EUDR applies to large operators
- Standard-risk country (*verify*: Implementing Regulation (EU) 2025/1093): every plot needs a geolocated check
- Hardest case: very many small farms, tree crops that look like deforestation, farms inside protected areas
- Notes: SystemEarth's sample is Thai rubber, their current market. We chose an expansion market where the problem is hardest, keeping their output schema.

### 3. How SystemEarth works today
- Workflow: customer submits plots + due-diligence CSV → SystemEarth analyses the plots → compares with the supplier's declaration
- Stack (PM, 14 Oct): **vectors in PostGIS, rasters in Google Cloud Storage served via GeoServer, analysis compute on Earth Engine (commercial, Enterprise Basic)**
- Past lots: **manual or scheduled reanalysis**, chosen by the user; skipped e.g. when deforestation happened after the harvest
- Already strong: dataset versions and a full audit trail per plot and analysis
- Diagram: Supplier → GeoJSON + CSV → Earth Engine analysis over stored layers → compare → verdict; reanalysis on demand or schedule
- Notes: We kept what they already do well (audit trail, their output schema) and targeted three things: cost and speed per check, storage of full raster copies, and knowing which past lots a new release actually affects.

### 4. What the decision actually needs (layer analysis)
Table:

| Layer | Keep | Drop | Why |
|---|---|---|---|
| JRC GFC2020 v4 | 1 bit: forest on 31 Dec 2020 | — | The cut-off baseline (EUDR Art. 2) |
| Hansen GFC v1.13 | Loss year 2021–2025, only on 2020 forest | `treecover2000`, loss before 2021, imagery bands | Earlier loss cannot change the verdict |
| RADD alerts | First post-cut-off date + confidence, only on 2020 forest | Alerts before 2021 | Dates matter only after the cut-off |
| WDPA | National protected areas, clipped | International designations (inside the park here) | Avoid double counting |

- Notes: Everything is put on the GFC2020 grid (≈ 9.2 m cells, ≈ 85 m²). RADD sits on its own grid and is resampled by nearest neighbour; both pipelines do it identically.

### 5. The insight: tree cover loss is not deforestation
- Big number: **99.5%** of post-2020 Hansen loss outside protected areas is on land that was **not forest in 2020**
- Comparison bars, 5,000 random 2 ha plots on unprotected land: **22.9%** flagged by a Hansen-only check vs **0.12%** gated by the 2020 baseline
- Map idea (optional): AOI 6.6–7.1°W, 5.6–6.05°N; forest in 2020 is almost entirely Taï National Park
- Notes: This is the decision rule, not the storage. It explains why suppliers using Hansen alone send farms to field visits. The supplier in SystemEarth's sample flagged 5 of 17 plots and cleared them on site.

### 6. Architecture
Diagram (left to right): Public layers → **Encode once per release** → Compact store → **DuckDB rule engine** → Verdict + manifest ID. Plot upload → **Plot index** → Compact store.

Store contents (small cards):
- **Base raster**: uint16 per cell = forest bit + 3 source bits + 12-bit first-disturbance date; ZSTD COG, 512 px tiles
- **Event log**: one Parquet file per release (cell, date, source)
- **Plot index**: run-length encoded cells per plot + protected-area overlaps computed once at upload
- **Manifest**: dataset IDs, versions, data state, file hashes
- Notes: No geometry at query time: a plot is a list of cell runs. Earth Engine is used only to extract a release, not for every check.

### 7. Storage efficiency
| | Size |
|---|---|
| Raw layers (compressed, as exported) | 6.05 MB |
| **Compact decision store** | **140 kB (43× smaller)** |
| Per km² | ≈ 50 bytes |
| Plot index | ≈ 148 bytes per plot |

- Measured choices: ZSTD without predictor 96 kB (with predictor 111 kB, DEFLATE 191 kB, LZW 370 kB); run-length plot index 70 kB vs 977 kB per cell
- Trade-off: the store holds only what today's rules need. A new question (degradation, another cut-off) needs re-extraction from source; the manifest records exactly what to re-fetch.
- Notes: SystemEarth stores full raster copies in GCS today; the decision needs about 2% of those bytes. Storage is cheap in money either way; small storage is what makes fast, local checks possible.

### 8. Query performance
| Path | One plot | 500 plots |
|---|---|---|
| Earth Engine, one request per plot | 0.51 s median (0.40–6.9 s) | ≈ 4 min sequential (estimate) |
| Earth Engine, batched | — | 7.9 s |
| **Compact store** | **32 ms** (1 tile, 0.9 kB read) | **0.34 s** |

- Earth Engine answers the same question: c2 identical on all 500 plots
- Compute: 0.17 EECU-seconds per single Earth Engine request; 0.036 per plot batched
- Trade-off: the compact path needs an encode step per release (3.9 s here) and a plot index at upload.
- Notes: Most of the 32 ms is fixed overhead (opening DuckDB, reading the manifest); a long-running service would be faster.

### 9. Updates: re-check only what can change
- Demo: last 3 months of RADD + Hansen 2025 published as one event batch (3,989 events, 22.7 kB)
- **39 of 500 plots re-checked** (the exact set that can change) in 0.11 s; 12 final statuses change (9 Compliant → Non-compliant)
- Structural reason: events only happen on 2020-forest cells; **~99% of ordinary cocoa plots touch no 2020 forest**, so they can never be changed by a deforestation update
- Trade-off: low-confidence RADD alerts can be retracted later; that needs "retract" events (designed, not built). A quarterly fold-in rebuilds the base.
- Fits their workflow: today users choose manually or by schedule which past lots to reanalyse; we tell them exactly which lots a release can affect, and events carry dates, so a lot can be checked only against events before its harvest (the case the PM raised)
- Notes: Reanalysis becomes targeted instead of guessed.

### 10. Result fidelity
- Big number: **100% decision fidelity**: all 500 plots, all five strata, before and after the update
- **Every output field identical**; area difference 0 ha
- **100% pixel fidelity** on 32.4 M cells (forest bit, date, sources)
- Thresholds as settings: across 12 settings of the 3-cell and 1% rules, **Compliant never changes (248)**; plots only move between Non-compliant and Needs review
- Trade-off/honesty: both pipelines share the spec's per-cell rule; 100% proves the encoding, event log, index and SQL rules lose nothing, not that the satellite layers are right
- Notes: 500 synthetic plots stratified over forest fragments, cocoa land, classified forests, park boundary and park interior, so every rule path is exercised.

### 11. Verification and running cost
Left: supplier check (simulated with the sample supplier's method: any Hansen loss 2021–2023):
- 75.6% agreement on c2 (stratified plots)
- **76 supplier flags we clear** (69 plots had no forest in 2020) · **46 supplier misses** (32 RADD alerts, 14 loss after 2023)

Right: budget, all of Côte d'Ivoire, 1 M plots, weekly updates:
| | Our design | Today |
|---|---|---|
| Storage | ≈ 0.17 GB, < $0.01/month (approximate) | full raster copies in GCS + PostGIS vectors |
| Reanalysis of 1 M plots | ≈ 11 minutes local compute; ≈ $0 | ≈ 7 hours (0.5 s per request, 20-request cap); ≈ $19 of EECU (0.17 EECU-s × $0.40/EECU-hour) |
| Fixed cost | object storage + a small batch job, < $1/month (approximate) | Enterprise Basic platform fee $500/month (includes 33 online + 100 batch EECU-hours) |
- Earth Engine prices: cloud.google.com/earth-engine/pricing, read 14 Oct 2026
- Say it straight: Earth Engine compute per check is cheap (≈ $19 per million checks). The case is **speed (minutes vs hours), no concurrency cap, targeted reanalysis**, and the option to take Earth Engine out of the check path (only needed to extract a release; Hansen and GFC2020 are downloadable directly), which could save the $6,000/year fee if nothing else needs it
| Extraction per release | one Earth Engine export or direct download (*not yet measured*) | — |
- Notes: Don't oversell savings: Earth Engine usage is tens of dollars a month at measured rates. The wins are time to reanalyse (hours → minutes), no concurrency cap, and a check path that doesn't need a $500/month platform. Engineering time, not the cloud bill, is the real cost of our design.

### 12. Trade-offs and next steps
Two columns:
- **What we chose and why**: native pixel grid (not H3), 16-bit packed base, event log, RLE plot index, DuckDB serverless, thresholds as settings
- **Limits**: rule-specific store; synthetic plots; one 50 km area (per-km² figures scale); alert retractions on a slide; prices approximate
- **Migration path**: run alongside the Earth Engine checks → compare verdicts plot by plot → switch re-checks to the compact store; keep Earth Engine for extraction
- Closing line: **Keep the decision, not the data.**

## Appendix slide (optional): judge Q&A
| Question | Answer |
|---|---|
| 100% because you share code? | Shared: the spec's per-cell rule. Verified independently: encoding, events, index, SQL rules |
| Why 3 pixels? | A setting; Compliant never changes across settings |
| Isn't GFC2020 wrong on shade cocoa? | Yes, sometimes (18% commission error in v2); uncertain plots go to review |
| Why not H3? | Hexes don't align with pixels; plot membership would change |
| Why not keep Earth Engine? | Cost per check and latency; we still use it once per release |
| Classified forests only "review"? | Legally restricted but heavily farmed; a legality question for a human |

## Backup demo (record before Saturday)
Run from the repo root with the venv active; each step prints its result:

```
python scripts/encode_layers.py build --radd-until 2026-06-30 --hansen-max-year 24
python scripts/index_plots.py
python scripts/engine.py check --out data/results/compact_verdicts_before_update.csv
python scripts/encode_layers.py events --batch 2026-10-07
python scripts/engine.py update --batch 2026-10-07 --previous data/results/compact_verdicts_before_update.csv --out data/results/compact_verdicts.csv
python scripts/verify_declarations.py
python -m pytest tests -q
```

Narration: build on data held back to 30 June → check 500 plots → a new release arrives → only 39 plots re-checked → results identical to the raw-layer pipeline (tests).
