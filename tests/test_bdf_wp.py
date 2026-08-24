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


def test_iter_legacy_yields_number_docrecord_pairs_from_years(monkeypatch):
    import cb_corpus.sources.bdf_wp as bdf_mod
    monkeypatch.setattr(bdf_mod, "_LEGACY_FIRST_YEAR", 1994)
    monkeypatch.setattr(bdf_mod, "_LEGACY_LAST_YEAR", 1994)
    f = _FakeFetcher({"year=1994.html": _read("legacy_1994_sparse.html")})
    pairs = list(_iter_legacy(f, years={1994}))
    assert len(pairs) == 2
    assert all(isinstance(n, int) for n, _r in pairs)
    assert {n for n, _r in pairs} == {30, 3}
    recs = [r for _n, r in pairs]
    assert all(isinstance(r, DocRecord) for r in recs)
    assert all(r.bank_code == "fr" and r.doc_type == DocType.D1 for r in recs)
    assert all(r.provenance == "bank_site" and r.date_source == "bank_site" for r in recs)
    assert all(r.mime_type == "application/pdf" for r in recs)
    assert all(r.date_precision == "month" for r in recs)
    # The pairing is stable per row: n°30 keeps its own record, not n°3's.
    by_number = dict(pairs)
    assert by_number[30].pdf_url.endswith("document-de-travail_30_1994.pdf")
    # n°3's PDF is filed under a completely unrelated "debats-economiques_..."
    # filename (a mismatched re-upload) -- proof the link must always be
    # scraped, never derived from a document-de-travail_{num}_{year} pattern.
    assert by_number[3].pdf_url.endswith("debats-economiques_3_2006-10.pdf")
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
    pairs = list(_iter_legacy(f, years={1994, 1995, 1996}))
    assert len(pairs) == 2                              # only 1994's 2 papers
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
    pairs = list(_iter_legacy(f, years={1994}))
    ids = {r.doc_id for _n, r in pairs}
    assert len(ids) == len(pairs)                        # distinct pdf_url -> distinct doc_id


# ==== New-system walker (Task 2) =======================================
#
# Real fixtures captured 2026-08-24 (no live network in these tests):
# - new_page0.html: listing page 0 (12 cards, Aug 2026 -> June 2026).
# - new_page_mid.html: listing page 15 (12 cards, Sept 2022 -> June 2022).
# - new_page_last_partial.html: listing page 33, the ACTUAL last page (2
#   cards only, Jan 2018) -- its own pagination links top out at 33 too.
# - new_detail_wp660.html / new_detail_wp661.html: the two detail pages for
#   the papers listed on new_page_last_partial, og:description carries the
#   number for both.
# - new_detail_wp660_synthetic_ogdesc_no_number.html: a COPY of wp660's
#   detail page with the number stripped from the og:description meta only
#   (the body paragraph still has it) -- exercises the body fallback.
# - new_detail_wp660_synthetic_no_number_anywhere.html: a further COPY with
#   the number ALSO stripped from the body paragraph the parser reads
#   (other incidental "no. 660" mentions elsewhere on the page, e.g. the
#   download-button caption and a JSON settings blob, are left untouched --
#   the parser never looks there, so their presence doesn't leak the number
#   back in) -- exercises skip-with-warning when it's absent everywhere the
#   parser looks.

from cb_corpus.sources.bdf_wp import (
    _iter_new, _new_ordinal_date, _new_wp_number,
    parse_new_listing_page, parse_new_detail, BDF_NEW,
)


# ---- _new_ordinal_date -------------------------------------------------

def test_new_ordinal_date_various_forms():
    assert _new_ordinal_date("21st of August 2026") == date(2026, 8, 21)
    assert _new_ordinal_date("2nd of June 2022") == date(2022, 6, 2)
    assert _new_ordinal_date("23rd of September 2022") == date(2022, 9, 23)
    assert _new_ordinal_date("1st of January 2018") == date(2018, 1, 1)


def test_new_ordinal_date_unparseable_returns_none():
    assert _new_ordinal_date("") is None
    assert _new_ordinal_date("not a date") is None


# ---- parse_new_listing_page: real fixtures -----------------------------

def test_parse_new_listing_page0_twelve_cards_newest_first():
    html = _read("new_page0.html")
    rows = parse_new_listing_page(html, BDF_NEW)
    assert len(rows) == 12
    d0, t0, u0 = rows[0]
    assert d0 == date(2026, 8, 21)
    assert "Jus naturale" in t0
    assert u0 == (BDF_NEW + "/en/publications-and-statistics/publications/"
                  "jus-naturale-impact-nature-related-litigation-corporate-valuation")
    d_last, _t, _u = rows[-1]
    assert d_last == date(2026, 6, 25)
    # newest-first: dates are non-increasing across the page
    assert all(rows[i][0] >= rows[i + 1][0] for i in range(len(rows) - 1))


def test_parse_new_listing_page_mid():
    html = _read("new_page_mid.html")
    rows = parse_new_listing_page(html, BDF_NEW)
    assert len(rows) == 12
    assert rows[0][0] == date(2022, 9, 23)
    assert rows[-1][0] == date(2022, 6, 2)


