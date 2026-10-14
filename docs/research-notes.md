# Research notes (as of 13 Oct 2026)

## 1. EUDR status

- After two postponements, main obligations apply from **30 Dec 2026** (most operators/traders); **30 Jun 2027** for certain micro and small operators.
- Commission simplification review published **4 May 2026**: the core legal text is **not** reopened; scope adjusted via a draft delegated act (e.g. leather proposed out); updated guidance/FAQ; Information System relaunched June 2026. Commission estimates ~75% lower annual compliance costs.
- Sources: Bird & Bird (cm.twobirds.com, Jan 2026), VinciWorks (Jan 2026), JD Supra (May 2026), WRI statement (5 May 2026).

### Definitions that drive the data (Article 2, paraphrased from knowledge — verify against official text before quoting on slides)

| Concept | Definition | Design implication |
|---|---|---|
| Forest | >0.5 ha, trees >5 m, canopy >10% (or able to reach); excludes land predominantly under agricultural or urban use | Land-use definition: tree cover ≠ forest |
| Cut-off | **31 December 2020** | One snapshot needed, not history before it |
| Deforestation | Conversion of forest to agricultural use, human-induced or not | Needs forest-2020 + change after |
| Degradation | Structural change, e.g. primary/naturally regenerating forest → plantation or other wooded land | Mainly wood products; standard loss layers can't detect it → out of scope |
| Legality | Produced per laws of the country of production | Protected-area layers are only a partial proxy |
| Geolocation | Polygons for plots >4 ha (except cattle); a point allowed for smaller plots | Engine must handle points and polygons |

### Cut-off mapping to layers

| Layer | How the cut-off applies |
|---|---|
| GFC2020 | Is the forest map at the cut-off |
| Hansen `lossyear` | ≤2020 → ignore; ≥2021 → potential deforestation |
| Alerts | Only dates after 2020-12-31 matter |
| Protected areas | Cut-off doesn't apply (legality vs current boundaries) |

Nuance: GFC2020 is built from imagery *around* 2020; Hansen annual dates are coarse → edge cases go to "needs review".

## 2. Datasets

| Layer | Resolution / format | Time | Coverage | Notes |
|---|---|---|---|---|
| JRC GFC2020 (`JRC/GFC2020/V4` in Earth Engine) | 10 m binary raster | Snapshot 2020 | Global | v2 accuracy: 91% overall, 18% forest commission error, 8% omission (CIRAD accuracy report). Not mandatory, not exclusive, not legally binding. v3 dated 28 Nov 2025; v4 on Earth Engine 2026. |
| JRC GFT2020 | 10 m forest types | 2020 | Global | Only standard layer helping with degradation |
| Hansen GFC v1.13 (`UMD/hansen/global_forest_change_2025_v1_13`) | ~30 m | 2000–2025, `lossyear` 1–25 = 2001–2025 | Global | Bands: treecover2000, loss, gain (2000–2012 only), lossyear, datamask, first_/last_ imagery bands. CC-BY-4.0. Measures tree cover loss, not deforestation. |
| GFW integrated alerts (GLAD-L + GLAD-S2 + RADD) | 10 m (GLAD-L upsampled from 30 m) | Daily, from 2015 | 30°N–30°S | Stores earliest detection date; confidence can change retroactively. DIST-ALERT now added (global vegetation). GLAD-S2 = South America only. |
| RADD (Wageningen, Sentinel-1 radar) | 10 m | From 2019 (Africa) | Humid tropical primary forest incl. 25 African countries; expanded to continental SE Asia | Earth Engine asset believed to be `projects/radar-wur/raddalert/v1` (unverified) |
| WDPA (`WCMC/WDPA/current/polygons`) | Vector | ~monthly | Global | Some entries are points only; quality varies |

Reference tool: **FAO Whisp** ("What is in that plot?", github.com/forestdatapartnership/whisp) — open-source plot-level EUDR risk using "convergence of evidence"; perennial-crop logic downgrades risk when a commodity layer shows agriculture by end-2020. Useful precedent for decision rules and "needs review".

## 3. SystemEarth sample data findings (`data/sample/`)

**Whose data is this (CTO Matthew Saw, hackathon chat, 12 Oct 2026):** SystemEarth collects the plot GeoJSON from customers, runs its own overlap analysis, then checks the result against the customer's submitted due-diligence CSV. So the CSV is the **supplier's self-declaration** (factory RG1), not SystemEarth's analysis. SystemEarth's role is **verifier**.

