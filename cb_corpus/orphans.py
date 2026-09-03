"""`sweep-orphans`: quarantine on-disk files that duplicate indexed documents.

An *orphan* is any regular file under ``raw/`` whose filename stem is not a
manifest ``doc_id``. Orphans exist because an earlier doc_id scheme named the
same documents differently and the old files were never removed; nearly all
of them are byte-identical to a document that IS indexed under another id.

Rules (see the design note of 2026-09-03):

* an orphan whose sha256 is owned by a manifest row is a **duplicate** → moved
  to ``raw_orphans/<same relative path>`` (``os.replace``, same filesystem);
* any other orphan is **unindexed** → left in place, reported (candidate for
  ``reindex-from-disk``);
* a file whose stem IS a manifest doc_id is never examined, let alone moved;
* the default run is a dry-run: it classifies and reports, moves nothing.

The manifest is read once, through :func:`storage.iter_manifest_rows` (no
``Storage()`` side effects). On the NAS the run-job global lock guarantees no
concurrent manifest writer; from a workstation over SMB, run it outside the
nightly sync window.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from .config import Config
from .storage import iter_manifest_rows

PDF_MAGIC = b"%PDF"
MIN_VALID_PDF_BYTES = 20 * 1024      # same honesty rule as recover._verify_local_pdf
_CHUNK = 1024 * 1024


@dataclass
class OrphanEntry:
    path: str                 # absolute on-disk path
    rel: str                  # relative to raw/, posix
    bank: str
    doc_type: str
    year_dir: str
    ext: str                  # lower-case, without the dot; "" when none
    size: int
    sha256: str
    valid_pdf: bool
    matched_doc_id: Optional[str]
    action: str               # would-move | moved | move-failed | kept-unindexed
    dest: Optional[str]       # relative path under raw_orphans/ (duplicates only)
    error: Optional[str] = None


@dataclass
class SweepSummary:
    files_seen: int
    orphans: int
    duplicates: int
    unindexed: int
    moved: int
    move_failed: int
    bytes_duplicates: int
    dry_run: bool
    report_path: str
    started_at: str
    finished_at: str


def load_manifest_index(cfg: Config) -> tuple[set[str], dict[str, str]]:
    """``(doc_ids, sha256 -> doc_id)`` across every per-bank manifest.

    Read-only, via :func:`storage.iter_manifest_rows` (blank lines skipped,
    torn tails handled there). A sha256 owned by two rows keeps the first
    owner — which row "owns" the bytes is immaterial to the sweep.
    """
    ids: set[str] = set()
    by_hash: dict[str, str] = {}
    for rec in iter_manifest_rows(cfg):
        doc_id = rec.get("doc_id")
        if not doc_id:
            continue
        ids.add(doc_id)
        sha = rec.get("sha256")
        if sha:
            by_hash.setdefault(sha, doc_id)
    return ids, by_hash


def iter_orphans(cfg: Config, doc_ids: set[str], *,
                 banks: Optional[set[str]] = None) -> Iterator[Path]:
    """Every regular file under ``raw/`` (any depth, any extension) whose stem
    is not in ``doc_ids``, in sorted order. ``banks`` restricts the top-level
    ``raw/<bank>/`` directories walked."""
    for path in _iter_all_files(cfg, banks):
        if path.stem not in doc_ids:
            yield path


def _sha256_of(path: Path) -> tuple[str, int, bool]:
    """(sha256, size, valid_pdf) in one streaming pass."""
    h = hashlib.sha256()
    size = 0
    head = b""
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            if not head:
                head = chunk[:len(PDF_MAGIC)]
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size, (size > MIN_VALID_PDF_BYTES and head == PDF_MAGIC)


def classify(path: Path, raw: Path, hash_index: dict[str, str]) -> OrphanEntry:
    """Hash one orphan and decide its provisional action."""
    rel = path.relative_to(raw).as_posix()
    parts = rel.split("/")
    bank = parts[0] if len(parts) > 1 else ""
    doc_type = parts[1] if len(parts) > 2 else ""
    year_dir = parts[2] if len(parts) > 3 else ""
    ext = path.suffix.lower().lstrip(".")
    sha, size, valid = _sha256_of(path)
    owner = hash_index.get(sha)
    return OrphanEntry(
        path=str(path), rel=rel, bank=bank, doc_type=doc_type, year_dir=year_dir,
        ext=ext, size=size, sha256=sha, valid_pdf=valid, matched_doc_id=owner,
        action="would-move" if owner else "kept-unindexed",
        dest=rel if owner else None,
    )


def move_to_orphans(entry: OrphanEntry, cfg: Config) -> None:
    """Move one duplicate from raw/ to raw_orphans/ (same relative path).

    ``os.replace`` inside ``data_dir`` — an atomic rename on one filesystem.
    Never overwrites: a destination that already holds the same bytes means a
    replayed sweep, so the source is simply removed; a destination with other
    content is left alone and the move is recorded as failed.
    """
    src = Path(entry.path)
    dest = cfg.orphans_dir / entry.rel
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists():
            dest_sha, _, _ = _sha256_of(dest)
            if dest_sha != entry.sha256:
                entry.action, entry.error = "move-failed", "dest exists with different content"
                return
            src.unlink()
        else:
            os.replace(src, dest)
        entry.action = "moved"
    except OSError as exc:
        entry.action, entry.error = "move-failed", f"{type(exc).__name__}: {exc}"


def sweep_orphans(cfg: Config, *, banks: Optional[set[str]] = None, move: bool = False,
                  progress_every: int = 1000, report_path: Optional[Path] = None,
                  now: Optional[datetime] = None) -> SweepSummary:
    """Find, classify, (move) and report every orphan under raw/.

    Raises ``FileNotFoundError`` when ``raw/`` is missing — a mis-pointed data
    dir must not look like a clean corpus.
    """
    if not cfg.raw_dir.is_dir():
        raise FileNotFoundError(f"corpus raw dir not found: {cfg.raw_dir}")
    started = now or datetime.now(timezone.utc)
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    report_path = report_path or (cfg.reports_dir / f"orphan_sweep_{stamp}.jsonl")
    report_path.parent.mkdir(parents=True, exist_ok=True)

    doc_ids, by_hash = load_manifest_index(cfg)
    files_seen = orphans_n = dups = unindexed = moved = failed = 0
    bytes_dups = 0
    with report_path.open("w", encoding="utf-8") as out:
        for path in _iter_all_files(cfg, banks):
            files_seen += 1
            if progress_every and files_seen % progress_every == 0:
                print(f"sweep-orphans: examined {files_seen} files "
                      f"({orphans_n} orphans, {dups} duplicates so far)", file=sys.stderr, flush=True)
            if path.stem in doc_ids:
                continue
            orphans_n += 1
            entry = classify(path, cfg.raw_dir, by_hash)
            if entry.matched_doc_id is None:
                unindexed += 1
            else:
                dups += 1
                bytes_dups += entry.size
                if move:
                    move_to_orphans(entry, cfg)
                    if entry.action == "moved":
                        moved += 1
                    else:
                        failed += 1
            out.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    finished = datetime.now(timezone.utc)
    summary = SweepSummary(
        files_seen=files_seen, orphans=orphans_n, duplicates=dups, unindexed=unindexed,
        moved=moved, move_failed=failed, bytes_duplicates=bytes_dups, dry_run=not move,
        report_path=str(report_path), started_at=started.isoformat(),
        finished_at=finished.isoformat(),
    )
    report_path.with_suffix(".summary.json").write_text(
        json.dumps(asdict(summary), indent=2) + "\n", encoding="utf-8")
    return summary


def _iter_all_files(cfg: Config, banks: Optional[set[str]]) -> Iterator[Path]:
    """Every regular file under raw/<bank>/ (sorted), for the counters; the
    orphan test itself is applied by the caller so ``files_seen`` is honest."""
    for bank_dir in sorted(p for p in cfg.raw_dir.iterdir() if p.is_dir()):
        if banks is not None and bank_dir.name not in banks:
            continue
        for path in sorted(bank_dir.rglob("*")):
            if path.is_file():
                yield path
