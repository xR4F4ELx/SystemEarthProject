# Business case: EUDR verification for Côte d'Ivoire cocoa

14 Oct 2026. Written for the organisers' "business case + hackathon" judging (see `docs/brief.md`).

## In one sentence

Côte d'Ivoire is where EUDR compliance is hardest, highest-volume and most exposed to the EU, and the usual way of checking plots there gets it wrong at scale. SystemEarth sells verification, so a cheaper, faster and reproducible verdict per plot is directly worth money to them and their customers.

## 1. Why there is a business need

| Fact | Why it creates demand | Confidence |
|---|---|---|
| Côte d'Ivoire grows about 40% of the world's cocoa | Every large chocolate maker and grinder buys from it | Widely cited; vendor source |
| About 60% of its cocoa goes to the EU (the EU's own 2023 factsheet says 59%) | EUDR applies to most of the crop | EU source |
| Classed as **standard risk** in the EUDR country benchmarking | No simplified due diligence: every plot needs a geolocated, plot-level check | Secondary sources; **verify** in Implementing Regulation (EU) 2025/1093 |
| Obligations apply from **30 Dec 2026** (large operators), 30 Jun 2027 (micro and small) | Customers need this now | See `docs/research-notes.md` §1 |
| Cocoa is grown by a very large number of smallholders on small farms | Hundreds of thousands of plots, each re-checked as new alerts arrive. Volume is the cost problem | Commonly cited around 1 million farmers; **verify** |
| Fines up to at least 4% of EU turnover, plus confiscation and market exclusion (Article 25, from memory) | A wrong "compliant" is very expensive for the EU buyer | **Verify** against the regulation text |

## 2. What makes Côte d'Ivoire hard (measured in our AOI)

AOI: 6.6–7.1°W, 5.6–6.05°N, eastern edge of Taï National Park towards Soubré. Data: GFC2020 v4, Hansen v1.13, RADD (`africa_20261007`), WDPA, exported 13 Oct 2026.

| Trap | What it does | What we measured |
|---|---|---|
| **Tree crops look like deforestation** | Cocoa, rubber and palm replanting, and fallow clearing, show up as Hansen tree cover loss | 99.5% of post-2020 Hansen loss outside protected areas is on land that was not forest in 2020 |
| **Forest is mostly gone outside parks** | The decision hinges on a precise 2020 forest baseline | Only 0.3% of unprotected land was forest in 2020; Taï NP is 97% forest |
| **Farms inside protected areas** | Legality risk that forest layers cannot see | Both classified forests in the AOI (No. 62, Nibi Hana) are about 0% forest: legally restricted, fully converted |
| **Shade cocoa looks like forest** | Risk of wrongly flagging good farms (GFC2020 v2: 18% forest commission error) | Not measured; uncertain plots go to review |
| **Small plots** | One 10 m pixel is a large share of a 1 ha farm, so edges matter | Pixel-centre rule fixed in `docs/decision-spec.md`; edge cases go to review |

## 3. Who has the problem, and where SystemEarth fits

Flow: **farmer → cooperative or exporter (submits GeoJSON + due-diligence CSV) → SystemEarth verifies the declaration → EU operator files its due-diligence statement → authority may audit years later.**

| Party | Their pain | What our design gives them |
|---|---|---|
| Supplier / cooperative | Wrong flags mean field visits and lost sales | The 2020 baseline removes most false flags |
| SystemEarth | Every check costs compute; many lots to re-check | Fast local checks; re-check only affected plots |
| EU operator | Must defend each plot's verdict | Verdict plus evidence plus data version |
| Competent authority | Needs to reproduce old decisions | Manifest per verdict |

## 4. The value in numbers

5,000 random 2 ha circular plots on unprotected land in the AOI:

| Method | Plots flagged | Per 10,000 plots |
|---|---|---|
| Hansen loss only, 2021–2025 (supplier-style) | 22.9% | ≈ 2,290 flags |
| Hansen loss only, 2021–2023 (the sample supplier's window) | 13.1% | ≈ 1,310 flags |
| Gated by 2020 forest baseline, Hansen + RADD (our rule) | 0.12% | ≈ 12 flags |

**Value formula:** field visits avoided ≈ plots × (Hansen-only flag rate − gated flag rate) × cost per field visit.

The cost per field visit is unknown; ask the organisers. Do not invent one. If a figure is needed, show a range labelled as an assumption.

Caveats to state out loud:

- **Random plots are not real farms.** Real cocoa clusters near forest edges, so the real rate will differ. Present it as the order of magnitude for this landscape.
- **"Removed" flags are not proven false.** GFC2020 misses some real forest (8% omission in v2); we have no field data to check.
- **The flag reduction comes from the decision rule, not the storage design.** SystemEarth could adopt the rule in their current stack. The architecture's own value is cost per check, update cost and audit-proof records, shown in the budget (evaluation step).

## 5. Where a skeptical judge will push

| Challenge | Answer |
|---|---|
| "Big traders already map their supply chains." | They map plots; checking and re-checking verdicts as data changes is the recurring cost. That is SystemEarth's service. |
| "Free tools exist (FAO Whisp, Global Forest Watch)." | They give risk indicators. SystemEarth needs a fixed, reproducible verdict per plot at scale, tied to a data version for audit. |
| "Côte d'Ivoire has its own national traceability system." | It identifies farmers and farms. A verdict still has to be produced and defended per plot, and EU operators remain liable. Check the system's current status before claiming more. |
| "Why not Ghana or Indonesia?" | The design is region-agnostic. Côte d'Ivoire is the largest volume with the hardest traps, so it is the strongest stress test. |
| "Your sample customer is Thai rubber." | That is SystemEarth's current case; Côte d'Ivoire cocoa is an expansion market. Same output schema, same mechanism (tree-crop rotation reads as loss). |

## To verify before slides

- [ ] Côte d'Ivoire's risk class in Implementing Regulation (EU) 2025/1093 (EUR-Lex annex)
- [ ] Number of cocoa farmers and typical farm size (ICCO or Conseil du Café-Cacao)
- [ ] Article 25 penalty wording (Regulation (EU) 2023/1115)
- [ ] Cost of a field verification visit (ask organisers)
- [ ] Current status of Côte d'Ivoire's national cocoa traceability system

## Sources

- [EU EUDR cocoa factsheet, Côte d'Ivoire (2023)](https://international-partnerships.ec.europa.eu/system/files/2023-06/EUDR%20Cocoa%20factsheet_CDI_EN.pdf)
- [Preferred by Nature: cocoa due diligence in West and Central Africa](https://www.preferredbynature.org/projects/developing-legal-frameworks-and-due-diligence-guidelines-cocoa-production-west-and-central)
- [TraceX: EUDR benchmarking (vendor blog)](https://tracextech.com/?p=28016)
- [Global Forest Watch Pro: cocoa risk map](https://pro-news.globalforestwatch.org/news-events/new-cocoa-risk-map-on-gfw-pro)
- AOI measurements: computed from `data/raw/` exports (manifest `aoi-ci_tai_soubre-2026-10-13`)
