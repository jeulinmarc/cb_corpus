"""`cb_corpus sweep-orphans`: quarantine on-disk files that duplicate indexed docs.

Scenario (measured on the production dataset, 2026-09-03): tens of thousands of
files under raw/ have no manifest row because an earlier doc_id scheme named
them differently; nearly all are byte-identical to a document that IS indexed
under another id. The sweep moves those duplicates out of raw/ (mirror tree
under raw_orphans/), reports the rest, and never touches an indexed file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cb_corpus import orphans
from cb_corpus.config import Config

PDF_A = b"%PDF-1.4 " + b"a" * (21 * 1024)          # valid: magic + > 20 KiB
PDF_B = b"%PDF-1.4 " + b"b" * (21 * 1024)
TINY = b"%PDF-1.4 tiny"                              # magic but too small
NOT_PDF = b"<html>not a pdf</html>" + b"x" * (21 * 1024)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _cfg(tmp_path: Path) -> Config:
    cfg = Config(data_dir=tmp_path / "data")
    cfg.raw_dir.mkdir(parents=True)
    cfg.manifest_dir.mkdir(parents=True)
    return cfg


def _write(cfg: Config, rel: str, data: bytes) -> Path:
    p = cfg.raw_dir / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def _index(cfg: Config, bank: str, *rows: dict, blank_line: bool = False) -> None:
    """Append manifest rows to manifest/<bank>.jsonl (doc_id, sha256, local_path)."""
    f = cfg.manifest_file(bank)
    with f.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
            if blank_line:
                fh.write("\n")


def _row(doc_id: str, data: bytes, bank: str, rel: str) -> dict:
    return {"doc_id": doc_id, "bank_code": bank, "doc_type": rel.split("/")[1],
            "sha256": _sha(data), "local_path": f"data/raw/{rel}", "pdf_url": f"https://x/{doc_id}.pdf"}


# --- Task 1: index, walk, classify ------------------------------------------

def test_orphans_dir_is_a_sibling_of_raw(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.orphans_dir == cfg.data_dir / "raw_orphans"


def test_manifest_index_maps_ids_and_hashes_across_bank_files(tmp_path):
    """The index is built from every per-bank file, and a blank line (a
    legitimate artefact of the append-only writers) must not break loading."""
    cfg = _cfg(tmp_path)
    _index(cfg, "us", _row("aaaa", PDF_A, "us", "us/C1/2015/aaaa.pdf"), blank_line=True)
    _index(cfg, "ecb", _row("bbbb", PDF_B, "ecb", "ecb/C1/2016/bbbb.pdf"))
    ids, by_hash = orphans.load_manifest_index(cfg)
    assert ids == {"aaaa", "bbbb"}
    assert by_hash == {_sha(PDF_A): "aaaa", _sha(PDF_B): "bbbb"}


def test_walk_yields_only_files_whose_stem_is_not_indexed(tmp_path):
    """Any regular file under raw/, at any depth and with any extension, is an
    orphan iff its stem is not a manifest doc_id — indexed files never show up."""
    cfg = _cfg(tmp_path)
    _write(cfg, "us/C1/2015/aaaa.pdf", PDF_A)          # indexed
    _write(cfg, "us/C1/2015/orph1.pdf", PDF_A)         # orphan (dup)
    _write(cfg, "us/C1/2015/orph2.html", NOT_PDF)      # orphan html
    _write(cfg, "us/C1/undated/orph3", TINY)           # orphan, no ext, non-numeric year dir
    _write(cfg, "us/.DS_Store", b"junk")               # shallow orphan is still an orphan
    _index(cfg, "us", _row("aaaa", PDF_A, "us", "us/C1/2015/aaaa.pdf"))
    ids, _ = orphans.load_manifest_index(cfg)
    rels = sorted(p.relative_to(cfg.raw_dir).as_posix() for p in orphans.iter_orphans(cfg, ids))
    assert rels == ["us/.DS_Store", "us/C1/2015/orph1.pdf", "us/C1/2015/orph2.html", "us/C1/undated/orph3"]


def test_walk_restricts_to_requested_banks(tmp_path):
    cfg = _cfg(tmp_path)
    _write(cfg, "us/C1/2015/o1.pdf", PDF_A)
    _write(cfg, "ecb/C1/2015/o2.pdf", PDF_B)
    rels = [p.relative_to(cfg.raw_dir).as_posix() for p in orphans.iter_orphans(cfg, set(), banks={"ecb"})]
    assert rels == ["ecb/C1/2015/o2.pdf"]


def test_classify_marks_duplicates_and_pdf_validity(tmp_path):
    cfg = _cfg(tmp_path)
    dup = _write(cfg, "us/C1/2015/dup.pdf", PDF_A)
    fresh = _write(cfg, "us/C1/2015/fresh.pdf", PDF_B)
    tiny = _write(cfg, "us/C1/undated/tiny", TINY)
    hash_index = {_sha(PDF_A): "aaaa"}
    e = orphans.classify(dup, cfg.raw_dir, hash_index)
    assert (e.rel, e.bank, e.doc_type, e.year_dir, e.ext) == ("us/C1/2015/dup.pdf", "us", "C1", "2015", "pdf")
    assert e.size == len(PDF_A) and e.sha256 == _sha(PDF_A) and e.valid_pdf is True
    assert e.matched_doc_id == "aaaa" and e.action == "would-move"
    assert e.dest == "us/C1/2015/dup.pdf"
    f = orphans.classify(fresh, cfg.raw_dir, hash_index)
    assert f.matched_doc_id is None and f.action == "kept-unindexed" and f.dest is None and f.valid_pdf is True
    t = orphans.classify(tiny, cfg.raw_dir, hash_index)
    assert t.ext == "" and t.year_dir == "undated" and t.valid_pdf is False


def test_classify_shallow_path_leaves_missing_parts_empty(tmp_path):
    cfg = _cfg(tmp_path)
    p = _write(cfg, "us/.DS_Store", b"junk")
    e = orphans.classify(p, cfg.raw_dir, {})
    assert (e.bank, e.doc_type, e.year_dir, e.ext) == ("us", "", "", "")   # ".DS_Store" has no suffix
