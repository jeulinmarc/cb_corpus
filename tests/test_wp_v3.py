"""WP v3 first PR: schema, ECB foedb scraper, and migration-join tests.

Pure-helper + JSON-fixture style (mirrors tests/test_framework.py). The foedb
fixtures under tests/fixtures/ are trimmed real responses captured June 2026.
"""
import json
from datetime import date, datetime
from pathlib import Path

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.storage import Storage, iter_manifest_rows
from cb_corpus.taxonomy import DocType
from cb_corpus.sources.ecb_foedb import (
    parse_versions, parse_metadata, chunk_records, wp_op_from_record,
    ecb_wp_number, repec_ecb_number, discover_ecb_wp, FOEDB_DB,
)
from cb_corpus import wp_migrate
from cb_corpus.wp_migrate import (
    build_report, normalize_url, repec_handle_from_source_url,
)
from cb_corpus.adapters.ecb import ECBAdapter
from cb_corpus.banks import get_bank
import cb_corpus.sources.ecb_foedb as ecb_foedb

FIX = Path(__file__).parent / "fixtures"


# ---- Phase 0 schema --------------------------------------------------
def test_docrecord_date_quality_defaults():
    rec = DocRecord(bank_code="ecb", doc_type=DocType.D1, title="x",
                    pdf_url="https://x/wp.pdf")
    assert (rec.date_precision, rec.date_source, rec.repec_handle) == ("day", "bank_site", "")


def test_to_row_serializes_alt_urls_and_quality_fields():
    rec = DocRecord(bank_code="ecb", doc_type=DocType.D1, title="x",
                    pdf_url="https://x/wp.pdf", alt_urls=["https://alt/wp.pdf"],
                    date_precision="month", date_source="repec",
                    repec_handle="RePEc:ecb:ecbwps:20253117")
    row = rec.to_row()
    assert row["alt_urls"] == ["https://alt/wp.pdf"]
    assert row["date_precision"] == "month"
    assert row["date_source"] == "repec"
    assert row["repec_handle"] == "RePEc:ecb:ecbwps:20253117"


def test_load_existing_indexes_alt_urls(tmp_path):
    cfg = Config(data_dir=tmp_path)
    Storage(cfg)  # ensures dirs
    row = {"doc_id": "d1", "bank_code": "ecb", "doc_type": "D1",
           "pdf_url": "https://x//pub/wp.pdf", "alt_urls": ["https://native/wp.pdf"],
           "sha256": "abc"}
    cfg.manifest_path.write_text(json.dumps(row) + "\n")
    st = Storage(cfg)
    assert st.is_known_url("https://x//pub/wp.pdf")
    assert st.is_known_url("https://native/wp.pdf")     # alt url recognised
    assert not st.is_known_url("https://unknown/wp.pdf")


def test_load_existing_tolerates_null_alt_urls(tmp_path):
    cfg = Config(data_dir=tmp_path)
    Storage(cfg)
    row = {"doc_id": "d1", "bank_code": "ecb", "doc_type": "D1",
           "pdf_url": "https://x/wp.pdf", "alt_urls": None}
    cfg.manifest_path.write_text(json.dumps(row) + "\n")
    st = Storage(cfg)                                    # must not raise on None
    assert st.is_known_url("https://x/wp.pdf")


def test_rewrite_manifest_is_atomic_and_idempotent(tmp_path):
    cfg = Config(data_dir=tmp_path)
    st = Storage(cfg)
    rows = [
        {"doc_id": "a", "bank_code": "ecb", "doc_type": "D1",
         "pdf_url": "https://x/a.pdf", "alt_urls": ["https://x/a-alt.pdf"], "date": "2020-01-02"},
        {"doc_id": "b", "bank_code": "ecb", "doc_type": "D2",
         "pdf_url": "https://x/b.pdf", "alt_urls": [], "date": "2021-03-04"},
    ]
    assert st.rewrite_manifest(rows) == 2
    # rows go to the per-bank file data/manifest/ecb.jsonl
    ecb_file = cfg.manifest_file("ecb")
    on_disk = [json.loads(l) for l in ecb_file.read_text().splitlines() if l.strip()]
    assert on_disk == rows
    assert not ecb_file.with_suffix(".jsonl.tmp").exists()
    # dedup indexes refreshed from the new content, incl. alt_urls
    assert st.is_known_url("https://x/a.pdf") and st.is_known_url("https://x/a-alt.pdf")
    # idempotent: rewriting the same rows yields the same file
    st.rewrite_manifest(rows)
    again = [json.loads(l) for l in ecb_file.read_text().splitlines() if l.strip()]
    assert again == rows


