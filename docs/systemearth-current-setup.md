# How SystemEarth handles data today

**Source authority (Rafael, 14 Oct):** the **product manager's answers below are the latest and the source of truth**. Where the CTO's earlier answers (further down) conflict, they are superseded.

## Product manager's answers, latest (received 14 Oct 2026) — source of truth

| Question | Answer (paraphrased) | What it means for us |
|---|---|---|
| Where do layers live? | **Vector layers in PostGIS; rasters in Google Cloud Storage, served through GeoServer.** Analysis compute runs on Google Earth Engine | They *do* store raster copies, so storage efficiency is a real comparison again (raw rasters in GCS vs our compact store). Their checks still use paid Earth Engine compute |
| Re-check every past lot on update? | **No, not automatically.** Users can run a **manual or scheduled reanalysis** of a past lot with current layers. Some lots don't need it, e.g. when the deforestation event happens **after the harvest** | Drop "they re-check everything". Our value is telling users **which** past lots an update actually affects (the affected-plot query) and making scheduled reanalysis cheap and targeted. The harvest-date point fits the event log: only events dated before a lot's harvest can matter for that lot |
| Record the dataset version behind each verdict? | **Yes, plus a full audit trail**: plot changes (modified, added, deleted), when analysis ran, and versions of the supply shed | Reproducibility is fully covered on their side. Our manifest is compatible, not a differentiator |
| Commercial Earth Engine? Cost per check? | **Commercial licence, Earth Engine "Enterprise Basic" pricing** (cloud.google.com/earth-engine/pricing) | Per-check cost = EECU used × the Basic plan's EECU price; our measured EECU per check can be priced directly |

## Product manager's original description

## Product manager's description (email reply to Rafael)

> We handle external datasets in a few ways. Some datasets are scraped from their source on a schedule, using Python scripts run by cron jobs on our own server. Fairly simple, but we haven't quite yet had time to develop the pipeline to automatically scrape when needed.
>
> Others we download and store in our own system (Google Cloud Storage, and PostGIS database). GeoServer and Mapbox are used to serve the layers on our platform.
>
> For the data which are hosted by the original source, we don't store a copy, unless for backup. Examples are datasets in Google Earth Engine — those map requests are redirected to Google, and our analysis runs on Google's servers. This saves us storage space and processing power.

## Analysis

| Approach | How it works | Strength | Weakness |
|---|---|---|---|
| Scheduled scraping | Python + cron on own server | Simple, cheap | Fixed schedule, not change-driven; likely re-downloads whole files |
| Downloaded and stored | Google Cloud Storage (rasters, served via GeoServer) + PostGIS (vectors) | Control, fast serving | Confirmed by the PM's latest answers; stores full raster copies the decision mostly doesn't need |
| Left at source (Earth Engine) | Analysis runs on Google | No storage/compute of their own | Remote call per check (latency, quotas, cost if commercial licence — unconfirmed); not reproducible when datasets change underneath |

Serving (GeoServer, Mapbox) is for map display, not compliance queries — out of scope.

Note: Rafael decided the design should **not** be constrained by this stack. It's context for pain points and the migration-path slide only.

## Datasets SystemEarth pointed to

- GLAD (University of Maryland): https://glad.umd.edu/dataset — producers of Hansen tree cover loss, GLAD alerts, DIST-ALERT. Potentially useful for distinguishing plantations in 2020: *Global Land Cover and Land Use Change 2000–2020*, *Global annual cropland dynamics 2015–2024*.
- Global Nature Watch: https://globalnaturewatch.org/map — interactive forest-change map (built by Vizzuality; operator not confirmed). Use for browsing the AOI, not as a cited source.

## Sample customer data (`data/sample/`)

SystemEarth's current use case: 17 smallholder **rubber** plots, factory RG1, lot 200001, Chai Buri district, Surat Thani, Thailand. Details in `docs/research-notes.md`.

## Answers from Matthew Saw (CTO), 14 Oct 2026 — superseded where they conflict with the PM's latest answers above

Superseded: "no PostGIS, layers in Earth Engine" (PM: vectors in PostGIS, rasters in GCS) and "re-check every past lot" (PM: manual or scheduled reanalysis, user's choice). Still consistent: versions are recorded; Earth Engine is commercial.

| Question | Answer (paraphrased) | What it means for us |
|---|---|---|
| Which layers live in PostGIS, whole or cropped? | They don't use PostGIS for this; the **forest layers are used in Google Earth Engine** | Their "original layers" query path is a remote Earth Engine computation per check. That is what our query comparison must beat. (Superseded: the PM's latest answer says vectors live in PostGIS and rasters in GCS.) |
| Re-check every past lot when a layer updates? | **Yes, and they call it a source of inefficiency** | Our incremental re-check addresses a pain they named themselves. Lead with it |
| Record the dataset version behind each verdict? | **Yes** | The manifest is table stakes, not a differentiator. Don't pitch "they can't reproduce verdicts" |
| Commercial Earth Engine licence? Cost per check? | **Commercial; cost is confidential but "relatively expensive"** | Every check and every re-check costs Earth Engine compute. Moving checks off Earth Engine is the cost case; Earth Engine is then only used for extraction on updates |

Workflow (CTO Matthew Saw, hackathon chat, 12 Oct 2026): the customer submits a plot GeoJSON plus a due-diligence CSV. SystemEarth runs its own overlap analysis on the GeoJSON, then checks whether its results match what the supplier declared in the CSV. Most of the CSV is about whether suppliers did their due diligence on the farms they buy from. So the CSV's c2/d1/d2 values are the **supplier's** claims, and SystemEarth is the verifier.
