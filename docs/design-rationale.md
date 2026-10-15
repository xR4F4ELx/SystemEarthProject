# Design rationale

15 Oct 2026. Every design choice, grouped by the judging criteria, with what SystemEarth does today, why we chose differently, what it cost us, and how to answer a challenge. Numbers come from `docs/evaluation.md` and `docs/business-case.md`.

The one idea behind every choice: **store the decision, not the data.** A plot's EUDR verdict depends on only two facts per 10 m cell: was it forest on 31 Dec 2020, and when (if ever) was it first disturbed after that. Everything below either keeps those facts exactly, makes them fast to query, or proves nothing was lost.

**"SystemEarth today"** uses only what SystemEarth has told us:
- the product manager's latest answers (14 Oct, the source of truth): vectors in PostGIS, rasters in Google Cloud Storage, analysis on Earth Engine, user-driven reanalysis, versions and audit trail recorded;
- the CTO's workflow description;
- the product manager's email (cron scraping);
- the sample files in `data/sample/`.

**Not known** means they haven't told us; nothing is guessed.

## 1. Business case and understanding the case

| Choice | SystemEarth today | Why | What we gave up | If challenged |
|---|---|---|---|---|
| **Côte d'Ivoire cocoa** | Current case: Thai rubber (17 plots, sample data) | Largest EUDR exposure, small plots, tree crops that look like deforestation, farms in protected areas: the hardest test of fidelity | No real plots: synthetic ones | "Your current market is Thai rubber; we built for an expansion market and kept your output schema." |
| **Verification framing** | Same workflow: customer submits GeoJSON + due-diligence CSV, SystemEarth checks it against its own analysis | Matches their real job | Not a rubric metric; kept separate | "76 supplier flags we clear, 46 they miss. Errors run both ways." |
| **Decision spec written first** | Not known how their rules are written down | Fidelity needs fixed rules both pipelines share | Up-front time | "Every threshold, date and pixel rule was fixed before encoding." |

## 2. Layer analysis

| Choice | SystemEarth today | Why | What we gave up | If challenged |
|---|---|---|---|---|
| **GFC2020 as a 1-bit forest baseline** | Not known whether they use a 2020 baseline. The sample *supplier* used Hansen alone and flagged 5 of 17 plots, cleared on site | EUDR counts loss of 2020 forest only. Without the baseline, 99.5% of post-2020 loss outside protected areas is tree-crop or fallow change | GFC2020 errors (18% commission in v2) | "Uncertain plots go to review, not auto-reject." |
| **Hansen: loss 2021–2025 on 2020 forest only** | Full rasters stored in GCS; which bands and years they use is not known | Earlier loss can't change a verdict; `treecover2000` was most of the file | Earlier history | "The cut-off is fixed in law; the baseline carries the one fact we need from before." |
| **RADD: first post-cut-off date + confidence, forest only** | Not known whether they use alerts | 10 m radar dates, newer than annual Hansen | Fires almost only in the park here | "32 supplier misses came from RADD alerts." |
| **Drop GLAD-L** | Not known (they pointed us to GLAD datasets) | Same Landsat source as Hansen | One more confirmation source | "Duplicates Hansen's sensor; RADD adds an independent radar signal." |
| **Earliest date per cell; Hansen year → 1 Jan** | Not known | One date per cell is all the rule needs; conservative | Precision Hansen doesn't have | "We never date deforestation later than the evidence allows." |
| **WDPA: national designations only** | Protected areas stored as vectors in PostGIS; which designations count is not known. The sample supplier recorded no dataset for its checks | The World Heritage record sits inside the park, so it only double-counts | Could miss international areas elsewhere | "Limitation and fix are in the spec." |
| **Thresholds as settings** | Not known | Small or weak signals go to a human | No single "right" number | "Across 12 settings, Compliant never changes." |

## 3. Storage efficiency

| Choice | SystemEarth today | Why | What we gave up | If challenged |
|---|---|---|---|---|
| **Native pixel grid, not H3** | Earth Engine computes on the layers' own grids; their plot-pixel rule is not known | Hexagons don't align with pixels and would break pixel fidelity | H3's multi-resolution convenience | "H3 can sit on top as a coarse index; storage stays on pixels." |
| **16 bits per cell** (forest, sources, date) | Full raw rasters in GCS | Exactly the decision facts in a native type; dates to 2032 | Extra fields; post-2032 dates | "Out-of-range dates are refused, not wrapped." |
| **Zero outside forest** | Full rasters kept, forest or not | 64% of the area is non-forest and compresses to almost nothing | — | "Base raster: 96 kB." |
| **COG + ZSTD, no predictor, 512 px tiles, no overviews** | Rasters served through GeoServer, so built for maps (format details not known) | Measured best: 96 kB vs 111 / 191 / 370 kB alternatives; about 1 tile per plot | 256 px tiles read fewer bytes | "Measured, not assumed." |
| **Event log instead of re-saved rasters** | Cron scraping on a fixed schedule; on-demand scraping not built yet (their words) | Stores only what's new: 3,989 events, 22.7 kB for three months | Retractions designed, not built | "Storage grows with change, not with time." |
| **Run-length plot index** | Plots stored as vectors in PostGIS | 977 kB per cell → 70 kB | Expanded back in SQL | "The RLE the brief mentions, used where it pays." |
| **No raw copies; re-extract via the manifest** | Raster copies kept in GCS | The decision needs about 2% of the raw bytes | **Store is rule-specific** | "Raw goes to cold storage or back to source; the manifest says what to re-fetch." |

