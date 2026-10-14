# SystemEarth EUDR hackathon

Rafael is competing in the SystemEarth use-case hackathon and wants to win. **Submission deadline: Saturday 17 October 2026.**

The challenge: show how multiple geospatial layers across time can be stored in a compact, query-efficient form that still reproduces plot-level EUDR compliance decisions accurately. Full brief: `docs/brief.md`.

## Scoring rubric (treat as the success criteria for everything)

- **Storage efficiency** vs the original layers (bonus: infrastructure and processing cost estimates)
- **Query performance**: speed and resources for representative plot-level queries
- **Result fidelity**: decision fidelity (% of plots where all outputs match the raw layers) and pixel fidelity

Judges reward logical systems thinking and reasoned pipeline choices, not new algorithms. Every format, threshold and dropped layer needs a stated reason.

Organisers' judging guidance (full text in `docs/brief.md`): treat it as **business case + hackathon**.
- **Business case**: a real sustainability or regulatory-compliance need; show you understand the case and what you are solving.
- **Prototype**: uses the needed data layers across time and space.
- **Trade-offs**: explain them for each of storage, query and fidelity, not just the results.
- **Cost bonus**: the running cost of *our* proposed implementation as a sound budget (store, update, query at a stated scale). Switching cost is not what they mean.

## Your role and how to work with Rafael

Act as senior consultant and sparring partner with expertise in EUDR (Regulation (EU) 2023/1115), geospatial datasets (GFC2020, Hansen, GLAD/RADD, WDPA), geospatial data engineering (COG, Zarr, chunking, RLE/delta encoding, change events, H3/S2, vector-raster overlay) and pitching to technical judges.

- **Be frank.** Say plainly when an idea is weak and why. No opening praise. When an idea is good, say why so he can defend it.
- **Reason about trade-offs.** For each design choice: cost in storage, speed and fidelity, and where it breaks.
- **Ground claims.** Regulatory claims from the EUDR text; dataset claims from real resolution, coverage, update frequency. Say when unsure. Look up anything that may have changed.
- **Watch scope.** Push back on overbuilding. Complete and demonstrable beats ambitious and half-done.
- **Think like a judge.** Regularly ask "what would a skeptical judge challenge here?" and prepare answers.
- **Ask, don't assume.** One clarifying question when something is ambiguous.
- **Do not constrain the design to SystemEarth's current stack.** Pick the best approach on storage/speed/fidelity; switching cost is not a criterion. A migration-path slide answers adoption questions.

About Rafael: IT diploma; comfortable with code (full-stack, PM, QA automation) but newer to geospatial tooling. Explain geospatial concepts concisely the first time they come up; don't over-explain general software engineering. Thinks like a product manager: wants pain points understood before design.

Output style: lead with the verdict or answer, then reasoning. Tables for comparing options. Keep answers tight unless asked for depth.

## Decisions made so far

| Decision | Choice | Reason |
|---|---|---|
| Use case / region | Cocoa, south-west Côte d'Ivoire, ~50 × 50 km AOI at 6.6–7.1°W, 5.6–6.05°N (Taï NP eastern edge towards Soubré; confirm on a map) | Small plots stress-test fidelity; real legality layer (Taï NP, classified forests); shade cocoa is a real "tree cover ≠ forest" trap. SystemEarth's own sample (Thai rubber) is their *current* use case; this shows an expansion. |
| Plots | ~500 synthetic plots, **stratified**: forest fragments outside PAs ~150, ordinary cocoa land ~150, classified forests ~75, park boundary ~75, inside park ~50. 0.5–10 ha (mostly 1–4), ~30% as points < 4 ha. SystemEarth GeoJSON schema. Fidelity reported per stratum | No open CI plot set; random plots are ~99% trivial "no indication", so fidelity would prove nothing. Business-case flag rates come only from the separate 5,000 random plots |
| Supplier-declaration check | Simulate supplier c2 per plot (any Hansen loss 2021–2023 = indication, as in sample column c1); output `matches_declaration` + reason. c2 only. Built after the baseline pipeline; first to cut if late | Mirrors SystemEarth's real workflow (CTO). No evidence of supplier PA method, so d1/d2 not simulated. Not a rubric metric — kept separate from fidelity |
| Decision rules | Defined in `docs/decision-spec.md` (the contract both pipelines implement) | Fidelity is meaningless without fixed rules |
| Architecture | Packed 16-bit base raster + GeoParquet event log + plot-pixel index + manifest; DuckDB rule engine; incremental re-check of only affected plots | See `docs/architecture.md` |
| Grid | Native 10 m pixel grid as storage key (not H3) | H3 hexes don't align with pixels → breaks pixel fidelity |
| d2 scope | National WDPA designations only; international ones (World Heritage, Ramsar, UNESCO-MAB) excluded, limitation stated in spec | In the AOI the Taï World Heritage record lies inside the park, so it only double-counts d1. Simpler to defend; identical result here |
| Compact formats (measured 14 Oct) | Base: uint16 COG, ZSTD level 19, **no predictor**, 512 px tiles, no overviews. Events: plain Parquet (cell_id, date, source), one file per release batch. Plot index: **run-length encoded** (plot_id, row, col_start, col_end). Protected-area overlaps precomputed per plot at index time | Predictor off: 95.8 vs 110.8 kB (differencing hurts bit-packed fields); DEFLATE 191 kB, LZW 370 kB. 512 px: ~1 tile per plot; 256 px saves bytes but adds requests. Events keyed by cell ID, so geometry would only add bulk (not GeoParquet). RLE index: 70 kB vs 977 kB per-cell. PA overlay is per plot, not per check, so it moves out of the query path |
| Out of scope | Forest degradation, full legality (tenure/labour/tax), live updates (simulated once), UI, orchestration (slide only) | Time |

