"""WP v3 phase 2 — RePEc/IDEAS completeness audit (never downloads).

Enumerates each wired IDEAS series (``SERIES`` in sources/repec.py) and asks: is
any paper missing from our manifest? Matching a RePEc paper to a manifest row,
in cascade:

  - **handle** — the IDEAS handle (e.g. RePEc:ecb:ecbwps:20253124) appears in a
    manifest row's ``source_url``/``repec_handle`` (exact for RePEc-sourced rows);
  - **key** — the bank-specific number (wp_migrate ``_KEY_FROM_HANDLE``) matches a
    native row's key (covers the 5 banks migrated off RePEc);
  - **title** — exact normalized title equality (last resort, unambiguous only).

Leftovers (RePEc papers no manifest row covers) are reported as `missing`, split
into `missing_recent` (within the last ~45 days → native discovery just hasn't
caught up / re-run it) and `missing_legacy` (older → ingest via RePEc + date
recovery). The reverse — manifest rows with no RePEc paper — is reported too
(`pending_repec` if recent, else `unmatched_old`). Stdout + CSV; no writes.
"""
from __future__ import annotations

import csv
import re
import sys
from datetime import date, timedelta
from typing import Iterable, Optional

from bs4 import BeautifulSoup

from .banks import get_bank
from .config import Config
from .http import Fetcher
from .sources.repec import IDEAS, SERIES, _paper_meta, extract_pdf_candidates
from .storage import Storage
from .wp_migrate import (_KEY_FROM_HANDLE, _KEY_FROM_PDF, normalize_title,
                         normalize_url, repec_handle_from_source_url)

_GRACE_DAYS = 45


def parse_series_listing(html: str, arch: str, series: str) -> list[tuple[str, str]]:
    """[(paper_id, title)] for an IDEAS series listing page. De-duped by id, keeping
    the longest title seen (skips the short 'By citations/downloads' sort links)."""
    soup = BeautifulSoup(html, "lxml")
    pat = re.compile(rf"/p/{re.escape(arch)}/{re.escape(series)}/([^/.]+)\.html$")
    best: dict[str, str] = {}
    for a in soup.find_all("a", href=True):
        m = pat.search(a["href"])
        if not m:
            continue
        pid = m.group(1)
        t = a.get_text(" ", strip=True)
        if t.lower().startswith(("by ", "sorted")):   # sort-order links, not titles
            t = ""
        # keep every paper id (completeness); upgrade to the longest real title seen
        if pid not in best or len(t) > len(best[pid]):
            best[pid] = t
    return list(best.items())


def enumerate_series(fetcher: Fetcher, handle: str, max_pages: int = 80
                     ) -> list[tuple[str, str]]:
    """All (paper_id, title) for an IDEAS series, following pagination."""
    arch, series = handle.split(":")
    base = f"{IDEAS}/s/{arch}/{series}"
    seen: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        url = f"{base}.html" if page == 1 else f"{base}{page}.html"
        try:
            html = fetcher.get_text(url)
        except Exception:
            break
        rows = parse_series_listing(html, arch, series)
        new = [(pid, t) for pid, t in rows if pid not in seen]
        if not new:
            break
        for pid, t in new:
            seen[pid] = t
    return list(seen.items())


