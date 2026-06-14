"""Retroactive HTML -> PDF migration.

Walks the manifest, finds every entry whose on-disk artifact is HTML, renders
the live URL via headless Chrome, replaces the .html with the .pdf, and
rewrites the manifest entry (mime_type, local_path).

Idempotent: re-running on an already-converted manifest does nothing.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from typing import Optional

from .config import Config
from .htmlpdf import find_chrome, render_url_to_pdf
from .storage import iter_manifest_rows, write_per_bank


def _ext_swap(path: Path, new_ext: str) -> Path:
    return path.with_suffix(new_ext)


def convert_existing(config: Optional[Config] = None,
                     dry_run: bool = False) -> dict[str, int]:
    """Convert every text/html entry in the manifest to PDF.

    Returns {status: count}. Statuses:
      converted     - HTML successfully rendered to PDF on disk; manifest updated.
      skip-not-html - entry mime != text/html (nothing to do).
      skip-missing  - on-disk file no longer exists.
      skip-already  - already a .pdf next to the manifest entry.
      error         - Chrome conversion failed.
    """
    cfg = config or Config()
    if find_chrome() is None:
        raise RuntimeError("no Chrome / Chromium binary found")
    rewritten: list[dict] = list(iter_manifest_rows(cfg))
    if not rewritten:
        return {"empty": 0}

    counts: dict[str, int] = {}
    n = 0
    # One Chrome profile reused across renders — avoids per-call cold-start
    # (10x speedup vs fresh tempdir per render).
    with tempfile.TemporaryDirectory(prefix="cbc_chrome_convert_") as udd:
        for row in rewritten:
            status = _convert_one(row, user_data_dir=udd, dry_run=dry_run)
            counts[status] = counts.get(status, 0) + 1
            n += 1
            if n % 50 == 0:
                print(f"[convert-html] processed {n} ({dict(counts)})",
                      file=sys.stderr, flush=True)

    if not dry_run:
        write_per_bank(cfg, rewritten)
    return counts


def _convert_one(row: dict, *, dry_run: bool,
                 user_data_dir: Optional[str] = None) -> str:
    if row.get("mime_type") != "text/html":
        return "skip-not-html"
    local = row.get("local_path")
    if not local:
        return "skip-missing"
    html_path = Path(local)
    pdf_path = _ext_swap(html_path, ".pdf")
    if pdf_path.exists() and not html_path.exists():
        return "skip-already"
    if not html_path.exists():
        return "skip-missing"
    if dry_run:
        return "converted"  # would-be
    try:
        render_url_to_pdf(row["pdf_url"], pdf_path, user_data_dir=user_data_dir)
    except Exception:
        return "error"
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        # Keep the source HTML alongside the rendered PDF.
        row["mime_type"] = "application/pdf"
        row["local_path"] = str(pdf_path)
        if html_path.exists():
            row["html_path"] = str(html_path)
        return "converted"
    return "error"
