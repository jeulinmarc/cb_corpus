import json
from datetime import date
from pathlib import Path

import pytest

from cb_corpus.taxonomy import DocType, FULL_SCOPE
from cb_corpus.banks import BIS_63, get_bank, bank_for_bis_institution
from cb_corpus.adapters import ADAPTERS, INSTANCE_FACTORIES, get_adapter
from cb_corpus.adapters.generic_sitemap import (
    GenericSitemapAdapter, parse_sitemap, _date_from_url,
)
from cb_corpus.adapters.listing_crawler import (
    ListingCrawlerAdapter, parse_links,
)
from cb_corpus.adapters.fed import FedAdapter, parse_minutes_links
from cb_corpus.adapters.ecb import (
    ECBAdapter, parse_index, parse_year_includes, parse_account_items,
    parse_bulletin_pdfs,
)
from cb_corpus.sources.bis_speeches import (
    parse_listing, parse_sitemap_index, parse_year_sitemap, parse_detail,
    _parse_slug_date, _guess_institution,
)
from cb_corpus.sources.repec import (
    parse_series_page, extract_official_pdf, extract_pdf,
)
from cb_corpus.models import DocRecord
from cb_corpus.config import Config
from cb_corpus.storage import Storage
from cb_corpus.completeness import build_matrix, summarize


# ---- taxonomy --------------------------------------------------------
def test_full_scope_is_a_through_f_only():
    groups = {dt.group for dt in FULL_SCOPE}
    assert groups == {"A", "B", "C", "D", "E", "F"}
    assert all(dt.group != "G" for dt in FULL_SCOPE)
    assert DocType.A3 in FULL_SCOPE and DocType.G1 not in FULL_SCOPE


# ---- banks -----------------------------------------------------------
def test_exactly_63_banks_unique_codes_and_domains():
    assert len(BIS_63) == 63
    assert len({b.code for b in BIS_63}) == 63
    assert all(b.homepage and "." in b.homepage for b in BIS_63)


def test_bis_institution_mapping():
    assert bank_for_bis_institution("Bank of England").code == "gb"
    assert bank_for_bis_institution("not a bank") is None


# ---- adapter registry ------------------------------------------------
def test_all_63_have_an_adapter():
    for b in BIS_63:
        assert b.code in ADAPTERS
    assert isinstance(get_adapter("us"), FedAdapter)
    assert isinstance(get_adapter("ecb"), ECBAdapter)


def test_generic_adapter_supports_speeches_and_papers():
    a = get_adapter("se")  # no bespoke adapter -> generic
    sup = a.supported_types()
    assert DocType.C1 in sup and DocType.D1 in sup


def test_fed_expected_counts():
    a = get_adapter("us")
    assert a.expected_count(DocType.A3, 2024) == 8
    assert a.expected_count(DocType.F1, 2024) == 4


# ---- BIS speech parser ----------------------------------------------
BIS_FIXTURE = """
<table class="documentList">
  <tr>
    <td class="item_date">15 Mar 2024</td>
    <td class="title">
      <a href="/review/r240315a.htm">Inflation outlook</a>
      <div>Andrew Bailey, Governor of the Bank of England, speech</div>
      <a href="/review/r240315a.pdf">PDF</a>
    </td>
  </tr>
</table>
"""


def test_parse_bis_listing():
    items = parse_listing(BIS_FIXTURE)
    assert len(items) == 1
    it = items[0]
    assert it.date == date(2024, 3, 15)
    assert it.title == "Inflation outlook"
    assert it.institution == "Bank of England"
    assert it.pdf_url.endswith("/review/r240315a.pdf")


# ---- BIS sitemap-based discovery (v2) -------------------------------
BIS_SITEMAP_INDEX = """<?xml version="1.0" encoding="utf-8"?>
<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <sitemap><loc>https://www.bis.org/sitemap_documents_2024.xml</loc></sitemap>
  <sitemap><loc>https://www.bis.org/sitemap_documents_2025.xml</loc></sitemap>
  <sitemap><loc>https://www.bis.org/sitemap_other.xml</loc></sitemap>
</sitemapindex>
"""

BIS_YEAR_SITEMAP = """<?xml version="1.0" encoding="utf-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
  <url><loc>https://www.bis.org/review/r240315a.pdf</loc></url>
  <url><loc>https://www.bis.org/review/r240315a.htm</loc></url>
  <url><loc>https://www.bis.org/review/r240316b.pdf</loc></url>
  <url><loc>https://www.bis.org/publ/work123.pdf</loc></url>
  <url><loc>https://www.bis.org/about/index.htm</loc></url>
</urlset>
"""

