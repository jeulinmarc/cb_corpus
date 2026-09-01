"""Concurrency tests for the per-bank sidecar manifest lock (issue #18)."""
import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from cb_corpus.storage import _locked

# Subprocess snippet: hold the sidecar lock of sys.argv[1] for sys.argv[2] seconds.
HOLDER = r"""
import sys, time
from pathlib import Path
from cb_corpus.storage import _locked
with _locked(Path(sys.argv[1])):
    print("HELD", flush=True)
    time.sleep(float(sys.argv[2]))
"""


def _spawn_holder(path: Path, seconds: float) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", HOLDER, str(path), str(seconds)],
                         stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "HELD"   # lock is held before we return
    return p


def test_locked_creates_sidecar_and_excludes(tmp_path):
    data = tmp_path / "xx.jsonl"
    hold = 1.0
    proc = _spawn_holder(data, hold)
    t0 = time.monotonic()
    with _locked(data):
        elapsed = time.monotonic() - t0
    proc.wait()
    assert elapsed >= hold * 0.8, f"lock did not block (elapsed={elapsed:.2f}s)"
    assert (tmp_path / ".xx.jsonl.lock").exists()
    assert not data.exists()          # _locked never creates the data file


def test_append_blocks_on_sidecar(tmp_path):
    from cb_corpus.config import Config
    from cb_corpus.models import DocRecord, DocType
    from cb_corpus.storage import Storage
    st = Storage(Config(data_dir=tmp_path))
    rec = DocRecord(bank_code="xx", doc_type=DocType.D1, title="t",
                    pdf_url="https://example.org/a.pdf")
    data = st.cfg.manifest_file("xx")
    hold = 1.0
    proc = _spawn_holder(data, hold)
    t0 = time.monotonic()
    st._append(rec)
    elapsed = time.monotonic() - t0
    proc.wait()
    assert elapsed >= hold * 0.8, f"_append ignored the sidecar lock ({elapsed:.2f}s)"
    rows = [json.loads(l) for l in data.read_text().splitlines() if l.strip()]
    assert len(rows) == 1 and rows[0]["bank_code"] == "xx"


def _seed(tmp_path, bank="xx", n=3):
    from cb_corpus.config import Config
    cfg = Config(data_dir=tmp_path)
    path = cfg.manifest_file(bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [{"doc_id": f"{bank}-{i}", "bank_code": bank, "title": f"t{i}",
             "pdf_url": f"https://example.org/{bank}/{i}.pdf"} for i in range(n)]
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return cfg, path, rows


def test_apply_row_updates_replaces_and_preserves(tmp_path):
    from cb_corpus.storage import apply_row_updates
    cfg, path, rows = _seed(tmp_path)
    before_lines = path.read_text().splitlines()
    upd = dict(rows[1]); upd["title"] = "CHANGED"
    n = apply_row_updates(cfg, {upd["doc_id"]: upd})
    assert n == 1
    after_lines = path.read_text().splitlines()
    assert len(after_lines) == 3
    assert after_lines[0] == before_lines[0]          # untouched: byte-identical
    assert after_lines[2] == before_lines[2]
    assert json.loads(after_lines[1])["title"] == "CHANGED"


def test_apply_row_updates_unknown_docid_warns_and_skips(tmp_path, capsys):
    from cb_corpus.storage import apply_row_updates
    cfg, path, rows = _seed(tmp_path)
    before = path.read_text()
    ghost = {"doc_id": "xx-ghost", "bank_code": "xx", "title": "g",
             "pdf_url": "https://example.org/g.pdf"}
    n = apply_row_updates(cfg, {"xx-ghost": ghost})
    assert n == 0
    assert path.read_text() == before                  # file untouched by content
    assert "xx-ghost" in capsys.readouterr().err


def test_apply_row_updates_empty_is_noop(tmp_path):
    from cb_corpus.storage import apply_row_updates
    cfg, path, rows = _seed(tmp_path)
    mtime = path.stat().st_mtime_ns
    assert apply_row_updates(cfg, {}) == 0
    assert path.stat().st_mtime_ns == mtime            # not rewritten at all


def test_apply_row_updates_missing_bank_file_warns(tmp_path, capsys):
    from cb_corpus.storage import apply_row_updates
    from cb_corpus.config import Config
    cfg = Config(data_dir=tmp_path)
    row = {"doc_id": "zz-1", "bank_code": "zz", "title": "t",
           "pdf_url": "https://example.org/z.pdf"}
    assert apply_row_updates(cfg, {"zz-1": row}) == 0
    assert not cfg.manifest_file("zz").exists()        # never conjures a file
    assert "zz-1" in capsys.readouterr().err


def test_apply_row_updates_multibank_touches_only_its_banks(tmp_path):
    from cb_corpus.storage import apply_row_updates
    cfg, path_xx, rows_xx = _seed(tmp_path, bank="xx")
    _,   path_yy, rows_yy = _seed(tmp_path, bank="yy")
    _,   path_zz, _       = _seed(tmp_path, bank="zz")
    zz_before = path_zz.read_text()
    u1 = dict(rows_xx[0]); u1["title"] = "X"
    u2 = dict(rows_yy[2]); u2["title"] = "Y"
    n = apply_row_updates(cfg, {u1["doc_id"]: u1, u2["doc_id"]: u2})
    assert n == 2
    assert json.loads(path_xx.read_text().splitlines()[0])["title"] == "X"
    assert json.loads(path_yy.read_text().splitlines()[2])["title"] == "Y"
    assert path_zz.read_text() == zz_before


def test_rewrite_manifest_refreshes_indexes(tmp_path):
    from cb_corpus.config import Config
    from cb_corpus.storage import Storage
    cfg, path, rows = _seed(tmp_path)
    st = Storage(cfg)
    upd = dict(rows[0]); upd["pdf_url"] = "https://example.org/NEW.pdf"
    assert st.rewrite_manifest({upd["doc_id"]: upd}) == 1
    assert st.is_known_url("https://example.org/NEW.pdf")