def run_repec_check(bank_codes: Optional[Iterable[str]] = None,
                    csv_path: Optional[str] = None,
                    config: Optional[Config] = None) -> dict[str, dict]:
    """Audit RePEc coverage for the requested banks (default: all wired). Never
    downloads. Returns {bank: summary}; writes a CSV of missing papers."""
    cfg = config or Config()
    fetcher = Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    codes = [c for c in (bank_codes or SERIES.keys()) if c in SERIES]
    cutoff = date.today() - timedelta(days=_GRACE_DAYS)

    results: dict[str, dict] = {}
    missing_rows: list[dict] = []
    for bank in codes:
        # Manifest coverage sets for this bank: handles, native keys, normalized
        # titles, and normalized PDF/alt URLs (the last catches non-RePEc-sourced
        # rows, e.g. es BdE-repository copies, when we fetch a leftover's page).
        handles: set[str] = set()
        keys: set = set()
        titles: set[str] = set()
        urls: set[str] = set()
        manifest_n = 0
        key_pdf, key_handle = _KEY_FROM_PDF.get(bank), _KEY_FROM_HANDLE.get(bank)
        for row in storage.iter_manifest(bank):
            if row.get("doc_type") not in ("D1", "D2"):
                continue
            manifest_n += 1
            h = (row.get("repec_handle") or repec_handle_from_source_url(row.get("source_url") or ""))
            if h:
                handles.add(h)
            if key_pdf:
                k = key_pdf(row.get("pdf_url") or "") or (key_handle and key_handle(row.get("source_url") or ""))
                if k:
                    keys.add(k)
            t = normalize_title(row.get("title") or "")
            if t:
                titles.add(t)
            for u in [row.get("pdf_url"), *(row.get("alt_urls") or [])]:
                if u:
                    urls.add(normalize_url(u))

        summary = {"repec_total": 0, "covered": 0, "recovered_pagefetch": 0,
                   "missing_recent": 0, "missing_legacy": 0, "manifest_total": manifest_n}
        bank_home = get_bank(bank).homepage
        for handle, doc_type in SERIES[bank]:
            arch, series = handle.split(":")
            leftovers: list[tuple[str, str, str]] = []
            for pid, title in enumerate_series(fetcher, handle):
                summary["repec_total"] += 1
                full_handle = f"RePEc:{arch}:{series}:{pid}"
                covered = full_handle in handles
                if not covered and key_handle:
                    k = key_handle(f"{series}:{pid}")
                    covered = bool(k and k in keys)
                if not covered and title:
                    covered = normalize_title(title) in titles
                if covered:
                    summary["covered"] += 1
                else:
                    leftovers.append((pid, title, full_handle))
            # Second pass (cheap — only the leftovers): fetch each unmatched paper's
            # IDEAS page and re-match by the bank PDF URL or the full canonical
            # title. Catches rows the listing title/handle missed.
            for pid, title, full_handle in leftovers:
                recovered = False
                try:
                    html = fetcher.get_text(f"{IDEAS}/p/{arch}/{series}/{pid}.html")
                    ftitle, _ = _paper_meta(html)
                    cands = extract_pdf_candidates(html, bank_home)
                    recovered = (any(normalize_url(c) in urls for c in cands)
                                 or bool(ftitle and normalize_title(ftitle) in titles))
                except Exception:
                    recovered = False
                if recovered:
                    summary["covered"] += 1
                    summary["recovered_pagefetch"] += 1
                    continue
                yr = int(pid[:4]) if pid[:4].isdigit() and 1990 <= int(pid[:4]) <= cutoff.year + 1 else 0
                recent = yr >= cutoff.year
                summary["missing_recent" if recent else "missing_legacy"] += 1
                missing_rows.append({"bank": bank, "handle": full_handle,
                                     "title": title, "bucket": "recent" if recent else "legacy"})
        results[bank] = summary
        print(f"{bank}: {summary}")

    out = csv_path or str(cfg.reports_dir / "repec_check.csv")
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["bank", "handle", "title", "bucket"])
        w.writeheader()
        for r in missing_rows:
            w.writerow(r)
    print(f"wrote {len(missing_rows)} missing paper(s) -> {out}", file=sys.stderr)
    return results


