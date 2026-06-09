"""Filesystem storage, manifest, dedup.

Layout:  data/raw/<bank>/<doctype>/<year>/<doc_id>.<ext>   (ext: pdf or html)
Manifest: data/manifest.jsonl  (one DocRecord row per line)

For HTML-sourced documents (e.g. ECB monetary policy accounts), the raw HTML
is ALWAYS preserved as `<doc_id>.html` (the source-of-truth); if `html_to_pdf`
is enabled and Chrome rendering succeeds, a `<doc_id>.pdf` is produced
alongside it and becomes the canonical artifact (`local_path`, `mime_type`).
The `html_path` field on the record points to the HTML sibling so downstream
consumers can fall back if the PDF rendering is missing or unsatisfactory.

No domain guard: the discovery layer is responsible for handing us URLs we
want. Dedup is enforced by doc_id (stable hash of bank+type+date+url) and by
content sha256.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Iterable, Iterator, Optional

from .config import Config
from .http import Fetcher
from .htmlpdf import render_url_to_pdf
from .models import DocRecord


_EXT_FOR_MIME = {
    "application/pdf": "pdf",
    "text/html": "html",
    "application/xhtml+xml": "html",
}


def ext_for_mime(mime: str) -> str:
    return _EXT_FOR_MIME.get((mime or "").lower(), "bin")


def _sweep_chrome_profiles(data_dir: Path, keep: Path) -> None:
    """Remove leftover per-PID Chrome profiles whose process is gone.

    Profiles are named `.chrome-profile-<pid>`; a crashed/killed run leaves its
    profile behind, so on startup we drop any whose PID is no longer alive
    (best-effort, never raises). `keep` (our own profile) is preserved.
    """
    try:
        candidates = list(data_dir.glob(".chrome-profile-*"))
    except OSError:
        return
    for p in candidates:
        if p == keep:
            continue
        try:
            pid = int(p.name.rsplit("-", 1)[-1])
        except ValueError:
            continue
        try:
            os.kill(pid, 0)          # raises if the PID is not alive
        except OSError:
            shutil.rmtree(p, ignore_errors=True)


class Storage:
    def __init__(self, config: Optional[Config] = None,
                 fetcher: Optional[Fetcher] = None):
        self.cfg = config or Config()
        self.fetcher = fetcher or Fetcher(self.cfg)
        self.cfg.raw_dir.mkdir(parents=True, exist_ok=True)
        self.cfg.reports_dir.mkdir(parents=True, exist_ok=True)
        self._hashes: set[str] = set()
        self._ids: set[str] = set()
        self._urls: set[str] = set()
        # Chrome profile reused across every render in THIS process (a fresh
        # per-call temp profile is ~10x slower). Keyed by PID so multiple
        # concurrent download processes don't fight over one profile lock —
        # this lets HTML→PDF renders run in parallel. Lives under data/
        # (git-ignored). Purged on exit (atexit) and stale ones swept at start,
        # so the per-PID dirs don't accumulate (they can reach hundreds of MB).
        self._chrome_profile = self.cfg.data_dir / f".chrome-profile-{os.getpid()}"
        _sweep_chrome_profiles(self.cfg.data_dir, keep=self._chrome_profile)
        atexit.register(shutil.rmtree, self._chrome_profile, ignore_errors=True)
        self._load_existing()

    # -- manifest --------------------------------------------------------
    def _load_existing(self) -> None:
        if not self.cfg.manifest_path.exists():
            return
        for rec in self.iter_manifest():
            self._ids.add(rec["doc_id"])
            if rec.get("sha256"):
                self._hashes.add(rec["sha256"])
            url = rec.get("pdf_url")
            if url:
                self._urls.add(url)

    def is_known_url(self, url: str) -> bool:
        """True if a record with this pdf_url is already in the manifest."""
        return url in self._urls

    def iter_manifest(self) -> Iterator[dict]:
        if not self.cfg.manifest_path.exists():
            return
        with self.cfg.manifest_path.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)

    def _append(self, rec: DocRecord) -> None:
        with self.cfg.manifest_path.open("a") as fh:
            fh.write(json.dumps(rec.to_row(), ensure_ascii=False) + "\n")

    # -- paths -----------------------------------------------------------
    def target_path(self, rec: DocRecord) -> Path:
        year = rec.year or 0
        ext = ext_for_mime(rec.mime_type) if rec.mime_type else (
            "pdf" if rec.pdf_url.lower().endswith(".pdf") else "html"
        )
        return (self.cfg.raw_dir / rec.bank_code / rec.doc_type.code
                / str(year) / f"{rec.doc_id}.{ext}")

    # -- download --------------------------------------------------------
    def save(self, rec: DocRecord, *, dry_run: bool = False) -> str:
        if rec.doc_id in self._ids:
            return "skip:already-indexed"
        if dry_run:
            # In-memory only: dedup within this pass for accurate counts, but
            # NEVER write to the manifest. A persisted dry-run row (local_path
            # =null, sha256=null) would be re-loaded by the next real run and
            # make save() skip it as "already-indexed" — the document would
            # then never be downloaded. (This is exactly how a 155-row
            # placeholder pollution happened once.)
            self._ids.add(rec.doc_id)
            self._urls.add(rec.pdf_url)
            return "dry-run:indexed"

        # Try the preferred URL, then any fallback copies (EconStor/SSRN/cached)
        # so a 403/dead preferred host doesn't lose the document. doc_id stays
        # bound to rec.pdf_url, so dedup/identity are unaffected by which copy won.
        content = None
        mime = ""
        for url in [rec.pdf_url, *(rec.alt_urls or [])]:
            try:
                content, mime = self.fetcher.get_bytes(url)
                break
            except Exception:
                content = None
        if content is None:
            raise RuntimeError(
                f"all {1 + len(rec.alt_urls or [])} candidate URL(s) failed for {rec.pdf_url}")
        if mime:
            rec.mime_type = mime
        digest = hashlib.sha256(content).hexdigest()
        if digest in self._hashes:
            return "skip:duplicate-content"

        is_html = mime.startswith("text/html") or mime.startswith("application/xhtml")
        if is_html:
            # Always preserve the source HTML — it's our fallback if the PDF
            # rendering is missing or unsatisfactory.
            rec.mime_type = "text/html"
            html_path = self.target_path(rec)
            html_path.parent.mkdir(parents=True, exist_ok=True)
            html_path.write_bytes(content)
            rec.html_path = str(html_path)

            if self.cfg.html_to_pdf:
                # Try to render a PDF sibling. If Chrome fails, the HTML still
                # remains and becomes the canonical artifact.
                pdf_rec = DocRecord(  # transient copy for path resolution
                    bank_code=rec.bank_code, doc_type=rec.doc_type, title=rec.title,
                    pdf_url=rec.pdf_url, date=rec.date,
                    mime_type="application/pdf",
                )
                # doc_id is derived from immutable fields, so same as rec.doc_id.
                pdf_path = self.target_path(pdf_rec)
                try:
                    # Chrome re-fetches the live URL; share the host's rate
                    # budget so this render + the get_bytes above don't burst.
                    self.fetcher.throttle(rec.pdf_url)
                    render_url_to_pdf(rec.pdf_url, pdf_path,
                                      user_data_dir=str(self._chrome_profile))
                    rec.mime_type = "application/pdf"
                    path = pdf_path
                except Exception:
                    # Render failed -> canonical artifact stays the HTML.
                    path = html_path
            else:
                path = html_path
        else:
            path = self.target_path(rec)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        rec.sha256 = digest
        rec.local_path = str(path)
        self._ids.add(rec.doc_id)
        self._hashes.add(digest)
        self._urls.add(rec.pdf_url)
        self._append(rec)
        return "saved"

    def save_many(self, recs: Iterable[DocRecord], *, dry_run: bool = False,
                  progress_every: int = 100, label: str = "") -> dict[str, int]:
        import sys
        counts: dict[str, int] = {}
        total = 0
        for rec in recs:
            try:
                status = self.save(rec, dry_run=dry_run).split(":")[0]
            except Exception:
                status = "error"
            counts[status] = counts.get(status, 0) + 1
            total += 1
            if progress_every and total % progress_every == 0:
                prefix = f"[{label}] " if label else ""
                print(f"{prefix}processed {total} ({dict(counts)})", file=sys.stderr, flush=True)
        return counts
