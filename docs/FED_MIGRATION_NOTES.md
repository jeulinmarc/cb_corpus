# Fed (FEDS + IFDP) native migration — issues & analysis

Notes from implementing v3 native discovery for the Federal Reserve (`us`, D1).
Written after the migration ran (2026-06-14). This is the honest post-mortem the
ECB doc didn't need — the Fed is materially messier than ECB, and the numbers
below are lower than a naive reading of the plan would suggest. My view on each
issue is called out explicitly.

## Outcome (what landed)

- **3455 / 4188** `us` D1 rows matched a bank-site record and were migrated
  (`date_source="bank_site"`): **964 day-precision**, **2491 confirmed-month**.
- **733 unmatched** rows kept their old date (RePEc/legacy) → date-recovery later.
- `doc_id` / `sha256` / `local_path` / `pdf_url` untouched; row count unchanged.
- Flip live-verified: a `--since 2026` discover yields only ~6 new papers, not
  the whole series → zero re-download.

The headline: **only ~28 % of matched Fed papers gained a real day**, vs ~99 %
for ECB. That is not a bug — it is what the Fed actually publishes (see below).

## The source reality (vs the design doc)

The plan assumed FEDS/IFDP year pages give the month and a per-paper landing page
gives the day. That much is true, but the details that actually matter were all
wrong or unstated:

1. **The day is only on the landing page**, as a meta tag
   `<meta name="citation_publication_date" content="MM-DD-YYYY">`. There is no
   bulk feed: the RSS (`/feeds/working_papers.xml`) holds only ~15 recent items
   and links title-slug pages, not numbered PDFs. So getting the day for the
   back-catalogue means **one HTTP request per paper** (~4500 total, ~40 min).
2. **The listing gives month only** (`<time datetime="December 2025">`).
3. **IFDP PDFs are `/econres/ifdp/files/ifdp{seq}.pdf`**, not `…pap.pdf` like
   FEDS. My first regex silently dropped *all* IFDP papers (0 of ~1400) until a
   live run caught it.
4. **Revised papers carry an `r<N>` infix**: `…/files/2025101r1pap.pdf`. The
   number is unchanged, but the naive regex returned no key → those papers went
   unmatched. ~5 of every 110 FEDS are revisions.
5. **`citation_publication_date` is the *current version's* date.** For a revised
   paper it is the revision date, which can be months or years after the
   original (e.g. a 2022 paper showing `08-13-2024`).
6. **Pre-~2015 landings carry `MM-01-YYYY`** — i.e. month only, day unknown,
   stored as the 1st.

> **My view:** the Fed exposes *less* structured data than ECB. ECB has a single
> JSON database (foedb) with the real publication instant for everything; the Fed
> has HTML + a per-paper meta tag that conflates publication and revision. The
> per-paper crawl is the price of day precision here, and it only pays off for
> recent papers. This is inherent to the source, not something to engineer away.

## The four bugs I hit (all caught by live runs, not unit tests)

| # | Symptom | Cause | Fix |
|---|---|---|---|
| 1 | 0 IFDP papers discovered | IFDP PDF is `files/ifdp{seq}.pdf`, regex wanted `pap.pdf` | broaden `_PDF_HREF` + `_FILES_IFDP_RE` |
| 2 | ~recent papers unmatched | revision infix `…NNNr1pap.pdf` | allow `(?:r\d+)?` in the number regexes |
| 3 | month-precision papers mislabeled `day` | `apply_change` hardcoded `date_precision="day"` (fine for ECB, wrong for Fed) | carry the native record's precision through the change |
| 4 | 289 rows with a stale `year` field | a year-shifted date didn't update the derived `year` | set `year` from the new date in `apply_change` |

> **My view:** every one of these was invisible to unit tests with synthetic
> fixtures and only surfaced against the live site. The "verify before you write"
> discipline (validate on one year, audit the full dry-run, diff pre/post) is
> what caught them. Bug #3 is the scary one — it would have asserted false day
> precision on ~2500 papers, silently degrading exactly the metadata this project
> exists to get right.

## Design decisions & my view

### Month constraint (revisions)
The listing month is treated as authoritative for the *original* publication. We
keep the landing **day** only when its (year, month) equals the listing's;
otherwise the paper stays at month precision (listing month). So a revised paper
does **not** get stamped with its revision day.

> **My view:** correct and conservative. The alternative — trusting
> `citation_publication_date` blindly — would systematically replace original
> publication dates with revision dates, which is worse than month precision. The
> cost is that genuinely-revised papers lose their (unknown) original day, but
> that day isn't recoverable from the Fed site anyway. Wayback/PDF-meta recovery
> could reclaim some of these later.

### Bank overrides RePEc's month (2249 rows changed month)
2249 migrated rows landed in a *different month* than their old RePEc date. This
looks alarming but is the whole point of v3: the bank site is the primary source,
RePEc is demoted. Audit: of 1110 rows whose old date had a real day, **1029
(93 %) had a year that disagreed with their own URL/handle** — i.e. the old date
was garbage (a 2000 IFDP was dated `2019-12-10`). The migration *fixed* these.

> **My view:** this is a net data-quality win, and the audit backs it up. The
> ~81 ambiguous cases (old day, plausible year, now overwritten) are a rounding
> error and the bank is still the more authoritative source. I'm comfortable. If
> we ever want to be paranoid, we could log any row where a *non-garbage* day was
> replaced and eyeball it — but I don't think it's worth it.

### The 733 unmatched
~346 are FRASER-hosted (`fraser.stlouisfed.org`) rows whose RePEc record never
pointed at a federalreserve.gov PDF, plus pre-1996 FEDS / very old IFDP that the
econres listings don't surface in a parseable form. These keep their RePEc month
and are flagged for the date-recovery waterfall.

> **My view:** acceptable and expected ("1990s papers mostly stay at month
> precision" — the plan said as much). FRASER rows are the most fixable tail: a
> small follow-up could match them by normalized title against the native set,
> but title matching is report-only by rule, so I left it out of the automated
> path. Not worth blocking on.

### Day-precision yield (964 / 3455 = 28 %)
Low compared to ECB because pre-~2015 Fed landings only assert a month. This is
honest: we label month precision rather than fake a day.

> **My view:** the right outcome. Inflating these to "day" (e.g. by trusting the
> `-01`) would be dishonest. Recent papers (2015+) get day precision reliably,
> which is where day precision actually matters for downstream time-series work.

## Cost & operational notes

- Full migration crawl ≈ **40 min** (one landing fetch per paper, 0.5 s throttle).
  Ran it as a background job. **Ongoing discovery must use `--since`** so the
  daily cron only fetches recent landings (dozens, not thousands).
- The crawl is **idempotent** and the manifest is now **git-tracked per bank**
  (`data/manifest/us.jsonl`), so the migration is fully reversible
  (`git checkout`) and there's a `us.jsonl.pre-fed.bak` belt-and-suspenders copy.
- No skip-before-fetch optimization yet: a full (no-`--since`) re-discover still
  fetches every landing before the dedup filter drops known papers. Harmless
  (no re-download) but wasteful; only matters if someone runs a full crawl, which
  the cron shouldn't. Could add a landing-URL skip later if it becomes a problem.

## Bottom line

The Fed migration is a clear improvement (true bank-sourced dates, garbage
corrected, day precision where it exists) but it is **not** the clean ~100 %
day-precision story ECB was. The gap is the source's, not the code's. The four
bugs are all fixed and tested; the residual month/legacy buckets are honest and
have a defined path forward (date recovery). I'd ship it.
