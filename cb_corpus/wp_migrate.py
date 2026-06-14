"""WP v3 — migrate v2 RePEc-dated rows to native bank-site dates (dry-run report).

The v2 manifest dates every D1/D2 working paper ``YYYY-MM-01`` (RePEc's
Creation-Date is month-only). The native bank-site scrapers know the exact day.
This module joins native discovery against the existing manifest rows by a match
cascade and reports the proposed date upgrade per row:

    date -> exact bank-site day, date_precision="day", date_source="bank_site",
    + stamp repec_handle, + register the native URL in alt_urls.

``doc_id`` / ``sha256`` / ``local_path`` are never touched — the PDF on disk is
the same file; this is a pure metadata correction with zero downloads.

This is the **dry-run report** (stdout summary + CSV). It writes nothing to the
manifest. Applying the migration (``--write``) and flipping a bank to native-first
discovery are deliberate follow-up steps (see docs/IMPLEMENTATION_PLAN.md phase 3),
because the native scraper must not run in download mode before the join has
registered native URLs in ``alt_urls``.

Currently wired for ECB (foedb). Other banks plug in as their native scrapers land.
"""
from __future__ import annotations

import csv
import re
import sys
from typing import Iterable, Optional

from .config import Config
from .http import Fetcher
from .sources.ecb_foedb import discover_ecb_wp, ecb_wp_number, repec_ecb_number
from .storage import Storage

# Native discovery per bank. doc_type ∈ {D1, D2}; returns DocRecords with day dates.
_NATIVE = {
    "ecb": discover_ecb_wp,
}
# Per-bank (pdf_url -> key) and (source_url/handle -> key) extractors.
_KEY_FROM_PDF = {
    "ecb": ecb_wp_number,
}
_KEY_FROM_HANDLE = {
    "ecb": repec_ecb_number,
}

_IDEAS_PATH = re.compile(r"/p/([^/]+)/([^/]+)/([^/.]+)")


def repec_handle_from_source_url(url: str) -> str:
    """IDEAS paper URL -> RePEc handle, e.g.
    https://ideas.repec.org/p/ecb/ecbwps/20253124.html -> RePEc:ecb:ecbwps:20253124
    """
    m = _IDEAS_PATH.search(url or "")
    return f"RePEc:{m.group(1)}:{m.group(2)}:{m.group(3)}" if m else ""


def normalize_url(u: str) -> str:
    """Canonicalise a PDF URL for equality: drop scheme, the ECB ``~hash`` segment,
    and collapse doubled slashes (manifest rows carry ``//pub`` from RePEc/RSS)."""
    u = (u or "").strip().lower()
    u = re.sub(r"^https?://", "", u)
    u = re.sub(r"~[0-9a-z]+", "", u)
    u = re.sub(r"/{2,}", "/", u)
    return u


def _row_key(bank: str, row: dict):
    """(doc_type, number) join key for a manifest row: from pdf_url, else handle."""
    return (_KEY_FROM_PDF[bank](row.get("pdf_url") or "")
            or _KEY_FROM_HANDLE[bank](row.get("source_url") or ""))


def build_report(bank: str, fetcher: Fetcher,
                 manifest_rows: Iterable[dict]) -> tuple[dict, list[dict]]:
    """Join native records against manifest D1/D2 rows for `bank`.

    Returns (summary_counts, change_rows). Each change row describes one matched
    manifest document and the metadata rewrite that the migration would apply.
    """
    # Index native discovery by join key and by normalized URL.
    native_by_key: dict = {}
    native_by_url: dict = {}
    for rec in _NATIVE[bank](fetcher):
        key = ecb_wp_number(rec.pdf_url) if bank == "ecb" else None
        if key is not None:
            native_by_key.setdefault(key, rec)
        native_by_url.setdefault(normalize_url(rec.pdf_url), rec)

    summary = {"native_total": len(native_by_url), "manifest_total": 0,
               "matched_key": 0, "matched_url": 0, "already_day": 0,
               "unmatched_manifest": 0}
    matched_native_keys: set = set()
    changes: list[dict] = []

    for row in manifest_rows:
        if row.get("bank_code") != bank or row.get("doc_type") not in ("D1", "D2"):
            continue
        summary["manifest_total"] += 1
        key = _row_key(bank, row)
        native = native_by_key.get(key) if key is not None else None
        match_type = "key" if native is not None else ""
        if native is None:
            native = native_by_url.get(normalize_url(row.get("pdf_url") or ""))
            match_type = "url" if native is not None else ""
        if native is None:
            summary["unmatched_manifest"] += 1
            continue
        summary[f"matched_{match_type}"] += 1
        if key is not None:
            matched_native_keys.add(key)

        # Already migrated? (idempotent — skip rows already at day/bank_site.)
        if row.get("date_precision") == "day" and row.get("date_source") == "bank_site":
            summary["already_day"] += 1
            continue

        native_url = native.pdf_url
        new_date = native.date.isoformat() if native.date else row.get("date")
        changes.append({
            "doc_id": row.get("doc_id"),
            "doc_type": row.get("doc_type"),
            "old_date": row.get("date"),
            "new_date": new_date,
            "match_type": match_type,
            "old_url": row.get("pdf_url"),
            "native_url": native_url,
            "repec_handle": repec_handle_from_source_url(row.get("source_url") or ""),
            "alt_url_added": native_url if native_url != (row.get("pdf_url") or "") else "",
        })

    summary["native_only"] = sum(1 for k in native_by_key if k not in matched_native_keys)
    return summary, changes


