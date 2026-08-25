# PR #13 follow-up minors — design spec

**Status: DRAFT — awaiting Marc's validation.**

Four Minor findings from PR #13's final whole-branch review, none behavioral
regressions, all small hardening/polish on `cb_corpus/recover.py` +
`cb_corpus/storage.py`. One PR.

## 1. Crash-window between quarantine release and alt_urls stamping

Today: `record_success(dead_url)` is durable per-entry during the pass loop,
while stamps apply only at end-of-pass (`_apply_stamps`). A SIGKILL/ENOSPC
mid-pass leaves processed URLs released-but-unstamped → the nightly sync
re-attempts them until quarantine re-accumulates or a later recover pass
re-stamps.

**Fix**: wrap each pass's main loop in `try/finally` with `_apply_stamps` in
the `finally` — stamps collected before the crash point are persisted even on
abrupt exit. (CSV writing stays outside: a torn CSV is a report, not state.)

## 2. Candidates-mode re-run reports `duplicate` instead of `converged`

The CDX path checks `_is_converged` per entry; the candidates pass does not,
so re-running a candidates file over an already-healed corpus reports
`duplicate` (idempotent but mislabeled — "nothing left to recover" vs "the
corpus already has it" are different truths).

**Fix**: in `_run_candidates_pass`, after matching the candidate to its audit
entry, short-circuit with `_is_converged(storage, entry)` → action
`converged`, counted in the summary, CSV row action `converged` (no stamping,
no quarantine call — converged means the index already knows every URL).

## 3. Honest stamp log line

`[recover] stamped {n} alt_url(s) on {len(stamps)} row(s)` counts *collected
doc_ids*, not rows actually modified — an idempotent re-run can print
"stamped 0 alt_url(s) on 1 row(s)".

**Fix**: `Storage.stamp_alt_urls` returns `(urls_stamped, rows_modified)`
(tuple; the API is one day old, no external consumers). Log both honestly:
`stamped {u} alt_url(s) on {r} row(s)`.

## 4. Deterministic alt_urls append order

The candidates duplicate-content site collects `{dead_url, final_url}` as a
set → append order into `alt_urls` varies across runs (hash randomization),
potential pointless diff churn in autocommitted manifests.

**Fix**: ordered list `[dead_url, final_url]` (dead first — provenance
history before live alternative), dedup preserving order.

## Tests

- 1: pass raises mid-loop (monkeypatched save that throws after entry 1) →
  entry 1's stamp is still applied.
- 2: candidates re-run over healed corpus → summary/CSV say `converged`,
  quarantine file unchanged.
- 3: idempotent re-run logs `0 alt_url(s) on 0 row(s)`; unit on the tuple
  return.
- 4: alt_urls order `[dead, final]` asserted exactly.

Suite green: `python3.13 -m pytest tests/ -q`. English only, no new deps.