BIS_DETAIL = """<!doctype html><html><head>
  <meta property="og:title" content="Andrew Bailey: Inflation outlook"/>
  <meta property="og:description" content="Speech by Mr Andrew Bailey, Governor of the Bank of England, at a dinner, London, 15 March 2024."/>
</head><body></body></html>
"""


def test_parse_sitemap_index_filters_yearly_files():
    pairs = parse_sitemap_index(BIS_SITEMAP_INDEX)
    assert pairs == [(2024, "https://www.bis.org/sitemap_documents_2024.xml"),
                     (2025, "https://www.bis.org/sitemap_documents_2025.xml")]


def test_parse_year_sitemap_keeps_only_review_pdfs():
    metas = parse_year_sitemap(BIS_YEAR_SITEMAP)
    assert {m.pdf_url for m in metas} == {
        "https://www.bis.org/review/r240315a.pdf",
        "https://www.bis.org/review/r240316b.pdf",
    }
    first = next(m for m in metas if "r240315a" in m.pdf_url)
    assert first.date == date(2024, 3, 15)
    assert first.detail_url.endswith("r240315a.htm")


def test_parse_slug_date_century_boundary():
    assert _parse_slug_date("960115") == date(1996, 1, 15)
    assert _parse_slug_date("240315") == date(2024, 3, 15)
    assert _parse_slug_date("999999") is None  # invalid month


def test_parse_detail_extracts_title_and_description():
    title, desc = parse_detail(BIS_DETAIL)
    assert title.startswith("Andrew Bailey")
    assert "Bank of England" in desc


def test_guess_institution_picks_longest_match():
    inst = _guess_institution(
        "Speech by Andrew Bailey, Governor of the Bank of England, in London.")
    assert inst == "Bank of England"
    assert _guess_institution("No central bank mentioned here.") == ""


def test_guess_institution_ignores_host_institution_after_at():
    # The speaker is from Bundesbank; the host is National Bank of Romania.
    # The naive longest-match would wrongly pick the longer Romanian label.
    desc = ("Keynote speech by Prof Claudia Buch, Vice-President of the "
            "Deutsche Bundesbank, at the 14th Seminar on Financial Stability "
            "Issues, organised by the National Bank of Romania and the IMF.")
    assert _guess_institution(desc) == "Deutsche Bundesbank"


# ---- RePEc discovery -------------------------------------------------
REPEC_SERIES = """
<html><body>
<a href="/p/boe/boeewp/0001.html">Paper one</a>
<a href="/p/boe/boeewp/0002.html">Paper two</a>
<a href="/cgi-bin/htsearch">search</a>
</body></html>
"""

REPEC_PAPER = """
<html><body><h1>A study of monetary policy</h1>
<a href="https://www.bankofengland.co.uk/-/media/boe/files/wp/0001.pdf">Official PDF</a>
<a href="https://ideas.repec.org/cached/0001.pdf">RePEc cached copy</a>
</body></html>
"""


def test_repec_series_parse_filters_to_paper_pages():
    urls = parse_series_page(REPEC_SERIES)
    assert any("boeewp/0001.html" in u for u in urls)
    assert all("htsearch" not in u for u in urls)


def test_repec_keeps_only_official_domain_pdf():
    pdf = extract_official_pdf(REPEC_PAPER, "bankofengland.co.uk", allow_bis=False)
    assert pdf.startswith("https://www.bankofengland.co.uk")
    # If the bank domain doesn't match, the IDEAS-cached copy must be rejected.
    assert extract_official_pdf(REPEC_PAPER, "example.org", allow_bis=False) is None


def test_repec_extract_pdf_prefers_bank_then_falls_back():
    # Bank homepage matches -> prefer that.
    pdf = extract_pdf(REPEC_PAPER, "bankofengland.co.uk")
    assert pdf.startswith("https://www.bankofengland.co.uk")
    # No bank match and no BIS host -> fall back to the first available PDF.
    only_cached = """<html><body>
    <a href="https://ideas.repec.org/cached/0001.pdf">Cached</a>
    </body></html>"""
    pdf = extract_pdf(only_cached, "example.org")
    assert pdf.startswith("https://ideas.repec.org")
    # No PDF at all -> None.
    assert extract_pdf("<html></html>", "example.org") is None


# ---- Fed / ECB native parsers ---------------------------------------
def test_fed_minutes_parser():
    html = '<a href="/monetarypolicy/files/fomcminutes20240131.pdf">Minutes</a>'
    got = parse_minutes_links(html)
    assert got and got[0][0] == date(2024, 1, 31)
    assert got[0][1].endswith("fomcminutes20240131.pdf")