- GeoJSON properties are only `ProducerName`, `ProducerCountry`, `ProductionPlace` (= plot ID), `Area` (ha). Synthetic plots should use exactly this schema.
- 17 smallholder rubber plots, 0.53–20.07 ha, Surat Thani, TH. All stored as polygons (even <4 ha). Stated area matches geometry (spot-checked 4).
- P-0002, P-0007, P-0012 missing → filtered out by a validation step ("valid" in filename).
- Only **3 spatial outputs**: c2 deforestation result, d1 strict PA overlap, d2 restricted area overlap. The other ~30 columns are document/survey-based and constant. These are what SystemEarth can verify against its own analysis.
- **The supplier declared Hansen tree cover loss 2021–2023 as its only deforestation layer** (column c1). No 2020 baseline, no alerts; 2024+ not checked. This is the supplier's method, not SystemEarth's.
- **5 of 17 plots (29%) flagged by the supplier, then cleared by the supplier's on-site verification** (P-0003, P-0005, P-0008, P-0010, P-0013) — likely rubber replanting (~25–30 yr cycle) showing as Hansen loss. Each false flag = a field visit for the supplier.
- **The supplier's PA checks record no dataset/version** → their declaration cannot be reproduced, and SystemEarth cannot tell whether a disagreement comes from different data or a real problem.
- **Data quality bug:** evidence links point to wrong plot IDs (P-0003 → "hyperlink-RG1-P-0002", P-0008 → "…P-0004", …) — looks off-by-one, maybe from removing invalid plots.
- Quantity columns (j1/j2) look templated (j2 steps of exactly 10,050 kg) — probably anonymised; don't draw conclusions.

## 4. Pain points (PM framing)

The brief frames it as storage; the real customer pain is that **re-checking plots is slow, costly and hard to defend in an audit**.

| # | Pain | Evidence | Root cause | Rubric |
|---|---|---|---|---|
| 1 | False flags → field visits | Supplier flagged 5/17, then cleared them on site | Supplier's loss layer has no 2020 baseline | Decision fidelity |
| 2 | Verdicts not reproducible | No version on supplier's PA checks | Inputs not recorded; sources change (GFC2020 v1→v4, alert confidence) | Decision fidelity |
| 3 | Re-checks slow/costly as data grows | Full raw copies or remote Earth Engine | Overlay against full rasters for a tiny slice | Query, storage |
| 4 | Storage grows each update | Cron + full copies | Snapshots, not changes | Storage |
| 5 | Updates not change-driven | Their own admission | Fixed schedule | Storage, cost |
| 6 | Record errors | Wrong evidence links | Weak validation | Side observation |
| 7 | Stale loss window | c1 = 2021–2023 | Layer list not kept current | Fidelity |

Stakeholders: EU operator (needs DDS + audit-proof evidence), SystemEarth, supplier/processor (few false flags), smallholder (not wrongly excluded), competent authority (audits possibly years later; records kept ~5 years — verify).

## 5. Competitor: Treefera

- London, "first-mile commodity intelligence"; $30M Series B June 2025 (Notion Capital lead), ~$44M total; 21–100 staff.
- Products: market intelligence (yield forecasts), risk intelligence (normalised scores), environmental intelligence (plot-level data for carbon/compliance incl. EUDR). API + dashboard.
- Claims: 100+ sources, ~40 yrs history, standardised into a common format ("data fabric"), 10 m plot level, refresh every 5 days, probabilistic outputs, "auditable" (no detail how), low compute.
- Stack (job ad): Python, PostGIS, GeoPandas, Xarray, Dagster.
- Takeaways: validates "pre-process into a common store, serve answers fast"; SystemEarth can differentiate on **explicit reproducibility** (manifest per verdict) and an EUDR-specific narrow scope. Don't claim Treefera lacks auditability — only that our design makes it explicit.

## 6. Judge challenges and answers

| Challenge | Answer |
|---|---|
| The brief asked about storage — why talk about false flags? | Decision fidelity is weighted equally; in the sample, 29% of the supplier's declared decisions needed a field visit to correct. Compact + correct + reproducible is the win. |
| Why build for Côte d'Ivoire when you were given Thai rubber? | Their current workflow covers Thai rubber; we built for an expansion market, kept their output schema; the pipeline is region-agnostic (optionally run the 17 Thai plots through it). |
| Why not keep querying Earth Engine? | Remote call per re-check, quotas/cost, silent dataset changes; local compact store is faster, cheaper, reproducible. |
| Why not buy Treefera data? | Broad platform priced for traders/banks, closed methods; SystemEarth needs a narrow, explainable, reproducible EUDR verdict at low cost per plot. |
| Why drop data before 2021? | Article 2 cut-off; earlier loss can't change c2. |
| Isn't GFC2020 wrong on shade cocoa? | Yes, possibly (18% commission error in v2); disagreements go to review, not auto-reject. |
| Why not H3? | Hexes don't align with pixels; would change which pixels are "inside" a plot. Fine as coarse index only. |
| How would we migrate? | Run compact store alongside current system, compare verdicts, then retire raw copies. |
