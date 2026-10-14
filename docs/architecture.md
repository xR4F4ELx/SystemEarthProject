# Target architecture

Chosen on storage, speed and fidelity alone — not constrained by SystemEarth's current stack.

## Core insight

A disturbance only matters where there was forest in 2020. Loss on land already in agriculture in 2020 can never change the verdict. So: keep a 1-bit forest-2020 mask and, only inside it, the first post-cut-off disturbance date. Everything else can be dropped or re-fetched from source.

## Components

```
Public layers (GFC2020, Hansen, RADD, WDPA)      Plot polygons (customer uploads)
            │                                               │
   Extract and encode (mask, resample, pack)        Plot indexer (pixels each plot covers)
            │                                               │
            └──────────────► Compact store (object storage) ◄┘
                 ├─ Base raster   (COG, 16-bit per pixel)
                 ├─ Event log     (GeoParquet, partitioned by month)
                 ├─ Plot index    (Parquet: plot_id → pixel ids)
                 └─ Manifest      (JSON: dataset IDs, versions, dates)
                                   │
                        Rule engine (DuckDB) — applies docs/decision-spec.md
                                   │
                        Plot verdicts (c2, d1, d2, final status + manifest ID)
```

| Component | Content | Why |
|---|---|---|
| Base raster | Per 10 m pixel: forest-2020 bit + first post-cut-off disturbance date + source + confidence; zero outside forest | Exactly what the rules use; zeros compress to almost nothing |
| Event log | One row per newly detected disturbance: cell id, date, source (confidence is in the source: radd_high / radd_low). Plain Parquet, one file per release batch | Appends by adding a file; tiny when change is rare. The cell id is the spatial key, so stored geometry would only add bulk |
| Plot index | Which grid cells each plot covers (pixel-centre rule), **run-length encoded** per grid row; plus each plot's protected-area overlap shares, computed once at upload | "Which plots does this alert affect?" becomes a key join; no geometry at query time. RLE: 70 kB vs 977 kB for 500 plots |
| Manifest | Dataset names, versions, export dates | Makes every verdict reproducible |
| Rule engine | DuckDB SQL over Parquet/COG-derived tables | No server, cheap, fast batch scans, files = snapshot |
| WDPA | Clipped vector polygons with dataset date | Small already; rasterising loses boundary accuracy |

## Incremental update flow (the query-performance win)

1. New alerts published (only dates since last run).
2. Append to event log (one new Parquet file).
3. Join new pixels to plot index → affected plots.
4. Re-check only those; skip the rest (verdict depends only on pixels in the plot's index + manifest, so it cannot change).

Wider re-check needed when: a baseline layer is revised (e.g. GFC2020 v5 → rebuild raster, re-check all; rare) or PA boundaries change (re-check plots near changed areas). Periodically (e.g. quarterly) fold the event log into a fresh base raster.

## Encoding options considered

| Option | Storage | Query | Fidelity | Weakness |
|---|---|---|---|---|
| A. Cropped raw stack (compressed GeoTIFF per layer) | Medium | Medium | Exact | Keeps what the decision doesn't need — **this is the comparison baseline** |
| **B. Packed decision raster** | Very small | Fast | Exact for decision | Rebuild per update is wasteful |
| **C. Event table** | Tiny when change rare | Fast with index | Exact | Awkward for static baseline |
| D. Precomputed verdicts only | Smallest | Instant | Known plots only | New plots/rule changes need raw again; no pixel fidelity |

**Chosen: B as base + C for updates**, plus plot index and manifest.

## Unconstrained-stack choices vs SystemEarth today

"Today" is per the CTO's answers (14 Oct, `docs/systemearth-current-setup.md`), which take precedence over the earlier product-manager email.

| Area | Today | Target |
|---|---|---|
| Re-checks | All past lots on every layer update (CTO: "a source of inefficiency") | Only affected plots |
| Query engine | Google Earth Engine, commercial licence, paid per check | DuckDB over local/object-storage files (serverless) |
| Change storage | None: each check recomputes against the full layers in Earth Engine | Event log, GeoParquet by month |
| Scheduling | Cron | Change-triggered orchestrator (e.g. Dagster) — slide only |
| Forest-layer copies | Not kept; layers stay in Earth Engine | Compact decision store only; raw re-extracted from source when needed; manifest records versions (they already record versions too) |

## Demo scope

| Part | Build or slide |
|---|---|
| Extract/encode, base raster, plot index, manifest | Build |
| DuckDB rule engine | Build |
| Event log + incremental update | Build — **hold back the last 3 months of RADD alerts** when building the base, then "publish" them as the update |
| Raw pipeline (comparison baseline) | Build — same rules on raw files |
| Orchestration, baseline-revision handling, quarterly fold-in | Slide |
| Migration path from SystemEarth's stack | Slide |

## Evaluation to report

Storage (raw vs compact, by layer), query time per plot (full re-check vs incremental), decision fidelity %, field fidelity, area agreement, pixel fidelity, cost estimate (store/update/query on cloud object storage), update vs rebuild cost, sensitivity of the 3-pixel threshold.
