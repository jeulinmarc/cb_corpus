"""Filesystem storage, manifest, dedup.

Layout:  data/raw/<bank>/<doctype>/<year>/<doc_id>.<ext>   (ext: pdf or html)
Manifest: data/manifest/<bank>.jsonl  (one file per bank, one DocRecord row per
line). A legacy single data/manifest.jsonl is auto-split into per-bank files on
first use (see migrate_legacy_layout).

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
import sys
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


# -- per-bank manifest IO (module-level so non-Storage callers can reuse) ------
def _manifest_files(cfg: Config, bank_code: Optional[str] = None) -> list[Path]:
    """Per-bank manifest file(s) to read. One bank if `bank_code` given, else all
    `data/manifest/*.jsonl`. Falls back to the legacy single file for pre-split repos."""
    if bank_code is not None:
        return [cfg.manifest_file(bank_code)]
    files = sorted(cfg.manifest_dir.glob("*.jsonl")) if cfg.manifest_dir.is_dir() else []
    if files:
        return files
    return [cfg.manifest_path] if cfg.manifest_path.exists() else []


def iter_manifest_rows(cfg: Config, bank_code: Optional[str] = None) -> Iterator[dict]:
    """Yield manifest rows across per-bank files (or one bank)."""
    for f in _manifest_files(cfg, bank_code):
        if not f.exists():
            continue
        with f.open() as fh:
            for line in fh:
                line = line.strip()
                if line:
                    yield json.loads(line)


def migrate_legacy_layout(cfg: Config) -> int:
    """One-time: split a legacy single `data/manifest.jsonl` into per-bank files.

    No-op when there is no legacy file or per-bank files already exist. Preserves
    each row's exact bytes (no reserialization), then retires the legacy file to
    `*.pre-split.bak` so it is not double-read. Returns #banks written (0 = no-op).
    """
    legacy = cfg.manifest_path
    if not legacy.exists():
        return 0
    if cfg.manifest_dir.is_dir() and any(cfg.manifest_dir.glob("*.jsonl")):
        return 0
    by_bank: dict[str, list[str]] = {}
    with legacy.open() as fh:
        for line in fh:
            s = line.strip()
            if not s:
                continue
            bank = json.loads(s).get("bank_code") or "_unknown"
            by_bank.setdefault(bank, []).append(s)
    cfg.manifest_dir.mkdir(parents=True, exist_ok=True)
    for bank, lines in by_bank.items():
        path = cfg.manifest_file(bank)
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("\n".join(lines) + "\n")
        os.replace(tmp, path)
    os.replace(legacy, legacy.with_suffix(".jsonl.pre-split.bak"))
    print(f"[storage] split legacy manifest into {len(by_bank)} per-bank file(s) "
          f"under {cfg.manifest_dir}", file=sys.stderr, flush=True)
    return len(by_bank)


def write_per_bank(cfg: Config, rows: Iterable[dict]) -> int:
    """Atomically (re)write the per-bank files for every bank present in `rows`
    (temp file + os.replace per bank). Returns the number of rows written."""
    migrate_legacy_layout(cfg)               # never let a legacy file shadow a write
    by_bank: dict[str, list[dict]] = {}
    n = 0
    for row in rows:
        by_bank.setdefault(row.get("bank_code") or "_unknown", []).append(row)
        n += 1
    cfg.manifest_dir.mkdir(parents=True, exist_ok=True)
    for bank, brows in by_bank.items():
        path = cfg.manifest_file(bank)
        tmp = path.with_suffix(".jsonl.tmp")
        with tmp.open("w") as fh:
            for row in brows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    return n


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
        self._source_urls: set[str] = set()
        # Chrome profile reused across every render in THIS process (a fresh
        # per-call temp profile is ~10x slower). Keyed by PID so multiple
        # concurrent download processes don't fight over one profile lock —
        # this lets HTML→PDF renders run in parallel. Lives under data/
        # (git-ignored). Purged on exit (atexit) and stale ones swept at start,
        # so the per-PID dirs don't accumulate (they can reach hundreds of MB).
        self._chrome_profile = self.cfg.data_dir / f".chrome-profile-{os.getpid()}"
        _sweep_chrome_profiles(self.cfg.data_dir, keep=self._chrome_profile)
        atexit.register(shutil.rmtree, self._chrome_profile, ignore_errors=True)
        migrate_legacy_layout(self.cfg)      # split a pre-existing single manifest
        self._load_existing()

    # -- manifest --------------------------------------------------------
    def _load_existing(self) -> None:
        for rec in self.iter_manifest():
            self._ids.add(rec["doc_id"])
            if rec.get("sha256"):
                self._hashes.add(rec["sha256"])
            url = rec.get("pdf_url")
            if url:
                self._urls.add(url)
            # Persisted alt_urls (WP v3): the same paper may have been registered
            # under an alternate URL (a native scraper URL stamped onto a row first
            # ingested via RePEc, or an EconStor/SSRN fallback). Index them so
            # is_known_url() recognises that URL too — this is what stops a native
            # scraper from re-downloading a paper it finds under a different URL.
            for alt in rec.get("alt_urls") or []:
                if alt:
                    self._urls.add(alt)
            src = rec.get("source_url")
            if src:
                self._source_urls.add(src)

    def is_known_url(self, url: str) -> bool:
        """True if a record with this pdf_url is already in the manifest."""
        return url in self._urls

    def is_known_source_url(self, url: str) -> bool:
        """True if a record with this source_url is already in the manifest.

        Own index, deliberately separate from is_known_url(): source pages
        (e.g. IDEAS paper pages) identify a listing entry BEFORE its PDF is
        known — used by incremental catalog walks to skip the per-item fetch.
        """
        return url in self._source_urls

    def iter_manifest(self, bank_code: Optional[str] = None) -> Iterator[dict]:
        """All manifest rows across per-bank files, or just one bank's."""
        yield from iter_manifest_rows(self.cfg, bank_code)

    def _append(self, rec: DocRecord) -> None:
        path = self.cfg.manifest_file(rec.bank_code)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as fh:
            fh.write(json.dumps(rec.to_row(), ensure_ascii=False) + "\n")

    def rewrite_manifest(self, rows: Iterable[dict]) -> int:
        """Atomically (re)write the per-bank manifest files for the banks present
        in `rows` (already-serialized dicts), grouping by `bank_code`.

        For in-place metadata rewrites (e.g. the WP v3 date migration) the manifest
        must be rewritten, not appended. Each bank file is written to a temp file
        and `os.replace`-d, so a crash mid-write leaves the previous files intact.

        Callers own the row contents (no merging). Pass the FULL set of rows for
        each bank you touch — a bank's file is fully replaced by its rows here.
        Returns the number of rows written and refreshes the in-memory dedup
        indexes so a long-lived Storage stays consistent with disk.
        """
        n = write_per_bank(self.cfg, rows)
        self._ids.clear(); self._hashes.clear(); self._urls.clear()
        self._source_urls.clear()
        self._load_existing()
        return n

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
            if rec.source_url:
                self._source_urls.add(rec.source_url)
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
        if rec.source_url:
            self._source_urls.add(rec.source_url)
        self._append(rec)
        return "saved"

    # -- reindex (no download) -------------------------------------------
    def reindex(self, rec: DocRecord, path: Path, *, dry_run: bool = False) -> str:
        """Index an already-downloaded file WITHOUT re-fetching it.

        For a document whose bytes are already on disk (at ``path``) but whose
        manifest row was lost — e.g. the manifest was reset while the downloads
        accumulated — recompute sha256 from the on-disk bytes, set
        ``local_path``/``mime_type``, and append the manifest row from the
        (re-discovered) record. Idempotent: skips a ``doc_id`` already indexed or
        whose content hash is already present. ``dry_run`` reports the action
        without writing (mirrors :meth:`save`'s no-write contract).
        """
        if rec.doc_id in self._ids:
            return "skip:already-indexed"
        if not path.is_file():
            return "skip:missing-file"
        if dry_run:
            return "dry-run:would-reindex"

        content = path.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        if digest in self._hashes:
            return "skip:duplicate-content"

        ext = path.suffix.lower().lstrip(".")
        rec.mime_type = {"pdf": "application/pdf", "html": "text/html"}.get(ext, rec.mime_type)
        if ext == "html":
            rec.html_path = str(path)
        rec.sha256 = digest
        rec.local_path = str(path)
        self._ids.add(rec.doc_id)
        self._hashes.add(digest)
        self._urls.add(rec.pdf_url)
        if rec.source_url:
            self._source_urls.add(rec.source_url)
        self._append(rec)
        return "reindexed"

    def _record_download_error(self, rec: DocRecord, exc: Exception, label: str) -> None:
        """Append one line to data/download_errors.jsonl (durable audit of every
        failed download — stdout scrolls away, this file doesn't). Append-only,
        O_APPEND line writes; never read by the crawler itself."""
        import datetime as _dt
        entry = {
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
            "label": label,
            "bank_code": rec.bank_code,
            "doc_type": rec.doc_type.code,
            "title": rec.title,
            "pdf_url": rec.pdf_url,
            "alt_urls": rec.alt_urls or [],
            "source_url": rec.source_url,
            "error": f"{type(exc).__name__}: {exc}".replace("\n", " ")[:500],
        }
        path = self.cfg.data_dir / "download_errors.jsonl"
        with path.open("a") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_many(self, recs: Iterable[DocRecord], *, dry_run: bool = False,
                  progress_every: int = 100, label: str = "") -> dict[str, int]:
        import sys
        counts: dict[str, int] = {}
        total = 0
        for rec in recs:
            try:
                status = self.save(rec, dry_run=dry_run).split(":")[0]
            except Exception as exc:
                status = "error"
                if not dry_run:
                    try:
                        self._record_download_error(rec, exc, label)
                    except Exception:
                        pass  # auditing must never break the crawl
            counts[status] = counts.get(status, 0) + 1
            total += 1
            if progress_every and total % progress_every == 0:
                prefix = f"[{label}] " if label else ""
                print(f"{prefix}processed {total} ({dict(counts)})", file=sys.stderr, flush=True)
        return counts
