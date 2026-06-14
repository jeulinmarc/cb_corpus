# Decisions — WP v3 (answered 2026-06-12)

Former open questions, now settled by the maintainer. This is the decision
record; the other docs reflect these choices.

## Q1 — Backfill depth → **Full history on first run**

Native scrapers crawl their complete archives on the first run (one-off
heavy crawl, listings only, ~hours per bank). Maximizes `bank_site`
day-precision from day one.

## Q2 — Fed backfill precision → **Day precision everywhere**

Month-only backfill rejected. The Fed scraper fetches the per-paper landing
page for every paper, including during backfill (~3 000 papers × 0.5 s
throttle ≈ 30 min one-off). No `month`-precision Fed rows.

## Q3 — D2 scope → **All banks, not just the five**

Occasional/discussion-paper-adjacent series (D2) are collected for **every
bank that has one**, not only ECB:

- The 5 big banks: D2 natively where the bank site lists them, RePEc
  otherwise.
- All other banks: via RePEc, like their D1 today.
- **New prerequisite task**: identify the D2 RePEc series handle per bank
  and add it to `SERIES` in `cb_corpus/sources/repec.py` (today only
  `ecbops` is wired). One-off research pass over the 13 wired banks +
  any others with a D2-like series.

## Q4 — `pending_repec` grace window → **45 days, confirmed**

## Q5 — Manifest in git → **Same repo as the code**

`data/manifest.jsonl` and `data/wp_dates_index.jsonl` committed to this
repo. Revisit (git-lfs / data repo) only if it gets heavy.

## Q6 — `repec_handle` stamping → **All wired banks**

`repec-check --write` stamps handles for every bank in `SERIES`, not just
the five.

## Q7 — Native scraper failure → **Fail loudly, no RePEc fallback**

A native D1/D2 scraper error aborts that bank/type for the run and is
reported. No silent fallback to RePEc (would reintroduce v2 date quality
unnoticed). `repec-check` remains the completeness safety net.

## Q8 — PDF tooling → **pypdf only, OCR out of scope**

Rung 3 (cover-page text) accepts a lower hit-rate on old scans rather than
adding pdfminer-six/OCR. Failure mode stays "month precision", which is
acceptable.