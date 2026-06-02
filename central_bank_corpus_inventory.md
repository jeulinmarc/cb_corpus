# Central Bank Document Corpus — Source & Document-Type Inventory

*Planning + completeness-check reference for scraping/downloading central bank PDFs.*
*Compiled June 2026. All counts are order-of-magnitude estimates unless a source is cited.*

---

## 0. Scope & headline finding (read this first)

**Scope (confirmed):** the *full* communications corpus — document types A through F in §3 — but **official primary sources only.** Every file must be the original document as published by the issuing central bank (or by the BIS, an official institution, re-hosting the original). **Excluded by rule:** any academic-curated dataset, any researcher-labeled/structured corpus, any machine-translated or model-OCR'd text. See §2 for the explicit keep/drop list. No machine translation — non-English originals are kept as-is.

**Sourcing reality.** There is **no single official source** for the full corpus:

1. **Speeches** — the BIS (official) hosts originals from ~130 central banks. Use it as a **discovery index + source of the original PDFs** (not its derived text extract).
2. **Working papers** — RePEc/IDEAS is academic *infrastructure* but stores only metadata; the PDFs it links are the official files on each bank's domain. Use it **only to discover URLs**; always fetch the PDF from the central bank's own domain.
3. **Minutes, statements, decisions, reports, projections** — not aggregated anywhere. **One scraper per central bank.**

So the architecture is: **BIS index for speeches + (optional) RePEc index for paper URLs, then per-bank adapters for everything — with every PDF pulled from an official domain.**

---

## 1. The universe (the denominator for completeness)

| Population | Count | Use as |
|---|---|---|
| BIS member central banks | 63 (≈95% of world GDP) | **Core target list** — highest priority, best-structured sites |
| Central banks that publish online (incl. speeches) | ~131 | Realistic ceiling for "actively publishing" banks |
| All monetary authorities / central banks worldwide | ~180–200 | Theoretical maximum (includes tiny/offline ones) |

**Recommendation:** define completeness against the **63 BIS members** first (tractable, high-value), then extend toward the 131 that publish online. The long tail (180+) is mostly low-volume or non-digitized.

---

## 2. Sources: official (keep) vs. derived (drop)

### 2a. KEEP — official primary sources & discovery indexes

| Source | Role | What you take | Notes |
|---|---|---|---|
| **Each central bank's own website** | **Primary source for all types A–F** | Original PDFs + the bank's own listing pages | The ground truth. One adapter per bank. |
| **BIS — bis.org/cbspeeches** | Official re-host of speeches from ~130 banks; discovery index | The **original speech PDFs** only | BIS is an official institution, not academic. Do **not** ingest its derived full-text extract — take PDFs. Noncommercial T&Cs apply. |
| **BIS — bis.org (own publications)** | Primary source | BIS working papers, bulletins, annual reports | BIS is itself an official issuer. |
| **RePEc / IDEAS (incl. NEP-CBA)** | **URL-discovery only** for working papers | Metadata + links → then fetch PDF **from the bank's domain** | Academic infrastructure, but stores no documents. Acceptable strictly as a link index; never treat its cached copies as the source. |

### 2b. Tools (allowed — these are code, not documents)
`gingado` (BIS), `bis-scraper`, and similar scrapers are **tooling** and fine to use regardless of authorship. Your exclusion rule applies to *data/documents*, not to *code*. (Note: if a tool also performs OCR or text extraction, keep its *download* function but discard its *derived text* — re-extract yourself or store raw PDFs.)

### 2c. DROP — academic-curated / model-derived (excluded by your rule)

| Excluded dataset | Why excluded |
|---|---|
| **CBS dataset** (cbspeeches.com) | Academic compilation; 5,347 entries machine-translated |
| **MPSD** (centralbanktexts.github.io) | Academic; statements reprocessed/standardized |
| **IMF WP 2025/109 dataset** | Academic + LLM-derived fields |
| **UMD thesis DB** | Academic research compilation |
| **ECB press-conference dataset** (ScienceDirect) | Academic; researcher-structured |
| **BERT-CBSI minutes corpus** | Academic + model-labeled |
| **istat-ai/ECB-FED-speeches** (Hugging Face) | Text produced by a pretrained OCR model |

> These remain useful **only** as informal cross-checks ("did we miss a speech they have?"), never as ingested data.

---

## 3. Document-type taxonomy (the completeness checklist)

Use this as the row dimension of your completeness matrix (bank × type × year). Cadence shown is the typical *publishing rate per bank that produces the type*.

### A. Monetary policy — decisions & deliberation
- **A1. Rate-decision press releases** — almost every bank; ~6–12/yr. *(Highest universality.)*
- **A2. Policy statements** — most banks; ~6–12/yr.
- **A3. Meeting minutes / "accounts" / "summary of deliberations"** — ~40–60 banks only; ~6–12/yr. Examples: Fed FOMC minutes (8/yr), ECB monetary policy accounts (8/yr, since 2015), BoE MPC minutes, BoJ minutes + Summary of Opinions, RBA minutes (~11/yr), BoC Summary of Deliberations (since 2023), Banxico, BCB Copom, RBI MPC minutes, Riksbank, Norges Bank, CNB/MNB/NBP/NBR.
- **A4. Voting records / individual votes** — subset of A3 banks (BoE, Riksbank, etc.).

### B. Press conferences & transcripts
- **B1. Press-conference transcripts / Q&A** — major banks; ~4–8/yr.
- **B2. Webcasts / opening remarks** — overlaps B1.

### C. Speeches & interviews
- **C1. Speeches** — ~130 banks; ~1,500–2,000/yr aggregate (indexed by BIS).
- **C2. Interviews / op-eds / testimony** — partially in BIS feed.