# ---- ECB foedb parsers ----------------------------------------------
def test_parse_versions_and_metadata():
    v, h = parse_versions(json.loads((FIX / "ecb_foedb_versions.json").read_text()))
    assert (v, h) == ("1781277750", "SGNKO2Ue")
    total, size, header = parse_metadata(json.loads((FIX / "ecb_foedb_metadata.json").read_text()))
    assert size == 250 and total >= 1 and "documentTypes" in header and "pub_timestamp" in header


def test_chunk_records_and_wp_op_extraction():
    meta = json.loads((FIX / "ecb_foedb_metadata.json").read_text())
    header = meta["header"]
    flat = json.loads((FIX / "ecb_foedb_chunk0.json").read_text())
    recs = chunk_records(flat, header)
    parsed = [wp_op_from_record(r) for r in recs]
    wp = [p for p in parsed if p and p[0] == DocType.D1]
    op = [p for p in parsed if p and p[0] == DocType.D2]
    other = [p for p in parsed if p is None]
    assert len(wp) == 2 and len(op) == 2 and len(other) == 2     # 2 WP + 2 OP + 2 non-WP
    # the known recent WP: number, exact day, absolute single-slash URL, real title
    wp3244 = next(p for p in wp if p[1] == 3244)
    _dt, number, title, d, pdf_url = wp3244
    assert number == 3244 and d == date(2026, 6, 3) and title
    assert pdf_url == "https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~0e92afef7d.en.pdf"


def test_record_date_uses_berlin_not_utc():
    """A local-midnight pub_timestamp (how ECB stores older papers' dates) must
    resolve to the Berlin calendar day, NOT the UTC previous day. ~25% of the
    archive is stored this way; a UTC reading would date them one day early."""
    from zoneinfo import ZoneInfo
    # 15 Jan 2020 00:00 Berlin (CET, +01:00) == 14 Jan 2020 23:00 UTC.
    ts = int(datetime(2020, 1, 15, 0, 0, tzinfo=ZoneInfo("Europe/Berlin")).timestamp())
    rec = {"pub_timestamp": ts, "publicationProperties": {"Title": "Old WP"},
           "documentTypes": ["/pub/pdf/scpwps/ecbwp1000.pdf"]}
    doc_type, number, _title, d, _url = wp_op_from_record(rec)
    assert (doc_type, number) == (DocType.D1, 1000)
    assert d == date(2020, 1, 15)         # Berlin day, not 2020-01-14


def test_ecb_wp_number_handles_all_url_shapes():
    B = "https://www.ecb.europa.eu//pub/pdf/"
    # hash AFTER the number (modern), and no-hash legacy forms
    assert ecb_wp_number(B + "scpwps/ecb.wp3124~475c.en.pdf") == (DocType.D1, 3124)
    assert ecb_wp_number(B + "scpwps/ecbwp722.pdf") == (DocType.D1, 722)
    assert ecb_wp_number(B + "scpops/ecb.op388.en.pdf") == (DocType.D2, 388)
    assert ecb_wp_number(B + "scpops/ecbocp2.pdf") == (DocType.D2, 2)
    # hash BEFORE the number (the variant that broke the first matcher)
    assert ecb_wp_number(B + "scpwps/ecb~44d02b04fd.wp3181en.pdf") == (DocType.D1, 3181)
    assert ecb_wp_number(B + "scpwps/ecb~0131e2da81.wp2585_en.pdf") == (DocType.D1, 2585)
    assert ecb_wp_number(B + "scpops/ecb~ae799b1df9.op370en.pdf") == (DocType.D2, 370)
    assert ecb_wp_number("https://www.ecb.europa.eu/pub/pdf/other/x.pdf") is None


