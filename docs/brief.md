# SystemEarth hackathon brief

Copied from the brief SystemEarth provided (`SystemEarthContext.md`). Minor typos from the source slides fixed.

## SystemEarth: keep the decision, not the data

**Geospatial / data engineering — Building multi-layer, multi-year geospatial data**

A compliance decision rarely needs the entire raw layer. It needs specific segments of pixel information: forest presence at the regulatory cut-off date, degradation over time, overlapping geometry and ESG risks, delivered fast and accurately.

Present on a specific use case and demonstrate how SystemEarth can represent multiple geospatial layers across time and space in a compact and query-efficient form, while preserving the information required to reproduce plot-level compliance decisions.

## What goes into a plot-level compliance decision?

For every farm plot, SystemEarth overlays multiple geospatial layers to answer EUDR questions: was the land forested at the cut-off date, has it been deforested since, and is it legal to produce there?

- **Natural forest baseline** — forest presence at the EUDR cut-off date of 31 December 2020
- **Tree cover loss and deforestation alerts** — annual loss and near-real-time alerts to track deforestation and degradation over time
- **Protected and restricted-use areas** — land legality layers from national and international datasets
- **Plot geometries, monitored over time** — plot polygons checked against every layer, and re-checked as layers update year after year

## What we would like to see

1. **Layer analysis** — which layers and attributes are essential, and which can be simplified, summarised or dropped. Ground it in regulatory definitions for your use case, e.g. forest thresholds and cut-off dates.
2. **Encoding strategy and working prototype** — combine multiple layers and time steps into a single dataset or architecture. Encode a sample set of layers for one region and query the compressed data.
3. **Evaluation** — compare your approach against the original layers on storage, query performance and fidelity of plot-level results.

## How solutions will be evaluated

- **Storage efficiency** — reduction in storage requirements versus the original layers. Bonus: estimate infrastructure and processing costs to store, update and query.
- **Query performance** — query speed (effort, processing time) and resource requirements (e.g. additional storage) for representative plot-level queries.
- **Result fidelity** — decision fidelity: % of plots where all compliance outputs match the original layers. Pixel fidelity: how well pixel-level detail is preserved, where relevant.

## What we are testing

- Logical thinking and systems design. Not maths, and not a brand-new compression algorithm.
- Understanding of the data. Why this dataset, and what the compliance decision actually needs from it.
- Reasoned pipeline choices. E.g. save every month as a raster vs store only the deltas / change events, and why.
- Storage vs speed vs fidelity. Can they see and explain the trade-offs?

## How we judge fairly

- Dataset and use case are open. Teams are not penalised for the dataset they choose.
- Existing algorithms are fine (mosaicking, delta / run-length encoding, chunked COG / Zarr, etc.). The reasoning around them is rewarded.
- Short timeline. Pace your workload and focus on delivering something complete. Ambitious ideas welcome even if only one section is complete.
- Logical thinking is important. Every format, threshold and dropped layer should have a reason.

## Judging guidance from the organisers (message to Rafael, received by 14 Oct 2026)

Verbatim, typos fixed:

> Think of it as a business case + hackathon.
>
> 1. Look for a business case in the sustainability, regulatory compliance point of view, something where there will be a business need.
> 2. Understand what your chosen case is and what you are trying to solve.
> 3. Find a way to implement your prototype by taking into account the data layers needed and how you can make use of data across time and space to solve your issue.
> 4. For the solutions your team proposed we will assess it based on 3 points: storage efficiency, query performance and result fidelity.
> 5. What we are testing here is your logical thinking and systems design, how well you understand the data and the case you proposed, your reasoned pipeline choices, can you see and explain the trade-offs for the 3 technical points mentioned in bullet number 4.
> 6. Being able to take into account the cost involved in the implementation is a bonus. Not necessarily cost of switching implementation but how much it will cost for the implementation you proposed, whether it is a sound budget.

What this adds to the brief:

- **The business case is judged**, not only the engineering: who needs this, why, and what it is worth.
- **The cost bonus is the running cost of our design** (store, update, query, at a stated scale), presented as a sound budget. Switching or migration cost is not what they mean.
- **Trade-offs must be explained for each of the three technical criteria** — storage, query, fidelity — not just results reported.
