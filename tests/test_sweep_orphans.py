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


# --- Task 2: move + sweep ------------------------------------------------------

def _corpus(tmp_path):
    """Indexed aaaa (PDF_A); orphans: dup.pdf (=PDF_A), fresh.pdf (PDF_B, unknown),
    page.html (=PDF_A bytes, dup), undated/blob (TINY, unknown)."""
    cfg = _cfg(tmp_path)
    _write(cfg, "us/C1/2015/aaaa.pdf", PDF_A)
    _write(cfg, "us/C1/2015/dup.pdf", PDF_A)
    _write(cfg, "us/C1/2015/fresh.pdf", PDF_B)
    _write(cfg, "us/C1/2015/page.html", PDF_A)
    _write(cfg, "us/C1/undated/blob", TINY)
    _index(cfg, "us", _row("aaaa", PDF_A, "us", "us/C1/2015/aaaa.pdf"))
    return cfg


def test_dry_run_reports_but_moves_nothing(tmp_path):
    cfg = _corpus(tmp_path)
    s = orphans.sweep_orphans(cfg)
    assert s.dry_run is True and s.files_seen == 5 and s.orphans == 4
    assert s.duplicates == 2 and s.unindexed == 2 and s.moved == 0
    assert s.bytes_duplicates == 2 * len(PDF_A)
    assert (cfg.raw_dir / "us/C1/2015/dup.pdf").exists()
    assert not cfg.orphans_dir.exists()
    rows = [json.loads(l) for l in Path(s.report_path).read_text().splitlines()]
    assert {r["rel"]: r["action"] for r in rows} == {
        "us/C1/2015/dup.pdf": "would-move", "us/C1/2015/page.html": "would-move",
        "us/C1/2015/fresh.pdf": "kept-unindexed", "us/C1/undated/blob": "kept-unindexed"}
    assert {r["rel"]: r["valid_pdf"] for r in rows}["us/C1/undated/blob"] is False
    assert Path(s.report_path).with_suffix(".summary.json").exists()


def test_move_quarantines_duplicates_in_a_mirror_tree(tmp_path):
    cfg = _corpus(tmp_path)
    s = orphans.sweep_orphans(cfg, move=True)
    assert s.moved == 2 and s.move_failed == 0 and s.dry_run is False
    assert not (cfg.raw_dir / "us/C1/2015/dup.pdf").exists()
    assert (cfg.orphans_dir / "us/C1/2015/dup.pdf").read_bytes() == PDF_A
    assert (cfg.orphans_dir / "us/C1/2015/page.html").read_bytes() == PDF_A
    # never touched: the indexed file and the unindexed orphans
    assert (cfg.raw_dir / "us/C1/2015/aaaa.pdf").exists()
    assert (cfg.raw_dir / "us/C1/2015/fresh.pdf").exists()
    assert (cfg.raw_dir / "us/C1/undated/blob").exists()
    rows = [json.loads(l) for l in Path(s.report_path).read_text().splitlines()]
    assert sorted(r["action"] for r in rows) == ["kept-unindexed", "kept-unindexed", "moved", "moved"]


def test_indexed_file_is_never_moved_even_if_it_duplicates_another_row(tmp_path):
    """Two indexed rows with identical bytes: both files stay, neither is an orphan."""
    cfg = _cfg(tmp_path)
    _write(cfg, "us/C1/2015/aaaa.pdf", PDF_A)
    _write(cfg, "us/C1/2016/cccc.pdf", PDF_A)
    _index(cfg, "us", _row("aaaa", PDF_A, "us", "us/C1/2015/aaaa.pdf"),
           _row("cccc", PDF_A, "us", "us/C1/2016/cccc.pdf"))
    s = orphans.sweep_orphans(cfg, move=True)
    assert s.orphans == 0 and s.moved == 0
    assert (cfg.raw_dir / "us/C1/2015/aaaa.pdf").exists() and (cfg.raw_dir / "us/C1/2016/cccc.pdf").exists()


def test_two_unindexed_orphans_with_the_same_bytes_both_stay(tmp_path):
    cfg = _cfg(tmp_path)
    _write(cfg, "us/C1/2015/o1.pdf", PDF_B)
    _write(cfg, "us/C1/2015/o2.pdf", PDF_B)
    s = orphans.sweep_orphans(cfg, move=True)
    assert s.orphans == 2 and s.unindexed == 2 and s.moved == 0


def test_second_run_is_a_no_op_and_replay_over_identical_dest_removes_source(tmp_path):
    cfg = _corpus(tmp_path)
    orphans.sweep_orphans(cfg, move=True)
    s2 = orphans.sweep_orphans(cfg, move=True)
    assert s2.duplicates == 0 and s2.moved == 0 and s2.unindexed == 2
    # replay: the same duplicate reappears in raw/ while its copy already sits in raw_orphans/
    _write(cfg, "us/C1/2015/dup.pdf", PDF_A)
    s3 = orphans.sweep_orphans(cfg, move=True)
    assert s3.moved == 1 and not (cfg.raw_dir / "us/C1/2015/dup.pdf").exists()
    assert (cfg.orphans_dir / "us/C1/2015/dup.pdf").read_bytes() == PDF_A


def test_dest_with_different_content_is_a_counted_failure_not_an_overwrite(tmp_path):
    cfg = _corpus(tmp_path)
    clash = cfg.orphans_dir / "us/C1/2015/dup.pdf"
    clash.parent.mkdir(parents=True)
    clash.write_bytes(PDF_B)
    s = orphans.sweep_orphans(cfg, move=True)
    assert s.move_failed == 1 and s.moved == 1
    assert (cfg.raw_dir / "us/C1/2015/dup.pdf").read_bytes() == PDF_A      # source untouched
    assert clash.read_bytes() == PDF_B                                       # dest untouched
    rows = {json.loads(l)["rel"]: json.loads(l) for l in Path(s.report_path).read_text().splitlines()}
    assert rows["us/C1/2015/dup.pdf"]["action"] == "move-failed"
    assert "different content" in rows["us/C1/2015/dup.pdf"]["error"]


def test_report_file_names_use_the_utc_timestamp(tmp_path):
    from datetime import datetime, timezone
    cfg = _corpus(tmp_path)
    s = orphans.sweep_orphans(cfg, now=datetime(2026, 9, 3, 14, 5, 6, tzinfo=timezone.utc))
    assert Path(s.report_path) == cfg.reports_dir / "orphan_sweep_20260903T140506Z.jsonl"
    summary = json.loads((cfg.reports_dir / "orphan_sweep_20260903T140506Z.summary.json").read_text())
    assert summary["orphans"] == 4 and summary["dry_run"] is True and summary["started_at"].startswith("2026-09-03T14:05:06")


def test_progress_lines_go_to_stderr(tmp_path, capsys):
    cfg = _corpus(tmp_path)
    orphans.sweep_orphans(cfg, progress_every=2)
    err = capsys.readouterr().err
    assert "sweep-orphans: examined 2 files" in err and "examined 4 files" in err
