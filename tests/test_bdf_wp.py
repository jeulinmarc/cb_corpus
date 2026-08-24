"""Banque de France (fr D1) legacy working-paper walker — Task 1.

Pure-helper + saved-fixture style (mirrors tests/test_wp_v3.py). The legacy
fixtures under tests/fixtures/bdf/ are real pages captured 2026-08-24:
- legacy_2019_variants.html: a rich year (40 papers) exercising the wild
  filename-prefix variety (document-de-travail_/doc_de_travail_/
  working-paper-/working_paper_/wp-/wp_/wpNNN[_0|_1] re-upload/revision
  suffixes) AND proving real day-of-month precision for this era.
- legacy_2010.html: a normal older year where every sampled day is "01"
  (CMS placeholder) -> month precision.
- legacy_1994_sparse.html: a near-empty year (2 papers only).
No live network in these tests.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

from cb_corpus.models import DocRecord
from cb_corpus.taxonomy import DocType
from cb_corpus.sources.bdf_wp import parse_legacy_year, _iter_legacy, BDF_LEGACY

FIX = Path(__file__).parent / "fixtures" / "bdf"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


# ---- parse_legacy_year: real fixtures --------------------------------

def test_parse_legacy_year_prefix_variants_and_day_precision():
    """2019: 40 papers survive wildly inconsistent PDF filename prefixes, and
    since real (non-01) days appear in the sample, the WHOLE page is trusted
    at day precision (not forced to month)."""
    html = _read("legacy_2019_variants.html")
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert len(rows) == 40
    numbers = {n for _d, _p, n, _t, _u in rows}
    assert len(numbers) == 40                       # every row got a distinct number

    # n°745, the newest paper, is filename `wp745.pdf` — a plain scraped link.
    d745 = next(r for r in rows if r[2] == 745)
    d, prec, number, title, pdf_url = d745
    assert d == date(2019, 12, 27) and prec == "day"
    assert pdf_url == BDF_LEGACY + "/sites/default/files/medias/documents/wp745.pdf"
    assert title

    # n°738 carries a revised-version filename suffix (`wp738_1.pdf`) — the
    # link is scraped as-is, never derived/rewritten.
    d738 = next(r for r in rows if r[2] == 738)
    assert d738[4].endswith("/wp738_1.pdf")
    assert d738[0] == date(2019, 11, 8) and d738[1] == "day"      # real day, not 01


def test_parse_legacy_year_month_precision_day01_normalization():
    """2010: every sampled row's day is 1 (CMS placeholder) -> the page is
    month precision and dates are normalized to day 1."""
    html = _read("legacy_2010.html")
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert len(rows) == 41
    assert all(prec == "month" for _d, prec, *_ in rows)
    assert all(d.day == 1 for d, *_ in rows)
    d311 = next(r for r in rows if r[2] == 311)
    assert d311[0] == date(2010, 12, 1)
    assert d311[4].endswith("/document-de-travail_311_2010.pdf")


def test_parse_legacy_year_sparse():
    """1994: a near-empty year (2 papers) parses cleanly, month precision."""
    html = _read("legacy_1994_sparse.html")
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert len(rows) == 2
    assert {n for _d, _p, n, _t, _u in rows} == {30, 3}
    assert all(prec == "month" for _d, prec, *_ in rows)


def test_parse_legacy_year_empty_page_returns_empty_list():
    assert parse_legacy_year("<html><body>no papers here</body></html>", BDF_LEGACY) == []


# ---- parse_legacy_year: adversarial synthetic rows --------------------

_ROW_TMPL = """
<div class="node node-publication teaser-collapsible">
  <div class="text-wrapper">
    <div class="overtitle no-tt">{cat}</div>
    <div class="element2">{title}</div>
    <div class="bot-pdf clearfix"><ul>
      <li>{date_span}</li>
    </ul></div>
    <div class="bt-consulter"><div class="liste-dl"><div class="item-list"><ul>
      <li class="last">{pdf_link}</li>
    </ul></div></div></div>
  </div>