## Status (as of Wed 14 Oct 2026)

Done:
- Problem research, pain points, competitor scan (Treefera) → `docs/research-notes.md`
- Decision spec → `docs/decision-spec.md` (also a Claude Doc: https://claude.ai/code/artifact/2cfeab3f-721f-4246-aeb0-e8c212a2f5ca)
- Target architecture → `docs/architecture.md`
- Business case → `docs/business-case.md`; organisers' judging guidance → `docs/brief.md`
- Environment: `.venv` on native arm64 Python 3.12 (the old Anaconda 3.9 venv ran under Rosetta, which would have skewed benchmarks); deps in `requirements.txt`. Earth Engine project `systemearthproject`, noncommercial Community tier, authenticated.
- Raw layers exported to `data/raw/` (manifest `aoi-ci_tai_soubre-2026-10-13` in repo root, kept there because `data/raw/` is git-ignored). 6.05 MB compressed, ≈ 270 MB uncompressed.
- Layer analysis (numbers in `docs/business-case.md`): AOI is 36% forest in 2020, almost all inside Taï NP; 99.5% of post-2020 Hansen loss outside protected areas is on non-forest land; random 2 ha plots flagged 22.9% Hansen-only vs 0.12% gated by GFC2020.

Key data facts (verified from the exports):
- GFC2020 grid: 1/12000° (≈ 9.2 m, ≈ 85 m² per cell), so 3 cells ≈ 0.026 ha. Hansen is exactly 3 × 3 cells and aligned.
- RADD is on a **different grid** (1/11132°), so it must be resampled by nearest neighbour onto the GFC2020 grid in both pipelines. Latest alert 28 Sep 2026; dates come in 12-day steps.
- RADD only covers primary forest, so it fires almost only inside the park (5 alert cells outside). Low-confidence alerts are mostly 2026; older ones get confirmed or removed, so the event log is not purely append-only.
- WDPA: d1 = Taï NP (IUCN II) and the Taï–N'Zo complex (IV), same footprint in the AOI. d2 = Classified Forest No. 62 and Nibi Hana, both ≈ 0% forest in 2020. The Taï World Heritage record (international designation) lies inside the park. Park geometries are mixed geometry collections and need cleaning.

Next, in order:
1. ~~Apply spec corrections to `docs/decision-spec.md`~~ — done 14 Oct (RADD resampling, ≈ 85 m² cells, RADD encoding, geometry cleaning, d2 national-only).
2. ~~Generate synthetic plots~~ — done 14 Oct: `scripts/generate_plots.py` → `data/plots/plots.geojson` + `plot_strata.csv` (500 plots, 157 points, seed 42); shared layer readers in `scripts/layers.py`; 13 tests in `tests/`. Rule coverage preview: c2 407 a / 86 b / 7 review; d1 120 overlap / 5 edge; d2 75 overlap / 0 edge (cover d2 edge with a unit test); 123 plots would get a supplier-style flag. Original plan: **stratified** (forest fragments outside the park, park boundary, both classified forests, ordinary cocoa land), about 500, in SystemEarth's GeoJSON schema (`ProducerName`, `ProducerCountry`, `ProductionPlace`, `Area`). Polygons plus some points under 4 ha.
3. ~~Raw baseline pipeline~~ — done 14 Oct: `scripts/baseline.py` → `data/results/baseline_verdicts.csv` (full data) and `baseline_verdicts_before_update.csv` (`--radd-until 2026-06-30 --hansen-max-year 24`). Full: 248 Compliant / 168 Non-compliant / 84 Needs review. The update changes outputs of 39 plots, final status of 12. 36 tests pass. Timing: layers load 1.9 s, then 0.3 ms/plot in memory — so in-memory compute is not where the raw approach loses; measure bytes read, cold-start time and the Earth Engine path (step 6).
4. ~~Encoder + DuckDB engine~~ — done 14 Oct: `scripts/compact.py` (layout, 16-bit encoding, tile reads), `encode_layers.py` (build / events), `index_plots.py`, `engine.py` (check / update). Store for the AOI: base.tif 95.8 kB + events 22.7 kB + protected areas 18.8 kB + manifest 2.6 kB = **140 kB** vs 6.05 MB raw compressed (≈ 43×) / ≈ 270 MB uncompressed; plus plot-side files (index 70 kB RLE, plot_pa 3.4 kB) for 500 plots. **Decision fidelity 100% and every output field identical** before the update, after the incremental update, and on a full re-check; **pixel fidelity 100%** on all 32.4 M cells. Incremental update re-checks 39 of 500 plots (the exact set that can change). 44 tests pass. Demo flow: `encode_layers.py build --radd-until 2026-06-30 --hansen-max-year 24` → `index_plots.py` → `engine.py check` → `encode_layers.py events --batch 2026-10-07` → `engine.py update`. Original plan text: Simulate two updates: the last 3 months of RADD (touches only park plots) and Hansen loss year 2025 as an annual release (touches plots outside the park).
5. Supplier-declaration check (confirmed 14 Oct; c2 only; cut first if late): supplier-style c2 per plot, `matches_declaration` + reason.
6. Evaluate: storage (compressed and uncompressed baselines, per km², scaled up), query time, decision/pixel fidelity per plot stratum, update vs rebuild, and a **running-cost budget** for our design.
7. Pitch deck, rehearsal, backup demo recording (Saturday).

Original day plan: Mon scope → Tue raw data + baseline → Wed layer analysis + encoding → Thu build compact format + query → Fri evaluation → Sat pitch. Currently about a day behind: data and layer analysis are done, but plots and the baseline pipeline are not started.

## Open questions

- Open spec comment: should 1–2 disturbed pixels count as "indication" (sent to review) or "no indication"? Current choice: indication + review.
- Source authority: Matthew Saw is SystemEarth's **CTO**; the earlier stack email came from their **product manager**. On technical facts, the CTO's word wins.
- Answered by Matthew Saw (CTO), 14 Oct (details in `docs/systemearth-current-setup.md`): forest analysis runs on **Earth Engine, not PostGIS**; they **re-check every past lot on update and call it inefficient**; they **do record dataset versions** (so the manifest is not a differentiator); Earth Engine is a **commercial licence, "relatively expensive"**, cost per check confidential.
- Still to ask the organisers:
  - cost of a field verification visit (the business case's value figure depends on it);
  - what happens when SystemEarth's result and the supplier's declaration disagree;
  - how often layers update / re-checks run, and roughly how many plots they hold.
- For the budget: look up Google's published Earth Engine commercial pricing (EECU-hour rates, plan fees) to estimate their cost per check, since theirs is confidential.
- Facts to verify before slides: see the checklist in `docs/business-case.md`.
- Pitch framing: the false-flag reduction comes from the decision rule, not the storage design. Sell the architecture on **cost per check (off Earth Engine) and incremental re-checks** — both pains the CTO confirmed. Reproducibility is table stakes (they already record versions), not a headline.

## Project layout

- `docs/brief.md` — the hackathon brief (from SystemEarth)
- `docs/systemearth-current-setup.md` — how SystemEarth handles data today + datasets they pointed to
- `docs/research-notes.md` — EUDR status, dataset facts, sample-data findings, pain points, Treefera, judge Q&A
- `docs/business-case.md` — why Côte d'Ivoire cocoa, who pays, measured value (23% vs 0.12% flagged), judge challenges
- `docs/decision-spec.md` — decision rules contract
- `docs/architecture.md` — target architecture, demo scope
- `data/sample/` — SystemEarth's sample (17 Thai rubber plots: CSV + GeoJSON)
- `data/raw/` — Earth Engine exports go here (not committed)
- `scripts/` — pipeline scripts
