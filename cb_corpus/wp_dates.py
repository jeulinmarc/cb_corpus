"""WP v3 phase 4 — recover the publication DAY for legacy month/year-precision
working papers, replicably.

For rows that aren't already at day precision (RePEc-dated legacy, or bank-site
rows the publisher only dates to the month), try in order, stopping at the first
hit that satisfies the **month constraint** (recovered day must fall in the row's
asserted YYYY-MM, and not be the 1st):

  0. committed index  — `data/wp_dates_index.jsonl` short-circuits everything
                        (run-once, replay-forever; no network on a re-build)
  1. PDF `/CreationDate` — read from the on-disk file (zero network)
  2. Wayback first capture of the PDF URL (then the abs/landing variant)

Hits from rungs 1-2 are appended to the committed index with an `evidence_url`,
so a fresh clone replays the exact same dates without re-crawling. Dates that
don't satisfy the month constraint are rejected — the row keeps month precision
rather than being moved to a different month (e.g. a 2009 digitisation snapshot
of a 1975 paper, or a revision-era PDF CreationDate).
"""
from __future__ import annotations

import csv
import json
import re
import sys
from datetime import date
from typing import Iterable, Optional

from .config import Config
from .http import Fetcher
from .sources.wayback import first_capture
from .storage import Storage, write_per_bank
from .wp_migrate import normalize_title, repec_handle_from_source_url

_LEGACY_BANKS = ("ecb", "us", "jp", "gb", "de")


def index_path(cfg: Config):
    return cfg.data_dir / "wp_dates_index.jsonl"


def load_index(cfg: Config) -> dict:
    """key -> entry. Key = RePEc handle when known, else 't:'+normalized title."""
    p = index_path(cfg)
    idx: dict = {}
    if p.exists():
        with p.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    e = json.loads(line)
                    idx[e["key"]] = e
    return idx


def row_key(row: dict) -> Optional[str]:
    """Stable join key for the index: the RePEc handle if derivable, else the
    normalized title (prefixed 't:')."""
    h = repec_handle_from_source_url(row.get("source_url") or "") or (row.get("repec_handle") or "")
    if h:
        return h
    t = normalize_title(row.get("title") or "")
    return "t:" + t if t else None


def _row_ym(row: dict) -> Optional[tuple[int, int]]:
    d = row.get("date") or ""
    return (int(d[:4]), int(d[5:7])) if len(d) >= 7 and d[:4].isdigit() else None


def month_ok(cand: Optional[date], row: dict) -> bool:
    """Candidate day is in the row's asserted YYYY-MM and not the 1st."""
    ym = _row_ym(row)
    return bool(cand and ym and (cand.year, cand.month) == ym and cand.day != 1)


def _ts_date(ts: Optional[str]) -> Optional[date]:
    """Wayback timestamp YYYYMMDD... -> date."""
    try:
        return date(int(ts[:4]), int(ts[4:6]), int(ts[6:8]))
    except (TypeError, ValueError):
        return None


def pdf_creation_date(local_path: Optional[str]) -> Optional[date]:
    """The PDF's /CreationDate day (raw Info dict), else None. No pypdf needed."""
    if not local_path:
        return None
    try:
        with open(local_path, "rb") as fh:
            blob = fh.read()
    except OSError:
        return None
    m = re.search(rb"/CreationDate\s*\(D:(\d{8})", blob)
    if not m:
        return None
    s = m.group(1).decode()
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def _abs_variant(url: Optional[str]) -> Optional[str]:
    """The Fed PDF<->abstract sibling next to `url` (…NNNpap.pdf <-> …NNNabs.html)."""
    if url and url.endswith("pap.pdf"):
        return url[:-len("pap.pdf")] + "abs.html"
    if url and url.endswith("abs.html"):
        return url[:-len("abs.html")] + "pap.pdf"
    return None


def _bare(url: str) -> str:
    return re.sub(r"^https?://", "", url or "")


def _url_variants(row: dict) -> list[str]:
    """Distinct URLs worth querying Wayback for: the pdf_url, its abs/pdf sibling,
    and any alt_urls (e.g. the native bank URL registered during migration)."""
    out: list[str] = []
    for u in [row.get("pdf_url"), *(row.get("alt_urls") or [])]:
        if not u:
            continue
        out.append(u)
        sib = _abs_variant(u)
        if sib:
            out.append(sib)
    return list(dict.fromkeys(out))


