"""Download failures are persisted to data/download_errors.jsonl."""
import json

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.storage import Storage
from cb_corpus.taxonomy import DocType


class _BoomFetcher:
    def get_bytes(self, url):
        raise RuntimeError("HTTP 404: gone")


def _rec(**kw):
    base = dict(bank_code="gb", doc_type=DocType.D1, title="t",
                pdf_url="https://x.test/dead.pdf", source_url="https://ideas.test/p/1.html",
                provenance="repec_discovery")
    base.update(kw)
    return DocRecord(**base)


def _mk_storage(tmp_path):
    (tmp_path / "manifest").mkdir(parents=True)
    return Storage(Config(data_dir=tmp_path), _BoomFetcher())


def test_failed_download_writes_one_audit_line(tmp_path):
    st = _mk_storage(tmp_path)
    counts = st.save_many([_rec()], dry_run=False, label="repec:gb")
    assert counts == {"error": 1}
    lines = (tmp_path / "download_errors.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["label"] == "repec:gb"
    assert row["bank_code"] == "gb"
    assert row["pdf_url"] == "https://x.test/dead.pdf"
    assert row["source_url"] == "https://ideas.test/p/1.html"
    assert "RuntimeError" in row["error"] and "\n" not in row["error"]
    assert row["ts"].endswith("Z") or "+" in row["ts"]


def test_dry_run_writes_no_audit_line(tmp_path):
    st = _mk_storage(tmp_path)
    st.save_many([_rec()], dry_run=True, label="repec:gb")
    assert not (tmp_path / "download_errors.jsonl").exists()


def test_successful_saves_write_no_audit_line(tmp_path):
    class _OkFetcher:
        def get_bytes(self, url):
            return b"%PDF-fake", "application/pdf"
    (tmp_path / "manifest").mkdir(parents=True)
    st = Storage(Config(data_dir=tmp_path), _OkFetcher())
    counts = st.save_many([_rec()], dry_run=False, label="repec:gb")
    assert counts.get("error") is None
    assert not (tmp_path / "download_errors.jsonl").exists()