### D. Research
- **D1. Working papers** — most BIS members; tens of thousands cumulatively (RePEc-indexed).
- **D2. Occasional / discussion / staff notes / bulletins** — most large banks.
- **D3. Economic letters / blogs** (e.g. Fed "Liberty Street Economics", FEDS Notes).

### E. Reports
- **E1. Monetary policy reports / Inflation reports** — most inflation-targeters; 2–8/yr.
- **E2. Financial stability reports** — most banks; 1–2/yr.
- **E3. Annual reports** — nearly all; 1/yr.
- **E4. Economic / quarterly bulletins** — many; 4–12/yr.

### F. Projections & forecasts
- **F1. Staff economic projections / fan charts** (Fed SEP, ECB staff projections) — tied to A/E cadence.

### G. Supervisory / regulatory / statistical (optional — define if in scope)
- **G1. Regulatory notices / consultations**
- **G2. Statistical releases / data bulletins**
- **G3. Supervisory reports**

> **Decide your scope now.** "Minutes + papers" = A3 + D. A serious "central bank communications" corpus usually = A + B + C + E + F.

---

## 4. Data sources (where to actually pull from)

### Tier 1 — Official indexes (do these first, huge coverage-per-effort)
- **BIS** — bis.org/cbspeeches (→ original speech PDFs from ~130 banks), bis.org (BIS's own working papers/reports).
- **RePEc / IDEAS** — OAI-PMH harvest for **URL discovery only**; resolve each hit to the PDF on the bank's own domain.

### Tier 2 — Per-central-bank sites (one adapter each, prioritize 63 BIS members)
Each has a different URL scheme, pagination, and PDF layout — there is no shortcut. Highest-value first:
Fed (federalreserve.gov), ECB (ecb.europa.eu), BoE, BoJ, PBoC, BoC, RBA, RBI, SNB, Riksbank, Norges Bank, Bundesbank, Banque de France, Banca d'Italia, Banco de España, Banxico, BCB (Brazil), BoK (Korea), MAS, SARB, … through the full BIS-63 list.

### Tier 3 — Long tail (the remaining ~120 banks)
Lower volume, often non-English, sometimes no PDFs / scanned only. Schedule last; budget for OCR.

---

## 5. Volume assessment (your completeness yardstick)

Rough order-of-magnitude totals for the **digital era** (≈1996–2025). Use these to sanity-check download counts; if you pull 10× fewer A3 minutes than this, something is broken.

| Type | Est. producing banks | Est. cumulative documents | Confidence |
|---|---|---|---|
| A1/A2 Statements & rate decisions | ~150 | **25,000–35,000** | Low-med |
| A3 Minutes / accounts | ~40–60 | **6,000–12,000** | Low-med |
| B Press-conf transcripts | ~30 | **2,000–4,000** | Low |
| C Speeches | ~130 | **~40,000** (BIS index lists ~35k through 2023 + ~2k/yr) | **High** |
| D Working/occasional papers | ~60 | **30,000–60,000** | Med (RePEc-countable) |
| E1 MP/Inflation reports | ~80 | **3,000–6,000** | Low-med |
| E2 Financial stability reports | ~120 | **2,000–4,000** | Low-med |
| E3 Annual reports | ~180 | **3,000–5,000** | Med |

**Grand order of magnitude: ~120,000–200,000 PDFs** for a full A–F corpus across the digital era. Minutes alone ("A3"): plan for **~10,000 documents**.

> These are estimates for *planning*, not ground truth. The robust completeness method is in §6.

---

## 6. Recommended scraping approach + completeness method

### Pipeline
1. **Index** from official sources only: BIS speech listing (→ original PDFs) and, optionally, RePEc to discover working-paper URLs (→ fetch PDF from the bank's domain).
2. **Per-bank adapters** for A/B/E/F: each emits `(bank, type, date, title, source_url, pdf_url)`.
3. **Download** the original PDF — this is the canonical artifact. Run a **text-layer check**; for scanned/non-searchable files you may OCR locally for indexing, but keep OCR output as a clearly-labeled *derived* layer, never overwriting or substituting for the official PDF.
4. **Normalize metadata** to one schema; dedupe on `(bank, type, date, title-hash)` and on file hash.

### Completeness check (this is the real answer to your request)
Build an **expected-count manifest**, then diff against downloads:
- For each bank × type, derive the **expected number per year** from the bank's *meeting calendar / publication schedule* (these are themselves published — e.g. FOMC has 8 meetings/yr, so 8 minutes/yr).
- Crawl each bank's **archive/index pages** and count *listed* items (the listing is the ground truth, not your guess).
- **Completeness = downloaded ÷ listed**, per bank, per type, per year. Flag any cell < 100%.
- Cross-validate speech counts against the **BIS yearly listing counts** (official, not the academic CBS set).

This makes completeness *measured against each site's own index*, which is far more reliable than the §5 estimates.

### Operational caveats
- **Legal/ToS:** BIS content is noncommercial-use only; check each bank's terms and `robots.txt`. Rate-limit and identify your crawler (be "polite").
- **Languages:** ~15% of speeches and many statements are non-English — plan translation if you need uniform NLP.
- **Drift:** sites redesign; adapters break. Schedule re-validation.
- **Minutes ≠ universal:** many banks never publish minutes — don't treat their absence as a scraping failure.

---

## 7. Suggested next step
Pick scope (minimal = A3 + D; full = A–F), confirm the target bank list (start at BIS-63), and I can (a) wire up the BIS speech index + RePEc URL discovery, and (b) scaffold the per-bank adapter framework + the expected-vs-downloaded completeness matrix.