def test_ecb_index_parser():
    html = '<a href="/press/accounts/2024/html/ecb.mg240404.en.pdf">Account</a>'
    rows = parse_index(html, href_must_contain="accounts")
    assert rows and rows[0][2].endswith(".pdf")


# ---- declarative adapters from TOML ---------------------------------
def test_toml_factories_loaded_for_majors():
    # banks_sources.toml ships with at least these.
    for code in ("ch", "au", "ca", "fr", "jp"):
        assert code in INSTANCE_FACTORIES
    # Hand-written ADAPTERS still override TOML for us/ecb.
    from cb_corpus.adapters.fed import FedAdapter
    assert isinstance(get_adapter("us"), FedAdapter)


def test_sitemap_parser_handles_urlset_and_index():
    urlset = """<?xml version="1.0"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url><loc>https://x.test/a.pdf</loc><lastmod>2024-03-15</lastmod></url>
      <url><loc>https://x.test/b.html</loc></url>
    </urlset>"""
    urls, children = parse_sitemap(urlset)
    assert len(urls) == 2 and not children
    assert urls[0][1] == date(2024, 3, 15)

    index = """<?xml version="1.0"?>
    <sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://x.test/sm1.xml</loc></sitemap>
    </sitemapindex>"""
    urls, children = parse_sitemap(index)
    assert children == ["https://x.test/sm1.xml"] and not urls


def test_date_from_url_picks_first_iso_like_date():
    assert _date_from_url("https://x/2024/03/15/foo.pdf") == date(2024, 3, 15)
    assert _date_from_url("https://x/foo-20240315.pdf") == date(2024, 3, 15)
    assert _date_from_url("https://x/no-date.pdf") is None


def test_listing_parse_links_extracts_pdfs_with_dates():
    html = """<html><body>
    <a href="/minutes/2024/g240115.pdf">January</a>
    <a href="/minutes/2024/g240228.pdf">February</a>
    <a href="/other.html">skip</a>
    </body></html>"""
    import re as _re
    rows = parse_links(html, "https://x.test/jp/",
                       _re.compile(r"g\d{6}\.pdf$"))
    assert len(rows) == 2
    assert rows[0][2].startswith("https://x.test/")


# ---- ECB v2 parsers --------------------------------------------------
def test_ecb_year_includes():
    html = """<html><body>
    <dl id="lazyload-container" data-snippets="../2026/html/index_include.en.html,../2025/html/index_include.en.html"></dl>
    </body></html>"""
    inc = parse_year_includes(html)
    assert inc == ["../2026/html/index_include.en.html",
                   "../2025/html/index_include.en.html"]


def test_ecb_account_items():
    html = """<html><body>
    <a href="/press/accounts/2026/html/ecb.mg260528~a93230dc4b.en.html">Acc 1</a>
    <a href="/press/accounts/2026/html/ecb.mg260416~6a27b0c258.en.html">Acc 2</a>
    <a href="/press/pr/date/2026/html/ecb.mp260430~xx.en.html">unrelated</a>
    </body></html>"""
    items = parse_account_items(html)
    assert len(items) == 2
    assert items[0][0] == date(2026, 5, 28)
    assert items[1][0] == date(2026, 4, 16)


def test_ecb_bulletin_pdfs_keep_english_only():
    html = """<html><body>
    <a href="/pub/pdf/ecbu/eb202603.en.pdf">EN</a>
    <a href="/pub/pdf/ecbu/eb202603.fr.pdf">FR (skipped)</a>
    <a href="/pub/pdf/ecbu/eb202602.en.pdf">EN</a>
    </body></html>"""
    rows = parse_bulletin_pdfs(html)
    assert len(rows) == 2
    assert all(u.endswith(".en.pdf") for _, _, u in rows)


# ---- storage (no domain guard in v2 — discovery layer owns URL quality) ----
def test_storage_indexes_any_url_in_dry_run(tmp_path):
    cfg = Config(data_dir=tmp_path)
    st = Storage(cfg)
    rec = DocRecord(bank_code="gb", doc_type=DocType.C1, title="x",
                    pdf_url="https://www.bankofengland.co.uk/a.pdf")
    assert st.save(rec, dry_run=True) == "dry-run:indexed"
    # second time should be deduped on doc_id
    assert st.save(rec, dry_run=True) == "skip:already-indexed"