def recover(fetcher: Fetcher, row: dict, use_wayback: bool = True) -> Optional[dict]:
    """Recover a day for one legacy row → {date, date_source, evidence_url} | None.

    On-disk PDF /CreationDate first (free), then (unless `use_wayback` is False)
    the Wayback first-capture of each URL variant (PDF, its abs/pdf sibling,
    alt_urls). Every candidate must pass the month constraint.
    """
    cd = pdf_creation_date(row.get("local_path"))
    if month_ok(cd, row):
        return {"date": cd.isoformat(), "date_source": "pdf_meta",
                "evidence_url": f"file://{row.get('local_path')}"}
    if not use_wayback:
        return None
    for url in _url_variants(row):
        ts = first_capture(fetcher, _bare(url))
        wd = _ts_date(ts)
        if month_ok(wd, row):
            return {"date": wd.isoformat(), "date_source": "wayback",
                    "evidence_url": f"https://web.archive.org/web/{ts}/{url}"}
    return None


def run_wp_dates(bank_codes: Optional[Iterable[str]] = None,
                 write: bool = False, csv_path: Optional[str] = None,
                 since_year: int = 1997, use_wayback: bool = True,
                 config: Optional[Config] = None) -> dict:
    """Recover days for non-day D1/D2 rows of the requested banks (default: the 5
    natively-covered banks). Default is a dry-run report; `write` rewrites the
    matched manifest rows (date/date_precision=day/date_source) and appends new
    resolutions to the committed index. `since_year` skips Wayback for very old
    rows whose first capture is always a later-era digitisation (still tried via
    PDF meta). Idempotent: day rows are skipped; index entries short-circuit."""
    cfg = config or Config()
    fetcher = Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    codes = set(bank_codes) if bank_codes else set(_LEGACY_BANKS)
    idx = load_index(cfg)
    # Resumable: in write mode each new resolution is appended to the committed
    # index immediately, so an interrupted long crawl keeps its progress and a
    # re-run short-circuits the already-resolved rows (no repeat Wayback queries).
    idx_fh = index_path(cfg).open("a") if write else None

    by_id: dict[str, dict] = {}          # doc_id -> {date, date_source} to apply
    new_count = 0
    counts = {"candidates": 0, "from_index": 0, "pdf_meta": 0, "wayback": 0, "unresolved": 0}

    try:
        for row in storage.iter_manifest():
            if (row.get("bank_code") not in codes or row.get("doc_type") not in ("D1", "D2")
                    or row.get("date_precision") == "day"):
                continue
            counts["candidates"] += 1
            key = row_key(row)
            hit = idx.get(key) if key else None
            if hit is None:
                yr = (_row_ym(row) or (0, 0))[0]
                # PDF meta is free (any year); Wayback only from ~the online era.
                r = recover(fetcher, row, use_wayback=(use_wayback and yr >= since_year))
                if r and key:
                    hit = {"key": key, "title_norm": normalize_title(row.get("title") or ""),
                           **r, "date_precision": "day", "resolved_at": date.today().isoformat()}
                    idx[key] = hit
                    new_count += 1
                    if idx_fh is not None:
                        idx_fh.write(json.dumps(hit, ensure_ascii=False) + "\n")
                        idx_fh.flush()
            else:
                counts["from_index"] += 1
            if hit:
                counts[hit["date_source"]] = counts.get(hit["date_source"], 0) + 1
                by_id[row["doc_id"]] = {"date": hit["date"], "date_source": hit["date_source"]}
            else:
                counts["unresolved"] += 1
            if counts["candidates"] % 100 == 0:
                print(f"wp-dates: {counts['candidates']} scanned, {len(by_id)} resolved "
                      f"({new_count} new) …", file=sys.stderr, flush=True)
    finally:
        if idx_fh is not None:
            idx_fh.close()

    print(f"wp-dates: {dict(counts)} (resolved {len(by_id)} / {counts['candidates']} candidates)")

    if csv_path or not write:
        out = csv_path or str(cfg.reports_dir / "wp_dates.csv")
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["doc_id", "new_date", "date_source"])
            for did, c in by_id.items():
                w.writerow([did, c["date"], c["date_source"]])
        print(f"wrote {len(by_id)} resolution(s) -> {out}", file=sys.stderr)

    if write and by_id:
        rows = []
        applied = 0
        for row in storage.iter_manifest():
            c = by_id.get(row.get("doc_id"))
            if c is not None:
                row["date"] = c["date"]
                row["year"] = int(c["date"][:4])
                row["date_precision"] = "day"
                row["date_source"] = c["date_source"]
                applied += 1
            rows.append(row)
        write_per_bank(cfg, rows)
        print(f"wp-dates: applied {applied} day-precision date(s) to the manifest; "
              f"{new_count} new index entr(y/ies) appended to {index_path(cfg)}",
              file=sys.stderr)
    return counts
