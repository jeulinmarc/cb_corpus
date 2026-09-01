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