def test_parse_new_listing_page_last_partial_fewer_than_twelve():
    html = _read("new_page_last_partial.html")
    rows = parse_new_listing_page(html, BDF_NEW)
    assert len(rows) == 2
    assert rows[0][0] == date(2018, 1, 24)
    assert rows[1][0] == date(2018, 1, 15)
    assert rows[0][2].endswith("global-financial-interconnectedness-non-linear-assessment-uncertainty-channel")
    assert rows[1][2].endswith("exchange-rate-movements-firm-level-exports-and-heterogeneity")


def test_parse_new_listing_page_empty_page_returns_empty_list():
    assert parse_new_listing_page("<html><body>no papers here</body></html>", BDF_NEW) == []


# ---- parse_new_listing_page: adversarial synthetic cards ---------------

_CARD_TMPL = """
<a class="card card-vertical rounded shadow-light h-100 w-100 text-decoration-none" href="{href}">
  <div class="card-body py-4 px-5 d-flex flex-column">
    <div class="d-flex flex-column">
      <h3 class="card-title pb-0 mb-lg-0 my-1">{title}</h3>
    </div>
    {date_html}
  </div>
</a>
"""


def _card(href="/en/publications-and-statistics/publications/some-paper",
          title="A Paper Title",
          date_html='<small class="d-flex mt-auto">21st of August 2026</small>'):
    return _CARD_TMPL.format(href=href, title=title, date_html=date_html)


def test_parse_new_listing_page_skips_card_with_no_date(capsys):
    html = _card(date_html="")
    assert parse_new_listing_page(html, BDF_NEW) == []
    err = capsys.readouterr().err
    assert "WARNING" in err and "malformed listing card" in err


def test_parse_new_listing_page_skips_card_with_no_title(capsys):
    html = _card(title="")
    assert parse_new_listing_page(html, BDF_NEW) == []
    err = capsys.readouterr().err
    assert "WARNING" in err


def test_parse_new_listing_page_skips_card_with_no_href(capsys):
    html = _card(href="")
    assert parse_new_listing_page(html, BDF_NEW) == []
    err = capsys.readouterr().err
    assert "WARNING" in err


def test_parse_new_listing_page_malformed_card_does_not_break_the_rest(capsys):
    good1 = _card(href="/en/.../paper-one", title="Paper One")
    bad = _card(date_html="")
    good2 = _card(href="/en/.../paper-two", title="Paper Two")
    html = f"<html><body>{good1}{bad}{good2}</body></html>"
    rows = parse_new_listing_page(html, BDF_NEW)
    assert {t for _d, t, _u in rows} == {"Paper One", "Paper Two"}
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1


# ---- parse_new_detail: real fixtures -----------------------------------

def test_parse_new_detail_number_from_og_description():
    number, _authors, _pdf = parse_new_detail(_read("new_detail_wp660.html"))
    assert number == 660
    number, _authors, _pdf = parse_new_detail(_read("new_detail_wp661.html"))
    assert number == 661


def test_parse_new_detail_authors():
    _n, authors, _pdf = parse_new_detail(_read("new_detail_wp660.html"))
    assert authors == ["Antoine Berthou", "Emmanuel Dhyne"]
    _n, authors, _pdf = parse_new_detail(_read("new_detail_wp661.html"))
    assert authors == ["Bertrand Candelon", "Laurent Ferrara", "Marc Joëts"]


def test_parse_new_detail_pdf_url_scraped_never_derived():
    _n, _a, pdf = parse_new_detail(_read("new_detail_wp660.html"))
    assert pdf == BDF_NEW + "/system/files/2023-05/wp660_0.pdf"
    # n661's PDF filename carries a completely different naming convention
    # ("document-de-travail-661_..." not "wp661...") -- proof it is always
    # the link scraped off the page, never derived from the number.
    _n, _a, pdf = parse_new_detail(_read("new_detail_wp661.html"))
    assert pdf == BDF_NEW + "/system/files/2023-05/document-de-travail-661_2018-01_0.pdf"


def test_parse_new_detail_body_fallback_when_og_description_lacks_number():
    html = _read("new_detail_wp660_synthetic_ogdesc_no_number.html")
    number, _authors, pdf = parse_new_detail(html)
    assert number == 660                     # recovered from the body paragraph
    assert pdf == BDF_NEW + "/system/files/2023-05/wp660_0.pdf"


def test_parse_new_detail_number_none_when_absent_everywhere():
    html = _read("new_detail_wp660_synthetic_no_number_anywhere.html")
    number, _authors, _pdf = parse_new_detail(html)
    assert number is None


def test_parse_new_detail_no_pdf_link_returns_none():
    html = "<html><head></head><body>no download here</body></html>"
    number, authors, pdf = parse_new_detail(html)
    assert pdf is None
    assert number is None
    assert authors == []


# ---- _new_wp_number: FR variant -----------------------------------------

def test_new_wp_number_fr_variant():
    assert _new_wp_number("Document de travail n° 1060. Some abstract text.") == 1060