</div>
"""


def _row(cat='<span class="category">Documents de travail n°42 :</span>',
         title="A Paper",
         date_span=('<span class="date-display-single" property="dc:date" '
                    'datatype="xsd:dateTime" content="2015-05-20T00:00:00+02:00">'
                    'Publié le 20/05/2015</span>'),
         pdf_link='<a href="/sites/default/files/medias/documents/wp42.pdf">DL</a>'):
    return _ROW_TMPL.format(cat=cat, title=title, date_span=date_span, pdf_link=pdf_link)


def test_parse_legacy_year_skips_row_with_no_number(capsys):
    html = _row(cat='<span class="category">Documents de travail :</span>')  # no n°
    assert parse_legacy_year(html, BDF_LEGACY) == []
    err = capsys.readouterr().err
    assert "WARNING" in err and "no paper number" in err


def test_parse_legacy_year_skips_row_with_no_pdf_link(capsys):
    html = _row(pdf_link="<span>no link here</span>")
    assert parse_legacy_year(html, BDF_LEGACY) == []
    err = capsys.readouterr().err
    assert "WARNING" in err and "no PDF link" in err


def test_parse_legacy_year_skips_row_with_no_parseable_date(capsys):
    html = _row(date_span='<span class="date-display-single">no date here</span>')
    assert parse_legacy_year(html, BDF_LEGACY) == []
    err = capsys.readouterr().err
    assert "WARNING" in err and "no parseable date" in err


def test_parse_legacy_year_malformed_row_does_not_break_the_rest(capsys):
    """One malformed row among good ones: skipped with a warning, the good
    rows still come out."""
    good1 = _row(cat='<span class="category">Documents de travail n°1 :</span>',
                pdf_link='<a href="/sites/default/files/medias/documents/wp1.pdf">DL</a>')
    bad = _row(cat='<span class="category">Documents de travail :</span>')
    good2 = _row(cat='<span class="category">Documents de travail n°2 :</span>',
                pdf_link='<a href="/sites/default/files/medias/documents/wp2.pdf">DL</a>')
    html = f"<html><body>{good1}{bad}{good2}</body></html>"
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert {n for _d, _p, n, _t, _u in rows} == {1, 2}
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1


def test_parse_legacy_year_falls_back_to_dmy_text_when_content_attr_missing():
    html = _row(date_span='<span class="date-display-single">Publié le 05/03/2016</span>')
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert rows[0][0] == date(2016, 3, 5)


def test_parse_legacy_year_falls_back_title_when_element2_missing():
    html = _ROW_TMPL.format(
        cat='<span class="category">Documents de travail n°99 :</span>',
        title="",
        date_span=('<span class="date-display-single" content="2015-01-01T00:00:00+02:00">'
                   'Publié le 01/01/2015</span>'),
        pdf_link='<a href="/x/wp99.pdf">DL</a>')
    rows = parse_legacy_year(html, BDF_LEGACY)
    assert rows[0][3] == "Documents de travail n°99"


# ---- _iter_legacy: fixture-fed fetcher --------------------------------

class _FakeFetcher:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def get_text(self, url):
        self.calls.append(url)
        for suffix, content in self.mapping.items():
            if url.endswith(suffix):
                return content
        raise RuntimeError(f"404: {url}")


def test_iter_legacy_yields_docrecords_from_years(monkeypatch):
    import cb_corpus.sources.bdf_wp as bdf_mod
    monkeypatch.setattr(bdf_mod, "_LEGACY_FIRST_YEAR", 1994)
    monkeypatch.setattr(bdf_mod, "_LEGACY_LAST_YEAR", 1994)
    f = _FakeFetcher({"year=1994.html": _read("legacy_1994_sparse.html")})
    recs = list(_iter_legacy(f, years={1994}))
    assert len(recs) == 2
    assert all(isinstance(r, DocRecord) for r in recs)
    assert all(r.bank_code == "fr" and r.doc_type == DocType.D1 for r in recs)
    assert all(r.provenance == "bank_site" and r.date_source == "bank_site" for r in recs)
    assert all(r.mime_type == "application/pdf" for r in recs)
    assert all(r.date_precision == "month" for r in recs)
    # n°3's PDF is filed under a completely unrelated "debats-economiques_..."
    # filename (a mismatched re-upload) -- proof the link must always be
    # scraped, never derived from a document-de-travail_{num}_{year} pattern.
    assert {r.pdf_url.rsplit("/", 1)[-1] for r in recs} == {
        "document-de-travail_30_1994.pdf", "debats-economiques_3_2006-10.pdf",
    }


def test_iter_legacy_years_filter_restricts_and_skips_missing_year(monkeypatch):
    """A `years` filter walks only the requested years; a year whose fetch
    fails (e.g. the confirmed 1995 gap) is skipped gracefully, not raised."""
    import cb_corpus.sources.bdf_wp as bdf_mod
    monkeypatch.setattr(bdf_mod, "_LEGACY_FIRST_YEAR", 1994)
    monkeypatch.setattr(bdf_mod, "_LEGACY_LAST_YEAR", 1996)
    f = _FakeFetcher({
        "year=1994.html": _read("legacy_1994_sparse.html"),
        # 1995 deliberately absent from the mapping -> _FakeFetcher raises,
        # same as a live 404.
        "year=1996.html": "<html><body>no papers</body></html>",  # empty page
    })
    recs = list(_iter_legacy(f, years={1994, 1995, 1996}))
    assert len(recs) == 2                              # only 1994's 2 papers
    assert sorted(f.calls) == sorted([
        "https://publications.banque-france.fr/liste-chronologique/documents-de-travail_year=1994.html",
        "https://publications.banque-france.fr/liste-chronologique/documents-de-travail_year=1995.html",
        "https://publications.banque-france.fr/liste-chronologique/documents-de-travail_year=1996.html",
    ])


def test_iter_legacy_years_none_walks_full_range_newest_first(monkeypatch):
    import cb_corpus.sources.bdf_wp as bdf_mod
    monkeypatch.setattr(bdf_mod, "_LEGACY_FIRST_YEAR", 1994)
    monkeypatch.setattr(bdf_mod, "_LEGACY_LAST_YEAR", 1995)
    f = _FakeFetcher({"year=1994.html": _read("legacy_1994_sparse.html")})
    list(_iter_legacy(f, years=None))
    assert f.calls[0].endswith("year=1995.html")        # newest year probed first
    assert f.calls[1].endswith("year=1994.html")


def test_iter_legacy_docrecord_doc_id_is_stable_and_keyed_on_pdf_url():
    import cb_corpus.sources.bdf_wp as bdf_mod
    f = _FakeFetcher({"year=1994.html": _read("legacy_1994_sparse.html")})
    recs = list(_iter_legacy(f, years={1994}))
    ids = {r.doc_id for r in recs}
    assert len(ids) == len(recs)                        # distinct pdf_url -> distinct doc_id
