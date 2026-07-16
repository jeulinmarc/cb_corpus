"""`recover-downloads` — inventory-driven Wayback recovery.

Every download that fails ALL of its candidate URLs is durably logged to
``data/download_errors.jsonl`` (``Storage._record_download_error``, live since
PR #6). Most of that inventory is genuinely gone from the bank's own site but
still sitting in the Wayback Machine under its ORIGINAL (now-dead) URL — this
module turns that inventory into recovered documents, the same way
``sources/wayback.py``'s ``WaybackSource``/``run_wayback_recovery`` already do
for hand-picked ``url_prefix`` sweeps, except driven by the audit trail
instead of a CDX prefix walk.

Per entry: skip it if the corpus already converged on it since the failure was
logged (nightly retries fill some gaps on their own); otherwise refresh title/
date from the IDEAS source page when there is one (same honesty as
``sources/repec.py`` discovery: month precision, ``date_source="repec"``);
look up the latest Wayback snapshot of the official URL (falling back to any
alternate URL); dry-run reports it, ``--download`` saves it with
``provenance="wayback"`` and the official bank URL untouched as ``pdf_url``
(citation + stable ``doc_id``) — the raw snapshot is just an ``alt_urls``
fallback ``Storage.save`` tries.

No fuzzy matching anywhere: an entry with no snapshot under any known URL is
reported ``unrecoverable`` and left alone, honestly. When ``--download``
finds a snapshot but ``Storage.save`` reports ``skip:*`` (the bytes
hash-match a doc already in the corpus, or the doc_id was already indexed),
the entry is reported ``duplicate`` -- there is nothing left to recover,
so it is never relabelled ``recoverable`` (which would just re-download the
same duplicate PDF every run).
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path
from typing import Iterable, Optional

from .banks import get_bank
from .config import Config
from .http import Fetcher
from .models import DocRecord
from .sources.repec import IDEAS, _paper_meta, extract_pdf_candidates
from .sources.wayback import latest_capture, raw_url
from .storage import Storage
from .taxonomy import by_code

_ACTIONS = ("recoverable", "recovered", "duplicate", "unrecoverable", "converged")
_CSV_FIELDS = ("bank", "pdf_url", "action", "snapshot_ts", "title")


def _read_inventory(cfg: Config,
                    bank_codes: Optional[Iterable[str]] = None) -> list[dict]:
    """Read ``data/download_errors.jsonl``, dedup by ``pdf_url`` keeping the
    LATEST entry (the file is append-ordered, so a later line simply
    overwrites an earlier one for the same url). Optional ``bank_codes``
    filter. A missing file is tolerated: empty inventory, a stderr note, no
    crash (the file is created lazily by the crawler only once a download
    actually fails)."""
    path = cfg.data_dir / "download_errors.jsonl"
    if not path.exists():
        print(f"[recover] no {path} -- nothing to recover", file=sys.stderr, flush=True)
        return []
    codes = set(bank_codes) if bank_codes else None
    by_url: dict[str, dict] = {}
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if codes is not None and entry.get("bank_code") not in codes:
                continue
            url = entry.get("pdf_url")
            if not url:
                continue
            by_url[url] = entry   # later line wins -> latest entry per pdf_url
    return list(by_url.values())


def _is_converged(storage: Storage, entry: dict) -> bool:
    """True if the corpus already has this document (by pdf_url, any of the
    audit entry's alt_urls, or its source_url) -- the nightly retry already
    got it since the failure was logged; no network needed to know that."""
    if storage.is_known_url(entry.get("pdf_url") or ""):
        return True
    for alt in entry.get("alt_urls") or []:
        if alt and storage.is_known_url(alt):
            return True
    source_url = entry.get("source_url") or ""
    return bool(source_url) and storage.is_known_source_url(source_url)


def _bank_homepage(bank_code: str) -> Optional[str]:
    try:
        return get_bank(bank_code).homepage
    except KeyError:
        return None


def _refresh_metadata(fetcher: Fetcher, entry: dict) -> tuple[str, object, Optional[str],
                                                              Optional[str], list[str]]:
    """(title, date, date_precision, date_source, fresh alt-url candidates).

    When ``source_url`` is an IDEAS page, fetch it once and take title/date/
    candidates via the existing ``_paper_meta``/``extract_pdf_candidates``
    (month precision, ``date_source="repec"`` -- same honesty as RePEc
    discovery). On any fetch failure, OR when ``source_url`` isn't IDEAS, fall
    back to the audit entry's own title; date stays unknown (``None``), and
    ``date_precision``/``date_source`` are left as ``None`` here so the caller
    leaves the ``DocRecord`` defaults untouched -- mirroring how
    ``WaybackSource`` already handles a paper with no recoverable date.
    """
    title = entry.get("title") or ""
    source_url = entry.get("source_url") or ""
    if source_url.startswith(IDEAS):
        try:
            html = fetcher.get_text(source_url)
            fresh_title, fresh_date = _paper_meta(html)
            cands = extract_pdf_candidates(html, _bank_homepage(entry.get("bank_code") or ""))
            return (fresh_title or title, fresh_date, "month", "repec", cands)
        except Exception:
            pass
    return (title, None, None, None, [])


def _find_snapshot(fetcher: Fetcher, pdf_url: str,
                   alt_candidates: list[str]) -> tuple[Optional[str], Optional[str]]:
    """(timestamp, original_url) for the latest Wayback snapshot of the
    official pdf_url, or -- on a miss -- of each alt url in order. ``None,
    None`` when nothing is archived anywhere."""
    for candidate in [pdf_url, *alt_candidates]:
        if not candidate:
            continue
        ts = latest_capture(fetcher, candidate)
        if ts:
            return ts, candidate
    return None, None


def run_recover_downloads(bank_codes: Optional[Iterable[str]] = None,
                          download: bool = False,
                          csv_path: Optional[str] = None,
                          config: Optional[Config] = None,
                          fetcher: Optional[Fetcher] = None) -> dict[str, dict]:
    """Drive the full recover-downloads pass. Dry-run by default: only the CSV
    is written, nothing is downloaded or saved (``--download`` opt-in mirrors
    the rest of the corpus's discovery commands). Returns
    ``{bank_code: {"recoverable": n, "recovered": n, "duplicate": n,
    "unrecoverable": n, "converged": n}}`` -- ``recovered`` only counts
    entries actually saved in ``--download`` mode; ``duplicate`` counts
    entries whose ``storage.save()`` came back ``skip:*`` (the snapshot's
    bytes hash-match a doc already in the corpus, or the doc_id was already
    indexed) -- nothing left to recover, so the CSV action is honestly
    ``duplicate``, not ``recoverable`` (which would keep re-downloading the
    full PDF every run for no gain). A CSV report (``{bank, pdf_url, action,
    snapshot_ts, title}``) is written in both modes so a dry-run's
    classification is never lost, and a CSV line never claims an action that
    didn't happen (a failed ``--download`` save stays ``recoverable``, not
    ``recovered``; its failure lands in ``download_errors.jsonl`` like any
    other, via the audit path).
    """
    cfg = config or Config()
    fetcher = fetcher or Fetcher(cfg)
    storage = Storage(cfg, fetcher)

    entries = _read_inventory(cfg, bank_codes)
    results: dict[str, dict] = {}
    csv_rows: list[dict] = []

    for entry in entries:
        bank = entry.get("bank_code") or "_unknown"
        summary = results.setdefault(bank, {a: 0 for a in _ACTIONS})
        pdf_url = entry.get("pdf_url") or ""

        if _is_converged(storage, entry):
            summary["converged"] += 1
            csv_rows.append({"bank": bank, "pdf_url": pdf_url, "action": "converged",
                             "snapshot_ts": "", "title": entry.get("title") or ""})
            continue

        title, rec_date, date_precision, date_source, cands = _refresh_metadata(fetcher, entry)
        alt_candidates = list(dict.fromkeys([*cands, *(entry.get("alt_urls") or [])]))
        ts, snapshot_of = _find_snapshot(fetcher, pdf_url, alt_candidates)

        if ts is None:
            summary["unrecoverable"] += 1
            csv_rows.append({"bank": bank, "pdf_url": pdf_url, "action": "unrecoverable",
                             "snapshot_ts": "", "title": title})
            continue

        summary["recoverable"] += 1
        action = "recoverable"

        if download:
            doc_type = None
            try:
                doc_type = by_code(entry.get("doc_type") or "")
            except KeyError:
                pass
            if doc_type is not None:
                snapshot_url = raw_url(snapshot_of, ts)
                rec_alts = list(dict.fromkeys(
                    [snapshot_url, *(u for u in alt_candidates if u != pdf_url)]))
                rec = DocRecord(
                    bank_code=bank, doc_type=doc_type, title=title,
                    pdf_url=pdf_url, alt_urls=rec_alts,
                    source_url=entry.get("source_url") or "",
                    date=rec_date, provenance="wayback",
                    mime_type="application/pdf",
                )
                if date_precision:
                    rec.date_precision = date_precision
                if date_source:
                    rec.date_source = date_source
                try:
                    status = storage.save(rec)
                except Exception as exc:  # noqa: BLE001 - audited below, never aborts the pass
                    status = "error"
                    try:
                        storage._record_download_error(rec, exc, "recover-downloads")
                    except Exception:
                        pass
                if status == "saved":
                    summary["recovered"] += 1
                    action = "recovered"
                elif status.startswith("skip:"):
                    # Bytes hash-matched an existing doc (skip:duplicate-content)
                    # or the doc_id was already indexed (skip:already-indexed):
                    # either way there is nothing left to recover here. Reporting
                    # this as "recoverable" would be a lie (nothing recoverable
                    # remains) and would keep re-downloading the full PDF every
                    # run just to discover the same duplicate again.
                    summary["duplicate"] += 1
                    action = "duplicate"

        csv_rows.append({"bank": bank, "pdf_url": pdf_url, "action": action,
                         "snapshot_ts": ts, "title": title})

    out = csv_path or str(cfg.reports_dir / "recover_downloads.csv")
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(_CSV_FIELDS))
        w.writeheader()
        for row in csv_rows:
            w.writerow(row)
    print(f"[recover] wrote {len(csv_rows)} row(s) -> {out}", file=sys.stderr, flush=True)

    return results