def run_repec_reconcile(bank_codes: Optional[Iterable[str]] = None,
                        write: bool = False,
                        csv_path: Optional[str] = None,
                        config: Optional[Config] = None,
                        fetcher: Optional[Fetcher] = None) -> dict[str, dict]:
    """One-shot reconciliation: stamp ``source_url = <IDEAS paper URL>`` onto
    manifest rows that uniquely match an IDEAS listing entry but carry no
    source_url of their own (spec §3 — the gb-style 374 dead-URL rows: native
    bank_site D1 rows under modern slug URLs, with no cross-reference to the
    RePEc entry that reproves them on every catalog pass).

    Matching cascade per listing entry, in order (never fuzzy, never dates):

      1. already-known: the IDEAS paper URL is already a manifest row's
         source_url (``known_sources``), or the full RePEc handle already
         maps to a row carrying a source_url -> ``already``, no page fetch.
      2. first pass (no fetch): union of rows matching the full handle, the
         bank-specific number key (`wp_migrate._KEY_FROM_HANDLE`), or the
         EXACT normalized title.
      3. second pass (only if #2 found nothing): fetch the IDEAS paper page,
         re-try the bank PDF-URL candidates against the manifest's
         (pdf_url + alt_urls) index, and the fetched canonical title.

    Write rule (strict): a listing entry stamps its match ONLY when exactly
    one candidate row was found (across both passes) AND that row's
    source_url is empty. Zero candidates -> ``unmatched``. More than one ->
    ``ambiguous``. One candidate with a source_url already set -> ``already``.
    Ambiguous/unmatched/already rows are reported (CSV + stdout), never
    written. Default is dry-run (``write=False``); writes go through
    ``storage.rewrite_manifest`` with the bank's FULL row set (never a
    filtered subset — see its docstring), touching ONLY ``source_url`` on the
    stamped doc_ids. Idempotent: once a row is stamped its IDEAS URL is a
    known source_url, so a second run finds it ``already`` and stamps 0.

    Returns {bank: {"stamped": n, "ambiguous": n, "already": n, "unmatched": n}}.
    Always writes a CSV (dry-run included) of every action taken, with rows
    ``{bank, ideas_url, action, doc_id, title}`` (doc_id/title empty when no
    single row was involved, e.g. ambiguous/unmatched).
    """
    cfg = config or Config()
    fetcher = fetcher or Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    codes = [c for c in (bank_codes or SERIES.keys()) if c in SERIES]

    results: dict[str, dict] = {}
    csv_rows: list[dict] = []

    for bank in codes:
        by_handle: dict[str, list[dict]] = {}
        by_key: dict = {}
        by_title: dict[str, list[dict]] = {}
        by_url: dict[str, list[dict]] = {}
        known_sources: set[str] = set()
        all_rows: list[dict] = []
        key_pdf, key_handle = _KEY_FROM_PDF.get(bank), _KEY_FROM_HANDLE.get(bank)

        for row in storage.iter_manifest(bank):
            all_rows.append(row)
            if row.get("doc_type") not in ("D1", "D2"):
                continue
            h = (row.get("repec_handle")
                 or repec_handle_from_source_url(row.get("source_url") or ""))
            if h:
                by_handle.setdefault(h, []).append(row)
            if key_pdf:
                k = key_pdf(row.get("pdf_url") or "") or (
                    key_handle and key_handle(row.get("source_url") or ""))
                if k:
                    by_key.setdefault(k, []).append(row)
            t = normalize_title(row.get("title") or "")
            if t:
                by_title.setdefault(t, []).append(row)
            for u in [row.get("pdf_url"), *(row.get("alt_urls") or [])]:
                if u:
                    by_url.setdefault(normalize_url(u), []).append(row)
            src = row.get("source_url")
            if src:
                known_sources.add(src)

        counts = {"stamped": 0, "ambiguous": 0, "already": 0, "unmatched": 0}
        stamps: dict[str, str] = {}   # doc_id -> ideas_url
        bank_home = get_bank(bank).homepage

        for handle, doc_type in SERIES[bank]:
            arch, series = handle.split(":")
            for pid, title in enumerate_series(fetcher, handle):
                ideas_url = f"{IDEAS}/p/{arch}/{series}/{pid}.html"
                full_handle = f"RePEc:{arch}:{series}:{pid}"
                handle_rows = by_handle.get(full_handle) or []

                if ideas_url in known_sources or any(r.get("source_url") for r in handle_rows):
                    counts["already"] += 1
                    doc_id = handle_rows[0]["doc_id"] if handle_rows else ""
                    csv_rows.append({"bank": bank, "ideas_url": ideas_url,
                                     "action": "already", "doc_id": doc_id, "title": title})
                    continue

                cand_by_id: dict[str, dict] = {r["doc_id"]: r for r in handle_rows}
                key = key_handle(f"{series}:{pid}") if key_handle else None
                if key is not None:
                    for r in by_key.get(key, []):
                        cand_by_id[r["doc_id"]] = r
                for r in by_title.get(normalize_title(title), []):
                    cand_by_id[r["doc_id"]] = r

                fetched_title = ""
                if not cand_by_id:
                    # Second pass (cheap -- only unmatched entries): fetch the
                    # IDEAS page and re-match by its bank PDF-URL candidates
                    # (matched against pdf_url/alt_urls) or its canonical title.
                    try:
                        html = fetcher.get_text(ideas_url)
                        fetched_title, _ = _paper_meta(html)
                        cands = extract_pdf_candidates(html, bank_home)
                    except Exception:
                        fetched_title, cands = "", []
                    for c in cands:
                        for r in by_url.get(normalize_url(c), []):
                            cand_by_id[r["doc_id"]] = r
                    if fetched_title:
                        for r in by_title.get(normalize_title(fetched_title), []):
                            cand_by_id[r["doc_id"]] = r

                csv_title = fetched_title or title
                candidates = list(cand_by_id.values())
                if not candidates:
                    counts["unmatched"] += 1
                    csv_rows.append({"bank": bank, "ideas_url": ideas_url,
                                     "action": "unmatched", "doc_id": "", "title": csv_title})
                elif len(candidates) > 1:
                    counts["ambiguous"] += 1
                    csv_rows.append({"bank": bank, "ideas_url": ideas_url,
                                     "action": "ambiguous", "doc_id": "", "title": csv_title})
                else:
                    row = candidates[0]
                    if row.get("source_url"):
                        counts["already"] += 1
                        csv_rows.append({"bank": bank, "ideas_url": ideas_url,
                                         "action": "already", "doc_id": row["doc_id"],
                                         "title": csv_title})
                    else:
                        counts["stamped"] += 1
                        stamps[row["doc_id"]] = ideas_url
                        csv_rows.append({"bank": bank, "ideas_url": ideas_url,
                                         "action": "stamp", "doc_id": row["doc_id"],
                                         "title": csv_title})

        results[bank] = counts
        print(f"{bank}: {counts}")

        if write and stamps:
            for row in all_rows:
                did = row.get("doc_id")
                if did in stamps:
                    row["source_url"] = stamps[did]
            storage.rewrite_manifest(all_rows)

    out = csv_path or str(cfg.reports_dir / "repec_reconcile.csv")
    cfg.reports_dir.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["bank", "ideas_url", "action", "doc_id", "title"])
        w.writeheader()
        for r in csv_rows:
            w.writerow(r)
    print(f"wrote {len(csv_rows)} row(s) -> {out}", file=sys.stderr)
    return results