def test_repec_ecb_number_from_handle_and_source_url():
    assert repec_ecb_number("https://ideas.repec.org/p/ecb/ecbwps/20253124.html") == (DocType.D1, 3124)
    assert repec_ecb_number("https://ideas.repec.org/p/ecb/ecbops/20022.html") == (DocType.D2, 2)
    assert repec_ecb_number("RePEc:ecb:ecbwps:20263244") == (DocType.D1, 3244)
    assert repec_ecb_number("https://ideas.repec.org/p/boe/boeewp/1234.html") is None


class _FakeFetcher:
    def __init__(self, mapping):
        self.mapping = mapping

    def get_text(self, url):
        for suffix, content in self.mapping.items():
            if url.endswith(suffix):
                return content
        raise AssertionError(f"unexpected url {url}")


def _foedb_fetcher():
    return _FakeFetcher({
        "versions.json": (FIX / "ecb_foedb_versions.json").read_text(),
        "metadata.json": (FIX / "ecb_foedb_metadata.json").read_text(),
        "chunk_0.json": (FIX / "ecb_foedb_chunk0.json").read_text(),
    })


def test_discover_ecb_wp_yields_d1_and_d2():
    recs = list(discover_ecb_wp(_foedb_fetcher()))
    assert {r.doc_type for r in recs} == {DocType.D1, DocType.D2}
    assert len(recs) == 4
    for r in recs:
        assert r.provenance == "bank_site"
        assert r.date_precision == "day" and r.date_source == "bank_site"
        assert r.pdf_url.startswith("https://www.ecb.europa.eu/pub/pdf/")


def test_discover_ecb_wp_since_early_stops():
    # since after the newest record -> nothing; since before -> all four.
    assert list(discover_ecb_wp(_foedb_fetcher(), since=date(2030, 1, 1))) == []
    assert len(list(discover_ecb_wp(_foedb_fetcher(), since=date(2000, 1, 1)))) == 4


# ---- migration helpers + join ---------------------------------------
def test_normalize_url_collapses_slash_and_hash():
    a = "https://www.ecb.europa.eu//pub/pdf/scpwps/ecb.wp3244~0e92afef7d.en.pdf"
    b = "https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~deadbeef.en.pdf"
    assert normalize_url(a) == normalize_url(b)


def test_repec_handle_from_source_url():
    assert repec_handle_from_source_url(
        "https://ideas.repec.org/p/ecb/ecbwps/20263244.html") == "RePEc:ecb:ecbwps:20263244"
    assert repec_handle_from_source_url("https://example.com/x") == ""


