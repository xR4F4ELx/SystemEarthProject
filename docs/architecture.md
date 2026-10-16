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
                 ├─ Event log     (Parquet, one file per release)
                 ├─ Plot index    (Parquet: plot_id → run-length encoded cells)
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

"Today" is per the product manager's latest answers (14 Oct, `docs/systemearth-current-setup.md`), which are the source of truth.

| Area | Today | Target |
|---|---|---|
| Re-checks | Manual or scheduled reanalysis of past lots, chosen by the user (e.g. skipped when deforestation happened after the harvest) | The system lists exactly which lots an update affects; reanalysis runs only on those, optionally only with events dated before each lot's harvest |
| Query engine | Earth Engine compute, commercial (Enterprise Basic), paid per EECU | DuckDB over local/object-storage files (serverless) |
| Change storage | None: each reanalysis recomputes against the full layers | Event log, one Parquet file per release |
| Scheduling | Cron, manual or scheduled reanalysis | Change-triggered orchestrator (e.g. Dagster) — slide only |
| Layer copies | Rasters in Google Cloud Storage (served via GeoServer), vectors in PostGIS | Compact decision store; raw re-extracted from source when needed |
| Audit | Dataset versions and a full audit trail already recorded | Manifest per verdict, compatible with their trail |

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

Results: `docs/evaluation.md`.

## Tech stack and system design (as built, 15 Oct 2026)

A file-based, serverless pipeline with three lifecycles: layers are encoded **once per release**, plots are indexed **once per upload**, and checks are cheap SQL lookups over small files. Expensive work (geometry, raster processing) happens when data changes; checks only do lookups. That's why checks are cheap and re-checks can be targeted.

```
Public layers (GFC2020, Hansen, RADD, WDPA)          Customer upload (plot GeoJSON + DD CSV)
        │  once per release                                   │  once per upload
  Extract + encode (encode_layers.py)                  Index plots (index_plots.py)
        │                                                     │
        └──────────────► Compact store (object storage) ◄─────┘
                         base.tif · events/*.parquet · plot_index.parquet
                         plot_pa.parquet · protected_areas.parquet · manifest.json
                                          │  per check
                         DuckDB rule engine (engine.py: check / update)
                                          │                    ▲ supplier CSV
                         Verdicts + manifest ID ──► Verify declarations (verify_declarations.py)
                                          │
                         Demo console (app/demo_console.py) · later: platform API
```

### The three lifecycles

| Lifecycle | Trigger | What runs | Cost (measured, 2,756 km² AOI) |
|---|---|---|---|
| **Per release** | New Hansen year or RADD alerts | Extract from source, keep only 2020-forest cells and post-cut-off dates, then pack (first build) or append an event file | 3.9 s full rebuild; an update is one 22.7 kB file |
| **Per upload** | Customer submits plots | Turn each plot into grid-cell runs; compute protected-area overlaps once | ≈ 5 s for 500 plots |
| **Per check** | Reanalysis request, or "which lots does this release affect?" | DuckDB joins plot cells to the base raster and events, then applies the rules in SQL | 32 ms per plot; 0.11 s for the 39 affected plots |

### Tech stack

| Layer | Tool | Why | Rejected |
|---|---|---|---|
| Extraction | **Earth Engine Python API** (batch export) | The layers are hosted there; SystemEarth already has a licence | Direct downloads also work for Hansen and GFC2020 (the way to drop Earth Engine later) |
| Raster processing | **rasterio (GDAL) + NumPy** | Standard, fast, exact control of grids and resampling | xarray/Zarr: more machinery than one 2D layer needs |
| Vector processing | **GeoPandas, Shapely, pyproj** | Plot footprints, overlay with protected areas, geodesic areas | PostGIS: a server for something done once per upload |
| Base raster format | **Cloud-Optimised GeoTIFF**, ZSTD, 512 px tiles | One tile read per plot over HTTP range requests; any GIS tool opens it | Zarr (better for many time steps, which the event log already covers); DEFLATE and LZW measured larger |
| Events, index, protected areas | **Parquet (ZSTD)**; protected areas as GeoParquet | Columnar, compressed, appendable as new files, read natively by DuckDB | A database table: needs a running server |
| Query engine | **DuckDB** (in-process SQL) | No server, fast joins over Parquet, the rules readable as SQL | PostGIS (always-on server); Earth Engine (paid per check, 0.5 s latency, 20-request cap on Basic) |
| Reproducibility | **JSON manifest**: dataset IDs, data state, SHA-256 hashes | Ties every verdict to exact inputs | — (SystemEarth's audit trail already covers this; ours plugs into it) |
| Tests | **pytest**, 53 tests | Rule paths, encoding round trip, events vs full data, compact vs raw on every field | — |
| Demo UI | **Streamlit** + pydeck map | Buttons call the real pipeline; built in hours | Full web app: days, and not scored |
| Runtime | **Python 3.12** (native arm64) | Current geospatial wheels; benchmarks not distorted by emulation | — |

### Production deployment (slide, not built)

Slots into what SystemEarth already uses (GCS, PostGIS, Earth Engine):

| Concern | Production design |
|---|---|
| **Storage** | Compact store in a **GCS bucket**; DuckDB and GDAL read it directly over HTTP range requests, with no download step |
| **Release pipeline** | A scheduled check for new releases (Cloud Scheduler) triggers a **Cloud Run job**: extract → append an event file → list affected lots. Replaces fixed-schedule cron scraping |
| **Checks** | A small **Cloud Run service** (an API inside their platform) runs the engine on demand: reanalysis, upload checks, "affected by this release" |
| **Plots and results** | Plots stay in **PostGIS**; the index is rebuilt for new or changed plots; verdicts with manifest IDs go into their audit trail |
| **Scale** | Partition the base raster and index by region (e.g. one COG per 1° tile) and events by release; checks parallelise by plot batch. Côte d'Ivoire is ≈ 120× the AOI; the store scales linearly (≈ 16 MB) |
| **Failure handling** | A bad release rolls back by dropping its event file; a baseline revision (e.g. GFC2020 v5) triggers a full rebuild and reanalysis; alert retractions become "retract" events; a periodic fold-in compacts events into a fresh base |
| **Earth Engine** | Used only for extraction, or replaced by direct downloads, which could remove the $500/month plan if nothing else needs it |

### Built vs slide

- **Built and tested:** extraction, encoding, event log, plot index, DuckDB engine, incremental update, supplier verification, evaluation, Earth Engine benchmark, map, demo console.
- **Slide only:** cloud deployment, automatic release trigger, retract events, periodic fold-in, region partitioning, platform API.

### Judge challenges

| Challenge | Answer |
|---|---|
| "Why not PostGIS? You already have it." | PostGIS is a database server; a check here is a key lookup over small static files, which DuckDB does with no server. PostGIS stays where it fits: plots and verdicts |
| "Why not Zarr?" | Zarr shines for many time steps of a cube; we replaced time steps with an event log, so one 2D raster plus events is simpler and smaller |
| "Is DuckDB production-grade?" | An embedded engine widely used for analytics over Parquet; here it runs one job at a time against immutable files, its ideal workload |
| "Does it scale to millions of plots?" | Measured 0.67 ms per plot in one thread → ≈ 11 minutes per million; split by region to parallelise |
