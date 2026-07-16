"""save() short-circuits on a known source_url — no fetch, both modes."""
import json

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.storage import Storage
from cb_corpus.taxonomy import DocType


class _SpyFetcher:
    def __init__(self):
        self.calls = 0
    def get_bytes(self, url):
        self.calls += 1
        return b"%PDF-fake", "application/pdf"


def _manifest_row(source_url):
    return {"bank_code": "gb", "doc_type": "D1", "title": "old paper",
            "pdf_url": "https://boe.test/modern/slug.pdf", "source_url": source_url,
            "date": "2005-01-01", "language": "en", "provenance": "bank_site",
            "mime_type": "application/pdf", "sha256": "aa", "local_path": "x",
            "doc_id": "deadbeef00000001", "year": 2005}


def _mk(tmp_path, source_url="https://ideas.test/p/boe/1.html"):
    (tmp_path / "manifest").mkdir(parents=True)
    (tmp_path / "manifest" / "gb.jsonl").write_text(json.dumps(_manifest_row(source_url)) + "\n")
    f = _SpyFetcher()
    return Storage(Config(data_dir=tmp_path), f), f


def _rec(source_url):
    return DocRecord(bank_code="gb", doc_type=DocType.D1, title="old paper",
                     pdf_url="https://boe.test/DEAD/old-url.pdf",
                     source_url=source_url, provenance="repec_discovery")


def test_known_source_skips_without_fetch(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/1.html")) == "skip:known-source"
    assert f.calls == 0


def test_known_source_skips_in_dry_run_too(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/1.html"), dry_run=True) == "skip:known-source"
    assert f.calls == 0


def test_unknown_or_empty_source_does_not_skip(tmp_path):
    st, f = _mk(tmp_path)
    assert st.save(_rec("https://ideas.test/p/boe/2.html")) != "skip:known-source"
    assert st.save(_rec("")) != "skip:known-source"