def test_build_report_join_classifies_and_proposes(monkeypatch):
    native = [
        DocRecord(bank_code="ecb", doc_type=DocType.D1, title="WP 3244",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~aa.en.pdf",
                  date=date(2026, 6, 3)),
        DocRecord(bank_code="ecb", doc_type=DocType.D1, title="WP 3243",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3243~bb.en.pdf",
                  date=date(2026, 6, 3)),
        DocRecord(bank_code="ecb", doc_type=DocType.D2, title="OP 388",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpops/ecb.op388.en.pdf",
                  date=date(2026, 2, 1)),
    ]
    monkeypatch.setitem(wp_migrate._NATIVE, "ecb", lambda fetcher: iter(native))

    manifest = [
        # A: month row, matches 3244 by key -> proposed change
        {"doc_id": "aaa", "bank_code": "ecb", "doc_type": "D1", "date": "2026-06-01",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpwps/ecb.wp3244~aa.en.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbwps/20263244.html"},
        # C: already migrated (matches 3243) -> counted already_day, no change
        {"doc_id": "ccc", "bank_code": "ecb", "doc_type": "D1", "date": "2026-06-03",
         "date_precision": "day", "date_source": "bank_site",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpwps/ecb.wp3243~bb.en.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbwps/20263243.html"},
        # B: legacy OP number 2, no native match -> unmatched
        {"doc_id": "bbb", "bank_code": "ecb", "doc_type": "D2", "date": "2002-02-01",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpops/ecbocp2.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbops/20022.html"},
        # F: month OP 388 matches -> proposed change
        {"doc_id": "fff", "bank_code": "ecb", "doc_type": "D2", "date": "2026-02-01",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpops/ecb.op388.en.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbops/2026388.html"},
        # E: different bank -> ignored entirely
        {"doc_id": "eee", "bank_code": "us", "doc_type": "D1", "date": "2020-01-01",
         "pdf_url": "https://federalreserve.gov/x.pdf", "source_url": ""},
    ]

    summary, changes = build_report("ecb", fetcher=None, manifest_rows=manifest)
    assert summary["manifest_total"] == 4          # E (us) excluded
    assert summary["matched_key"] == 3             # A, C, F
    assert summary["matched_url"] == 0
    assert summary["already_day"] == 1             # C
    assert summary["unmatched_manifest"] == 1      # B
    assert summary["native_only"] == 0             # all 3 native keys matched

    by_id = {c["doc_id"]: c for c in changes}
    assert set(by_id) == {"aaa", "fff"}            # only the two month rows change
    a = by_id["aaa"]
    assert a["old_date"] == "2026-06-01" and a["new_date"] == "2026-06-03"
    assert a["match_type"] == "key"
    assert a["repec_handle"] == "RePEc:ecb:ecbwps:20263244"
    assert a["alt_url_added"] == "https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~aa.en.pdf"
    # invariant #1: doc_id never changes during migration
    assert a["doc_id"] == "aaa"


def test_ecb_d1_d2_are_native_and_skip_known_urls(monkeypatch):
    """After the flip, ECB D1/D2 discover via foedb (not RePEc), the doc_type
    filter holds, and the pipeline's is_known_url hook skips already-known papers
    (zero re-download of the migrated back-catalogue)."""
    assert DocType.D1 in ECBAdapter.native_types and DocType.D2 in ECBAdapter.native_types
    recs = [
        DocRecord(bank_code="ecb", doc_type=DocType.D1, title="a",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp1.en.pdf"),
        DocRecord(bank_code="ecb", doc_type=DocType.D1, title="b",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp2.en.pdf"),
        DocRecord(bank_code="ecb", doc_type=DocType.D2, title="c",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpops/ecb.op1.en.pdf"),
    ]
    monkeypatch.setattr(ecb_foedb, "discover_ecb_wp",
                        lambda fetcher, since=None: iter(recs))
    ad = ECBAdapter(get_bank("ecb"), fetcher=object())   # fetcher unused (patched)

    # routes to native foedb and filters by doc_type (D1 -> a,b ; not the RePEc path)
    assert [r.title for r in ad.discover(DocType.D1)] == ["a", "b"]
    assert [r.title for r in ad.discover(DocType.D2)] == ["c"]

    # is_known_url hook skips known URLs before download
    ad._skip_known_url = lambda u: u.endswith("wp1.en.pdf")
    assert [r.title for r in ad.discover(DocType.D1)] == ["b"]


# ---- Fed (us) FEDS/IFDP scraper -------------------------------------
from cb_corpus.sources.fed_wp import (
    parse_year_links, parse_landing, fed_key_from_url, fed_key_from_handle,
    discover_fed_wp, FED,
)

_FEDS_YEAR_HTML = """
<div class="heading feds-note" id="2025110">
  <span class="badge badge--feds"><strong>FEDS</strong> 2025-110 </span>
  <div><time datetime="December 2025"> December 2025 </time>
    <h5><a href="/econres/feds/slug-a.htm">Paper A</a></h5></div>
</div>
<div class="heading feds-note" id="2025050">
  <span class="badge badge--feds"><strong>FEDS</strong> 2025-050 </span>
  <div><time datetime="March 2025"> March 2025 </time>
    <h5><a href="/econres/feds/slug-b.htm">Paper B (revised)</a></h5></div>
</div>
"""
# A: landing day falls in the listing month -> day precision.
_LANDING_A = ('<meta name="citation_publication_date" content="12-22-2025" />'
              '<a href="/econres/feds/files/2025110pap.pdf">PDF</a>')
# B: landing date is a later-year revision -> month precision (listing month kept).
_LANDING_B = ('<meta name="citation_publication_date" content="01-15-2026" />'
              '<a href="/econres/feds/files/2025050pap.pdf">PDF</a>')


def test_parse_year_links_and_landing():
    entries = parse_year_links(_FEDS_YEAR_HTML, "feds")
    assert [(e[0], e[1], e[2]) for e in entries] == [("feds", 2025, 110), ("feds", 2025, 50)]
    assert entries[0][3] == date(2025, 12, 1) and entries[0][4].endswith("/slug-a.htm")
    cpd, pdf = parse_landing(_LANDING_A)
    assert cpd == date(2025, 12, 22)
    assert pdf == FED + "/econres/feds/files/2025110pap.pdf"


def test_fed_key_extraction_and_consistency():
    # URL forms (FEDS modern uses NNNpap.pdf; IFDP modern uses files/ifdp{seq}.pdf)
    assert fed_key_from_url(FED + "/econres/feds/files/2022086pap.pdf") == ("feds", 2022, 86)
    assert fed_key_from_url(FED + "/econres/ifdp/files/ifdp1429.pdf") == ("ifdp", 1429)
    assert fed_key_from_url(FED + "/pubs/feds/1997/199711/199711abs.html") == ("feds", 1997, 11)
    assert fed_key_from_url(FED + "/pubs/ifdp/2000/694/ifdp694.pdf") == ("ifdp", 694)
    # revised papers carry an r<N> infix but keep the same number
    assert fed_key_from_url(FED + "/econres/feds/files/2025101r1pap.pdf") == ("feds", 2025, 101)
    assert fed_key_from_url(FED + "/econres/ifdp/files/ifdp1429r2.pdf") == ("ifdp", 1429)
    # handle / IDEAS-path forms
    assert fed_key_from_handle("https://ideas.repec.org/p/fip/fedgfe/2022-82.html") == ("feds", 2022, 82)
    assert fed_key_from_handle("RePEc:fip:fedgfe:95-24") == ("feds", 1995, 24)
    assert fed_key_from_handle("https://ideas.repec.org/p/fip/fedgif/694.html") == ("ifdp", 694)
    assert fed_key_from_handle("https://ideas.repec.org/p/fip/fedgfe/103343.html") is None
    # native PDF key == manifest handle key for the same paper (the join works)
    assert (fed_key_from_url(FED + "/econres/feds/files/2022086pap.pdf")
            == fed_key_from_handle("RePEc:fip:fedgfe:2022-86"))
    assert (fed_key_from_url(FED + "/pubs/ifdp/2000/694/ifdp694.pdf")
            == fed_key_from_handle("RePEc:fip:fedgif:694"))


def test_apply_change_preserves_native_precision():
    """A Fed paper with no confirmed day must migrate to month precision, not be
    mislabeled 'day'. apply_change honours the change's date_precision."""
    from cb_corpus.wp_migrate import apply_change
    row = {"date": "2022-12-01", "pdf_url": "https://x/a.pdf", "alt_urls": []}
    apply_change(row, {"new_date": "2022-12-01", "date_precision": "month",
                       "repec_handle": "RePEc:fip:fedgfe:2022-86",
                       "alt_url_added": "https://y/a.pdf"})
    assert row["date_precision"] == "month" and row["date_source"] == "bank_site"
    assert row["repec_handle"] == "RePEc:fip:fedgfe:2022-86"
    assert "https://y/a.pdf" in row["alt_urls"]


def test_discover_fed_wp_month_constraint(monkeypatch):
    pages = {
        "feds/all-years": '<a href="/econres/feds/2025.htm">2025</a>',
        "ifdp/all-years": "",                       # no IFDP years -> skip
        "feds/2025.htm": _FEDS_YEAR_HTML,
        "feds/slug-a.htm": _LANDING_A,
        "feds/slug-b.htm": _LANDING_B,
    }

    class F:
        def get_text(self, url):
            for k, v in pages.items():
                if k in url:
                    return v
            raise AssertionError(f"unexpected {url}")

    recs = list(discover_fed_wp(F()))
    assert len(recs) == 2 and all(r.bank_code == "us" and r.doc_type == DocType.D1 for r in recs)
    a = next(r for r in recs if "2025110" in r.pdf_url)
    b = next(r for r in recs if "2025050" in r.pdf_url)
    assert a.date == date(2025, 12, 22) and a.date_precision == "day"     # day in listing month
    assert b.date == date(2025, 3, 1) and b.date_precision == "month"     # revision -> month kept
    assert a.date_source == "bank_site" and a.provenance == "bank_site"


def test_run_wp_migrate_write_applies_in_place_and_is_idempotent(tmp_path, monkeypatch):
    native = [
        DocRecord(bank_code="ecb", doc_type=DocType.D1, title="WP 3244",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~aa.en.pdf",
                  date=date(2026, 6, 3)),
        DocRecord(bank_code="ecb", doc_type=DocType.D2, title="OP 388",
                  pdf_url="https://www.ecb.europa.eu/pub/pdf/scpops/ecb.op388.en.pdf",
                  date=date(2026, 2, 10)),
    ]
    monkeypatch.setitem(wp_migrate._NATIVE, "ecb", lambda fetcher: iter(native))

    cfg = Config(data_dir=tmp_path)
    Storage(cfg)  # ensure dirs exist
    rows = [
        {"doc_id": "id3244", "bank_code": "ecb", "doc_type": "D1", "date": "2026-06-01",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpwps/ecb.wp3244~aa.en.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbwps/20263244.html",
         "sha256": "h1", "local_path": "/raw/3244.pdf"},
        {"doc_id": "id388", "bank_code": "ecb", "doc_type": "D2", "date": "2026-02-01",
         "pdf_url": "https://www.ecb.europa.eu//pub/pdf/scpops/ecb.op388.en.pdf",
         "source_url": "https://ideas.repec.org/p/ecb/ecbops/2026388.html",
         "sha256": "h2", "local_path": "/raw/388.pdf"},
        {"doc_id": "idus", "bank_code": "us", "doc_type": "D1", "date": "2020-01-01",
         "pdf_url": "https://federalreserve.gov/x.pdf", "source_url": ""},
    ]
    cfg.manifest_path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

    wp_migrate.run_wp_migrate(bank_codes=["ecb"], write=True, config=cfg)
    after = {r["doc_id"]: r for r in iter_manifest_rows(cfg)}
    assert len(after) == 3                                   # no rows lost/gained
    a = after["id3244"]
    assert a["date"] == "2026-06-03"
    assert a["date_precision"] == "day" and a["date_source"] == "bank_site"
    assert a["repec_handle"] == "RePEc:ecb:ecbwps:20263244"
    assert "https://www.ecb.europa.eu/pub/pdf/scpwps/ecb.wp3244~aa.en.pdf" in a["alt_urls"]
    # invariant #1: identity/content/file pointers untouched
    assert a["doc_id"] == "id3244" and a["sha256"] == "h1" and a["local_path"] == "/raw/3244.pdf"
    assert a["pdf_url"] == rows[0]["pdf_url"]
    # non-ECB row passes through byte-for-byte (no schema fields injected)
    assert after["idus"] == rows[2]

    # idempotent: a second --write finds them already migrated and rewrites nothing new
    summary2 = wp_migrate.run_wp_migrate(bank_codes=["ecb"], write=True, config=cfg)
    assert summary2["ecb"]["already_day"] == 2
    after2 = {r["doc_id"]: r for r in iter_manifest_rows(cfg)}
    assert after2 == after
