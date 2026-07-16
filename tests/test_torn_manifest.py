"""Torn-manifest resilience (audit C1 — task H1).

A JSONL manifest line torn by SIGKILL/ENOSPC mid-append currently wedges
every subsequent run (strict json.loads at Storage init). The fix in
storage.iter_manifest_rows distinguishes:

  - a malformed FINAL line  -> a lost append, REPAIR: truncate it off
    atomically, save the fragment to `<file>.torn`, warn loudly, continue.
  - a malformed NON-final line -> real corruption, RAISE naming file+line.

Blank lines are skipped as before. An intact file must never be rewritten.
"""
from __future__ import annotations

import json

from cb_corpus.config import Config
from cb_corpus.storage import iter_manifest_rows


def _write(path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# --- (i) torn FINAL line: repaired, loaded rows = all-but-torn ---
def test_torn_final_line_is_repaired(tmp_path, capsys):
    cfg = Config(data_dir=tmp_path)
    f = cfg.manifest_file("us")
    good1 = json.dumps({"doc_id": "a"}) + "\n"
    good2 = json.dumps({"doc_id": "b"}) + "\n"
    torn = '{"doc_id": "c", "title": "trunc'   # no closing brace/quote, no \n
    _write(f, (good1 + good2 + torn).encode())

    rows = list(iter_manifest_rows(cfg, "us"))

    assert rows == [{"doc_id": "a"}, {"doc_id": "b"}]

    # file repaired on disk: only the two intact lines remain
    assert f.read_bytes() == (good1 + good2).encode()

    # fragment preserved for forensics
    torn_path = f.with_name(f.name + ".torn")
    assert torn_path.exists()
    assert torn_path.read_bytes() == torn.encode()

    # one loud stderr warning naming file + byte offset
    err = capsys.readouterr().err
    assert str(f) in err
    assert str(len((good1 + good2).encode())) in err


# --- (ii) torn MIDDLE line: raises, message names file + line ---
def test_torn_middle_line_raises(tmp_path):
    cfg = Config(data_dir=tmp_path)
    f = cfg.manifest_file("us")
    good1 = json.dumps({"doc_id": "a"}) + "\n"
    bad = "{not json at all}\n"
    good2 = json.dumps({"doc_id": "b"}) + "\n"
    _write(f, (good1 + bad + good2).encode())

    import pytest
    with pytest.raises(ValueError) as exc_info:
        list(iter_manifest_rows(cfg, "us"))

    msg = str(exc_info.value)
    assert str(f) in msg
    assert ":2:" in msg or "line 2" in msg.lower()

    # non-final corruption must NOT repair/truncate the file
    assert f.read_bytes() == (good1 + bad + good2).encode()
    assert not f.with_name(f.name + ".torn").exists()


# --- (iii) intact file: byte-identical after a load, no .torn written ---
def test_intact_file_is_byte_identical_after_load(tmp_path):
    cfg = Config(data_dir=tmp_path)
    f = cfg.manifest_file("us")
    content = (json.dumps({"doc_id": "a"}) + "\n"
               + json.dumps({"doc_id": "b"}) + "\n"
               + json.dumps({"doc_id": "c"}) + "\n")
    _write(f, content.encode())
    before = f.read_bytes()
    before_mtime_ns = f.stat().st_mtime_ns

    rows = list(iter_manifest_rows(cfg, "us"))

    assert rows == [{"doc_id": "a"}, {"doc_id": "b"}, {"doc_id": "c"}]
    assert f.read_bytes() == before
    assert f.stat().st_mtime_ns == before_mtime_ns
    assert not f.with_name(f.name + ".torn").exists()


# --- (iv) empty file / trailing-newline-only file: zero rows, no crash ---
def test_empty_and_blank_only_files_load_as_zero_rows(tmp_path):
    cfg = Config(data_dir=tmp_path)

    f_empty = cfg.manifest_file("us")
    _write(f_empty, b"")
    assert list(iter_manifest_rows(cfg, "us")) == []
    assert f_empty.read_bytes() == b""

    f_blank = cfg.manifest_file("fr")
    _write(f_blank, b"\n\n   \n")
    assert list(iter_manifest_rows(cfg, "fr")) == []
    # blank-only file left untouched (nothing to repair — no content line at all)
    assert f_blank.read_bytes() == b"\n\n   \n"