def test_storage_tracks_known_urls_for_rerun_dedup(tmp_path):
    """Re-runs must short-circuit before the BIS detail-page fetch."""
    cfg = Config(data_dir=tmp_path)
    st1 = Storage(cfg)
    rec = DocRecord(bank_code="de", doc_type=DocType.C1, title="x",
                    pdf_url="https://www.bis.org/review/r240315a.pdf")
    st1.save(rec, dry_run=True)
    # Reopen — _urls is re-populated from the manifest.
    st2 = Storage(cfg)
    assert st2.is_known_url("https://www.bis.org/review/r240315a.pdf")
    assert not st2.is_known_url("https://www.bis.org/review/r240316b.pdf")


def test_storage_html_to_pdf_invokes_renderer(tmp_path, monkeypatch):
    """When fetched response is HTML and html_to_pdf=True, Storage renders to PDF."""
    from cb_corpus import storage as storage_mod

    cfg = Config(data_dir=tmp_path, html_to_pdf=True)
    st = Storage(cfg)
    # Stub fetcher to return HTML bytes.
    st.fetcher.get_bytes = lambda url: (b"<html><body>hi</body></html>", "text/html")
    # Stub Chrome renderer to just write a fake PDF.
    rendered: list = []
    def fake_render(url, output, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4 stub")
        rendered.append((url, output))
    monkeypatch.setattr(storage_mod, "render_url_to_pdf", fake_render)
    rec = DocRecord(bank_code="ecb", doc_type=DocType.A3, title="x",
                    pdf_url="https://www.ecb.europa.eu/x.en.html",
                    date=date(2024, 1, 1))
    assert st.save(rec) == "saved"
    assert rec.mime_type == "application/pdf"
    assert rec.local_path.endswith(".pdf")
    assert Path(rec.local_path).read_bytes().startswith(b"%PDF")
    assert rendered and rendered[0][0] == rec.pdf_url


def test_storage_html_to_pdf_falls_back_when_chrome_fails(tmp_path, monkeypatch):
    """If Chrome rendering raises, Storage falls back to saving the raw HTML."""
    from cb_corpus import storage as storage_mod
    cfg = Config(data_dir=tmp_path, html_to_pdf=True)
    st = Storage(cfg)
    st.fetcher.get_bytes = lambda url: (b"<html>raw</html>", "text/html")
    def boom(url, output, **kw):
        raise RuntimeError("chrome down")
    monkeypatch.setattr(storage_mod, "render_url_to_pdf", boom)
    rec = DocRecord(bank_code="ecb", doc_type=DocType.A3, title="x",
                    pdf_url="https://e.test/x.html", date=date(2024, 1, 1))
    assert st.save(rec) == "saved"
    assert rec.mime_type == "text/html"
    assert rec.local_path.endswith(".html")


def test_storage_html_to_pdf_disabled_keeps_html(tmp_path):
    """html_to_pdf=False -> HTML stays HTML on disk."""
    cfg = Config(data_dir=tmp_path, html_to_pdf=False)
    st = Storage(cfg)
    st.fetcher.get_bytes = lambda url: (b"<html>raw</html>", "text/html")
    rec = DocRecord(bank_code="ecb", doc_type=DocType.A3, title="x",
                    pdf_url="https://e.test/x.html", date=date(2024, 1, 1))
    assert st.save(rec) == "saved"
    assert rec.mime_type == "text/html"
    assert rec.local_path.endswith(".html")


def test_retry_html_recovers_and_records_failures(tmp_path, monkeypatch):
    """retry_failed retries leftover HTML rows; recovered rows update manifest,
    irrecoverable URLs go to data/failed_urls.txt."""
    import json as _json
    from cb_corpus import retry_html as retry_mod

    cfg = Config(data_dir=tmp_path)
    # Seed manifest: one row still text/html (will recover on first try),
    # one row still text/html (all strategies will fail), one already-pdf row.
    paths = []
    for code, mime, suffix in [("a", "text/html", ".html"),
                               ("b", "text/html", ".html"),
                               ("c", "application/pdf", ".pdf")]:
        p = tmp_path / "raw" / "ecb" / "A3" / "2024" / f"{code}{suffix}"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"<seed>")
        paths.append(p)
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.manifest_path.open("w") as fh:
        for p, mime, code in zip(paths, ["text/html", "text/html", "application/pdf"], ["a", "b", "c"]):
            fh.write(_json.dumps({
                "bank_code": "ecb", "doc_type": "A3", "title": "x",
                "pdf_url": f"https://e.test/{code}.en.html",
                "date": "2024-01-01", "year": 2024, "doc_id": code,
                "mime_type": mime, "local_path": str(p),
                "sha256": None, "language": "en",
            }) + "\n")

    monkeypatch.setattr(retry_mod, "find_chrome", lambda: "/fake/chrome")

    # First URL recovers on attempt; second URL fails every attempt.
    def fake_try(chrome, url, output, *, timeout, virtual_time_budget_ms, user_data_dir):
        if "a.en.html" in url:
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(b"%PDF-1.4 recovered")
            return True
        return False
    monkeypatch.setattr(retry_mod, "_try_strategy", fake_try)

    counts = retry_mod.retry_failed(cfg)
    assert counts.get("recovered") == 1
    assert counts.get("still-fail") == 1
    assert counts.get("skip") == 1

    # Manifest updated correctly.
    rows = [_json.loads(l) for l in cfg.manifest_path.read_text().splitlines()]
    a_row = next(r for r in rows if r["doc_id"] == "a")
    b_row = next(r for r in rows if r["doc_id"] == "b")
    c_row = next(r for r in rows if r["doc_id"] == "c")
    assert a_row["mime_type"] == "application/pdf"
    assert a_row["local_path"].endswith(".pdf")
    assert b_row["mime_type"] == "text/html"  # still failed
    assert c_row["mime_type"] == "application/pdf"  # untouched

    # Failed URLs file written.
    failed = (cfg.data_dir / "failed_urls.txt").read_text()
    assert "b.en.html" in failed
    assert "a.en.html" not in failed