**Result:** 140 kB against 6.05 MB raw compressed (**43×**). Quote 43×, not the 1,935× uncompressed figure, which RADD's int32 export inflates.

## 4. Query performance

| Choice | SystemEarth today | Why | What we gave up | If challenged |
|---|---|---|---|---|
| **Plot index + protected-area overlap computed at upload** | Each analysis overlays plots on layers in Earth Engine | Geometry work once per plot, not per check | Re-index if protected areas change | "Plots and protected areas change rarely; checks happen constantly." |
| **DuckDB over files, no server** | Earth Engine compute, commercial (Enterprise Basic) | No infrastructure; files are the snapshot | No multi-user database features | "32 ms vs 0.51 s per plot; 0.34 s vs 7.9 s for 500 plots (our Earth Engine replica of the check)." |
| **Tile-level reads** | Remote Earth Engine request per check; 20 concurrent high-volume requests on Basic | About 1 tile (0.9 kB) per plot, works with object-storage range requests | — | "One small read per check, wherever the files live." |
| **Incremental update: new events joined to the plot index** | **Manual or scheduled reanalysis**, the user decides which past lots, e.g. skipping lots where deforestation came after the harvest | Only plots containing a changed cell can change: 39 of 500; about 99% of cocoa plots touch no 2020 forest | Retractions not handled yet | "We tell users exactly which lots a release affects, and dated events make harvest-date checks trivial." |
| **Rules as SQL** | Not known | Independent of the Python baseline, a real cross-check | Two implementations | Part of the fidelity argument. |

**Be honest about one point:** checking a plot against the raw layers is also fast *once they're in memory* (0.35 ms). The raw approach loses on loading whole rasters and on Earth Engine's per-request latency and 20-request concurrency cap, not on arithmetic.

## 5. Result fidelity

| Choice | SystemEarth today | Why | What we gave up | If challenged |
|---|---|---|---|---|
| **Two pipelines from one spec** | Their check is compared against supplier declarations, not against a second pipeline | The brief scores agreement with the original layers | Building twice | "100% decision fidelity, every field identical." |
| **Stratified synthetic plots** | Real customer plots | Random plots are about 99% trivial; strata exercise every rule | Overall rates not representative | "Per-stratum fidelity; landscape rates from 5,000 random plots." |
| **Pixel-centre rule; points buffered** | Not known (sample plots were all polygons, even under 4 ha) | Deterministic; EUDR allows points up to 4 ha | Slight edge differences vs polygon overlay | "Same rule both sides; edges go to review." |
| **Pixel fidelity over the whole area** | Not applicable (no compact store) | Proves the per-cell facts survive | — | "100% on 32.4 M cells." |
| **Manifest per verdict** | **Already strong:** dataset versions plus a full audit trail of plot changes, analysis runs and supply-shed versions | Makes verdicts re-derivable | — | "Compatible with your audit trail; not a claim against it." |

**Say it yourself, before a judge does:** both pipelines share the spec's per-cell rule, so 100% proves the *encoding, events, index and SQL* lose nothing. It doesn't prove the satellite layers are right on the ground.

## 6. Cost (the bonus)

| Choice | SystemEarth today | Why | If challenged |
|---|---|---|---|
| **Measure, then price** | Enterprise Basic: $500 a month (33 online + 100 batch EECU-hours included), then $0.40 per EECU-hour | A sound budget beats a guessed one | "Earth Engine compute is cheap, about $19 per million checks; we don't claim compute savings." |
| **Pitch speed and dropping a dependency** | A million-plot reanalysis ≈ 7 hours (0.5 s per request, 20-request cap) | Our reanalysis ≈ 11 minutes; Earth Engine needed only to extract releases | "The $6,000-a-year fee could go if nothing else needs Earth Engine; your call." |

## The three choices to defend first

1. **The 2020 forest baseline gate.** The regulatory heart of the design; it explains both the tiny store and the targeted updates.
2. **The event log plus incremental re-check.** Answers the brief's "every month as a raster vs only the changes" question with measured numbers.
3. **The two-pipeline fidelity test.** The proof; being upfront that the per-cell rule is shared makes it more credible.

## Where "not known" matters most (ask before Saturday)

1. **Which forest and loss layers does your own analysis use: a 2020 baseline, alerts, or Hansen only?** Decides how much of the false-flag story is news to them.
2. **How do you decide a pixel is inside a plot, and do you apply thresholds?** Decides how comparable our verdicts are to theirs.
3. **What else runs on Earth Engine?** Decides whether "drop the $500 a month" is realistic.
