# How SystemEarth handles data today

**Source authority:** the email below is from SystemEarth's **product manager**; Matthew Saw is the **CTO**. Where they disagree on the technical stack, the CTO's answers (further down) take precedence. In particular, the forest analysis runs in Earth Engine, not PostGIS.

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
| Downloaded and stored | Google Cloud Storage + PostGIS | Control, fast serving | Per the CTO, not used for the forest analysis; possibly other data or map serving (unconfirmed) |
| Left at source (Earth Engine) | Analysis runs on Google | No storage/compute of their own | Remote call per check (latency, quotas, cost if commercial licence — unconfirmed); not reproducible when datasets change underneath |

Serving (GeoServer, Mapbox) is for map display, not compliance queries — out of scope.

Note: Rafael decided the design should **not** be constrained by this stack. It's context for pain points and the migration-path slide only.

## Datasets SystemEarth pointed to

- GLAD (University of Maryland): https://glad.umd.edu/dataset — producers of Hansen tree cover loss, GLAD alerts, DIST-ALERT. Potentially useful for distinguishing plantations in 2020: *Global Land Cover and Land Use Change 2000–2020*, *Global annual cropland dynamics 2015–2024*.
- Global Nature Watch: https://globalnaturewatch.org/map — interactive forest-change map (built by Vizzuality; operator not confirmed). Use for browsing the AOI, not as a cited source.

## Sample customer data (`data/sample/`)

SystemEarth's current use case: 17 smallholder **rubber** plots, factory RG1, lot 200001, Chai Buri district, Surat Thani, Thailand. Details in `docs/research-notes.md`.

## Answers from Matthew Saw (CTO) to Rafael's questions, 14 Oct 2026

| Question | Answer (paraphrased) | What it means for us |
|---|---|---|
| Which layers live in PostGIS, whole or cropped? | They don't use PostGIS for this; the **forest layers are used in Google Earth Engine** | Their "original layers" query path is a remote Earth Engine computation per check. That is what our query comparison must beat. (The product manager's email mentions PostGIS; the CTO's answer takes precedence.) |
| Re-check every past lot when a layer updates? | **Yes, and they call it a source of inefficiency** | Our incremental re-check addresses a pain they named themselves. Lead with it |
| Record the dataset version behind each verdict? | **Yes** | The manifest is table stakes, not a differentiator. Don't pitch "they can't reproduce verdicts" |
| Commercial Earth Engine licence? Cost per check? | **Commercial; cost is confidential but "relatively expensive"** | Every check and every re-check costs Earth Engine compute. Moving checks off Earth Engine is the cost case; Earth Engine is then only used for extraction on updates |

Workflow (CTO Matthew Saw, hackathon chat, 12 Oct 2026): the customer submits a plot GeoJSON plus a due-diligence CSV. SystemEarth runs its own overlap analysis on the GeoJSON, then checks whether its results match what the supplier declared in the CSV. Most of the CSV is about whether suppliers did their due diligence on the farms they buy from. So the CSV's c2/d1/d2 values are the **supplier's** claims, and SystemEarth is the verifier.