def test_convert_existing_rewrites_manifest(tmp_path, monkeypatch):
    """convert_existing turns HTML rows into PDF rows and rewrites the manifest."""
    import json as _json
    from cb_corpus import convert as convert_mod
    cfg = Config(data_dir=tmp_path)
    # Seed an HTML file on disk + matching manifest entry.
    html_path = tmp_path / "raw" / "ecb" / "A3" / "2024" / "abc123.html"
    html_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.write_bytes(b"<html>seed</html>")
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.manifest_path.open("w") as fh:
        fh.write(_json.dumps({
            "bank_code": "ecb", "doc_type": "A3", "title": "x",
            "pdf_url": "https://e.test/abc.en.html",
            "date": "2024-01-01", "year": 2024, "doc_id": "abc123",
            "mime_type": "text/html", "local_path": str(html_path),
            "sha256": None, "language": "en",
        }) + "\n")

    monkeypatch.setattr(convert_mod, "find_chrome", lambda: "/fake/chrome")
    def fake_render(url, output, **kw):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"%PDF-1.4 ok")
    monkeypatch.setattr(convert_mod, "render_url_to_pdf", fake_render)

    counts = convert_mod.convert_existing(cfg)
    assert counts == {"converted": 1}
    pdf_path = html_path.with_suffix(".pdf")
    # New policy: keep BOTH the HTML and the rendered PDF.
    assert pdf_path.exists() and html_path.exists()
    row = _json.loads(cfg.manifest_path.read_text().splitlines()[0])
    assert row["mime_type"] == "application/pdf"
    assert row["local_path"].endswith(".pdf")
    assert row["html_path"].endswith(".html")


def test_storage_target_path_extension(tmp_path):
    from datetime import date as _d
    cfg = Config(data_dir=tmp_path)
    st = Storage(cfg)
    pdf_rec = DocRecord(bank_code="us", doc_type=DocType.A3, title="m",
                        pdf_url="https://x/y.pdf", date=_d(2024, 1, 1),
                        mime_type="application/pdf")
    assert st.target_path(pdf_rec).suffix == ".pdf"
    html_rec = DocRecord(bank_code="ecb", doc_type=DocType.A3, title="m",
                         pdf_url="https://x/y.en.html", date=_d(2024, 1, 1),
                         mime_type="text/html")
    assert st.target_path(html_rec).suffix == ".html"


# ---- completeness matrix --------------------------------------------
def test_completeness_matrix_statuses(tmp_path):
    cfg = Config(data_dir=tmp_path)
    # one downloaded FOMC minute in 2024 (expected 8 -> partial)
    rec = DocRecord(bank_code="us", doc_type=DocType.A3, title="m",
                    pdf_url="https://www.federalreserve.gov/x.pdf",
                    date=date(2024, 5, 1))
    cfg.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    with cfg.manifest_path.open("w") as fh:
        fh.write(json.dumps(rec.to_row()) + "\n")
    st = Storage(cfg)
    rows = build_matrix([2024], bank_codes=["us"], storage=st)
    cell = next(r for r in rows if r["doc_type"] == "A3" and r["year"] == 2024)
    assert cell["expected"] == 8 and cell["downloaded"] == 1
    assert cell["status"] == "partial"
    # a type with no downloads and known expectation -> missing
    f1 = next(r for r in rows if r["doc_type"] == "F1" and r["year"] == 2024)
    assert f1["status"] == "missing"
    assert "partial" in summarize(rows)