def test_new_wp_number_en_variant():
    assert _new_wp_number("Working Paper Series no. 660. Some abstract text.") == 660


def test_new_wp_number_absent_returns_none():
    assert _new_wp_number("No number mentioned here at all.") is None


# ---- _iter_new: fixture-fed fetcher -------------------------------------

def test_iter_new_yields_number_docrecord_pairs(monkeypatch):
    """max_pages=1 pins the walk to a single listing fetch; feeding it the
    real last-partial fixture (2 cards, wp660 + wp661) exercises the full
    listing -> detail -> DocRecord path end to end."""
    f = _FakeFetcher({
        "working-papers?page=0": _read("new_page_last_partial.html"),
        "global-financial-interconnectedness-non-linear-assessment-uncertainty-channel":
            _read("new_detail_wp661.html"),
        "exchange-rate-movements-firm-level-exports-and-heterogeneity":
            _read("new_detail_wp660.html"),
    })
    pairs = list(_iter_new(f, since=None, max_pages=1))
    assert {n for n, _r in pairs} == {660, 661}
    by_number = dict(pairs)

    r660 = by_number[660]
    assert isinstance(r660, DocRecord)
    assert r660.bank_code == "fr" and r660.doc_type == DocType.D1
    assert r660.date == date(2018, 1, 15)
    assert r660.date_precision == "day" and r660.date_source == "bank_site"
    assert r660.provenance == "bank_site" and r660.mime_type == "application/pdf"
    assert r660.pdf_url == BDF_NEW + "/system/files/2023-05/wp660_0.pdf"
    assert r660.source_url.endswith("exchange-rate-movements-firm-level-exports-and-heterogeneity")

    r661 = by_number[661]
    assert r661.date == date(2018, 1, 24)
    assert r661.pdf_url == BDF_NEW + "/system/files/2023-05/document-de-travail-661_2018-01_0.pdf"

    # only the single listing page was fetched, plus the two detail pages
    assert len(f.calls) == 3


def test_iter_new_max_pages_caps_the_walk_even_with_more_pages_listed():
    """new_page0.html's own pagination links go up to page 33; max_pages=2
    must stop the walk at pages 0-1 regardless."""
    f = _FakeFetcher({
        "working-papers?page=0": _read("new_page0.html"),
        "working-papers?page=1": "<html><body>no papers</body></html>",
    })
    list(_iter_new(f, since=None, max_pages=2))
    listing_calls = [c for c in f.calls if "?page=" in c]
    assert sorted(listing_calls) == sorted([
        BDF_NEW + "/en/publications-and-research/our-main-publications/working-papers?page=0",
        BDF_NEW + "/en/publications-and-research/our-main-publications/working-papers?page=1",
    ])


def test_iter_new_since_stops_before_any_card_older_than_cutoff(monkeypatch):
    """A `since` newer than every card on page 0 means zero fresh rows on
    that page -> the newest-first walk stops immediately, with no detail
    fetches and no page 1 fetch at all."""
    f = _FakeFetcher({"working-papers?page=0": _read("new_page0.html")})
    pairs = list(_iter_new(f, since=date(2026, 9, 1)))
    assert pairs == []
    assert f.calls == [BDF_NEW + "/en/publications-and-research/"
                        "our-main-publications/working-papers?page=0"]


def test_iter_new_since_stops_after_a_whole_stale_page():
    """Page 0 has a mix of fresh/stale cards (some detail fetches attempted,
    even if their targets aren't fixture-mapped here); page 1 is entirely
    stale relative to `since` -> the walk fetches page 1 (to check) but never
    page 2."""
    f = _FakeFetcher({
        "working-papers?page=0": _read("new_page0.html"),        # Aug26 -> Jun26
        "working-papers?page=1": _read("new_page_mid.html"),     # Sep22 -> Jun22
        "working-papers?page=2": _read("new_page_last_partial.html"),
    })
    list(_iter_new(f, since=date(2026, 7, 1)))
    listing_calls = [c for c in f.calls if "?page=" in c]
    assert (BDF_NEW + "/en/publications-and-research/our-main-publications/"
            "working-papers?page=0") in listing_calls
    assert (BDF_NEW + "/en/publications-and-research/our-main-publications/"
            "working-papers?page=1") in listing_calls
    assert (BDF_NEW + "/en/publications-and-research/our-main-publications/"
            "working-papers?page=2") not in listing_calls


def test_iter_new_detail_fetch_failure_is_skipped_not_raised():
    """A detail page that 404s is skipped gracefully, same as a listing
    page that fails."""
    f = _FakeFetcher({
        "working-papers?page=0": _read("new_page_last_partial.html"),
        # only wp660's detail page is mapped; wp661's 404s
        "exchange-rate-movements-firm-level-exports-and-heterogeneity":
            _read("new_detail_wp660.html"),
    })
    pairs = list(_iter_new(f, since=None, max_pages=1))
    assert {n for n, _r in pairs} == {660}


def test_iter_new_no_listing_page_zero_returns_gracefully():
    class _AlwaysFails:
        def get_text(self, url):
            raise RuntimeError("boom")
    assert list(_iter_new(_AlwaysFails())) == []