def apply_change(row: dict, change: dict) -> None:
    """Apply one proposed migration `change` to a manifest `row`, in place.

    Metadata only: date + precision/source, repec_handle, and the native URL
    registered in alt_urls (so dedup recognises it). doc_id / sha256 / local_path
    / pdf_url are deliberately left untouched — the file on disk is the same.
    """
    row["date"] = change["new_date"]
    row["date_precision"] = "day"
    row["date_source"] = "bank_site"
    if change.get("repec_handle"):
        row["repec_handle"] = change["repec_handle"]
    alt = change.get("alt_url_added")
    if alt and alt != row.get("pdf_url"):
        alts = list(row.get("alt_urls") or [])
        if alt not in alts:
            alts.append(alt)
        row["alt_urls"] = alts


def run_wp_migrate(bank_codes: Optional[Iterable[str]] = None,
                   csv_path: Optional[str] = None,
                   write: bool = False,
                   config: Optional[Config] = None) -> dict[str, dict]:
    """Migration report for the requested banks (default: all wired).

    Default (``write=False``) is a dry run: writes nothing to the manifest, prints
    a per-bank summary and a CSV of proposed rewrites under data/reports/.

    With ``write=True`` it additionally applies the matched changes and atomically
    rewrites the manifest (metadata only — see :func:`apply_change`). Safety rails:
    the row count must be unchanged and the set of doc_ids identical before/after,
    or it raises before swapping the file in. Idempotent: a second run finds the
    rows already at day/bank_site and changes nothing. Returns {bank: summary}.
    """
    cfg = config or Config()
    fetcher = Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    codes = [c for c in (bank_codes or _NATIVE.keys()) if c in _NATIVE]

    results: dict[str, dict] = {}
    all_changes: list[dict] = []
    for bank in codes:
        # Re-read the manifest per bank (cheap; keeps the join self-contained).
        summary, changes = build_report(bank, fetcher, storage.iter_manifest())
        for c in changes:
            c["bank"] = bank
        all_changes.extend(changes)
        results[bank] = summary
        print(f"{bank}: {summary} (proposed date fixes: {len(changes)})")

    if all_changes:
        out = csv_path or str(cfg.reports_dir / "wp_migrate.csv")
        cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        fields = ["bank", "doc_id", "doc_type", "old_date", "new_date",
                  "match_type", "old_url", "native_url", "repec_handle", "alt_url_added"]
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader()
            for c in all_changes:
                w.writerow({k: c.get(k, "") for k in fields})
        print(f"wrote {len(all_changes)} proposed change(s) -> {out}", file=sys.stderr)
    else:
        print("no proposed changes (nothing matched, or all already migrated)",
              file=sys.stderr)

    if write and all_changes:
        change_by_id = {c["doc_id"]: c for c in all_changes}
        before_ids: list[str] = []
        new_rows: list[dict] = []
        applied = 0
        for row in storage.iter_manifest():
            before_ids.append(row.get("doc_id"))
            c = change_by_id.get(row.get("doc_id"))
            if c is not None:
                apply_change(row, c)
                applied += 1
            new_rows.append(row)
        after_ids = [r.get("doc_id") for r in new_rows]
        # Safety rails: never lose/gain rows; never change identity.
        assert len(new_rows) == len(before_ids), "row count changed"
        assert set(after_ids) == set(before_ids), "doc_id set changed"
        assert applied == len(change_by_id), (
            f"applied {applied} != {len(change_by_id)} matched (doc_id mismatch?)")
        n = storage.rewrite_manifest(new_rows)
        print(f"MIGRATED {applied} row(s) in place; manifest now {n} rows "
              f"(doc_id/sha256/local_path untouched)", file=sys.stderr)
    return results
