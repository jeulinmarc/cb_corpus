"""Bank of England Staff Working Papers (gb D1) — `paper_meta` + `discover_boe_wp`.

Saved-fixture style (mirrors tests/test_bdf_wp.py). The fixtures under
tests/fixtures/boe/ are real pages captured 2026-09-02:
- sitemap_staff_working_paper_truncated.html: the BoE's own staff-WP sitemap
  (`/sitemap/staff-working-paper`). The live page lists ~2 460 papers over 35
  years in two sections (paper pages, then media PDFs) for 900 KB; the fixture
  keeps the page whole and both sections intact but drops every year block
  except 1992 (2 of its 5 entries) and 2025 (3 of its 58) — enough for a real
  two-era walk, small enough to read.
- wp_2025_game_theoretic_foundation.html: the real paper page for
  `/working-paper/2025/a-game-theoretic-foundation-for-the-fiscal-theory-of-
  the-price-level`, carrying "Published on 18 July 2025".
- wp_1992_financial_deregulation.html: the real 1992 paper page
  (`/working-paper/1992/financial-deregulation-and-household-saving`), which
  still carries a "Published on 01 October 1992" line.
- ..._synthetic_no_published_date.html: the SAME 1992 page with its
  `<div class="published-date">…</div>` block removed and nothing else
  touched — the shape of a page whose publication day the BoE never
  published, which the walker must degrade to year precision rather than
  invent a day for.

`discover_boe_wp` was previously reached by no test (it was only ever
monkeypatched away). No live network in these tests.
"""
from __future__ import annotations

from datetime import date

from cb_corpus.sources.boe_wp import discover_boe_wp, paper_meta
from cb_corpus.taxonomy import DocType
from tests.conftest import read_fixture

SITEMAP = "/sitemap/staff-working-paper"
WP_2025 = "/working-paper/2025/a-game-theoretic-foundation-for-the-fiscal-theory-of-the-price-level"
WP_1992 = "/working-paper/1992/financial-deregulation-and-household-saving"


def _read(name: str) -> str:
    return read_fixture("boe", name)


def _walk_fetcher(fetcher_factory):
    """The full walk: the recorded sitemap (5 papers over 1992 and 2025), the
    real 2025 paper page, and the 1992 page stripped of its published-date
    block. The other three papers have no page — as a dead paper page would.
    """
    return fetcher_factory({
        SITEMAP: _read("sitemap_staff_working_paper_truncated.html"),
        WP_1992: _read("wp_1992_financial_deregulation_synthetic_no_published_date.html"),
        WP_2025: _read("wp_2025_game_theoretic_foundation.html"),
    })


# ---- paper_meta: the "Published on" day -------------------------------

def test_paper_meta_reads_the_published_day(fetcher_factory):
    """The paper page is the only place the exact publication day exists (the
    sitemap knows the year alone), so the "Published on DD Month YYYY" line is
    what buys this source day precision instead of a 1 January placeholder."""
    f = fetcher_factory({WP_2025: _read("wp_2025_game_theoretic_foundation.html")})
    d, title, pdf = paper_meta(f, "https://www.bankofengland.co.uk" + WP_2025)

    assert d == date(2025, 7, 18)
    assert title == "A game-theoretic foundation for the fiscal theory of the price level"
    assert pdf == ("https://www.bankofengland.co.uk/-/media/boe/files/working-paper/"
                   "2025/a-game-theoretic-foundation-for-the-fiscal-theory-of-the-price-level.pdf")


def test_paper_meta_reads_the_published_day_on_a_1992_page_too(fetcher_factory):
    """The same line is read off a page from the BoE's oldest working-paper
    year — the post-migration template is uniform across 33 years, so day
    precision is not a modern-pages-only privilege."""
    f = fetcher_factory({WP_1992: _read("wp_1992_financial_deregulation.html")})
    d, title, pdf = paper_meta(f, "https://www.bankofengland.co.uk" + WP_1992)

    assert d == date(1992, 10, 1)
    assert title == "Financial Deregulation and Household Saving"
    assert pdf.endswith("/working-paper/1992/financial-deregulation-and-household-saving.pdf")


def test_paper_meta_without_a_published_line_returns_no_day(fetcher_factory):
    """A page with no published-date block yields `None` for the day — the
    walker's signal to fall back to year precision. Title and PDF must still
    come back: a missing day must not cost us the paper."""
    f = fetcher_factory({
        WP_1992: _read("wp_1992_financial_deregulation_synthetic_no_published_date.html"),
    })
    d, title, pdf = paper_meta(f, "https://www.bankofengland.co.uk" + WP_1992)

    assert d is None
    assert title == "Financial Deregulation and Household Saving"
    assert pdf.endswith("/working-paper/1992/financial-deregulation-and-household-saving.pdf")


def test_paper_meta_returns_none_when_the_page_cannot_be_fetched(fetcher_factory):
    """A dead paper page is reported as None, not raised: one 404 in a 2 400-
    paper walk must not abort the whole sitemap crawl."""
    f = fetcher_factory({})
    assert paper_meta(f, "https://www.bankofengland.co.uk" + WP_2025) is None


# ---- discover_boe_wp: sitemap -> pages -> records ---------------------

def test_discover_reads_the_sitemap_first_then_every_paper_page(fetcher_factory):
    """The sitemap is the only enumeration of the 2 400-paper back-catalogue,
    and each paper page is then read in turn — that fan-out is the shape of the
    walk, and the D1/gb provenance every yielded record must carry."""
    f = _walk_fetcher(fetcher_factory)

    recs = list(discover_boe_wp(f))

    assert f.calls[0].endswith(SITEMAP)                 # sitemap first...
    assert len(f.calls) == 6                            # ...then its 5 listed papers
    assert all(c.startswith("https://www.bankofengland.co.uk/working-paper/")
               for c in f.calls[1:])
    assert all(r.bank_code == "gb" and r.doc_type == DocType.D1 for r in recs)
    assert all(r.provenance == "bank_site" and r.date_source == "bank_site" for r in recs)
    assert all(r.mime_type == "application/pdf" for r in recs)
    assert all(r.source_url.startswith("https://www.bankofengland.co.uk/working-paper/")
               for r in recs)


def test_discover_keeps_the_day_read_from_the_paper_page(fetcher_factory):
    """A paper whose page carries "Published on" is dated to that exact day —
    the whole reason the walk pays for one fetch per paper instead of taking
    the year off the sitemap and stopping there."""
    f = _walk_fetcher(fetcher_factory)

    recs = list(discover_boe_wp(f))

    modern = next(r for r in recs if r.source_url.endswith(WP_2025))
    assert modern.date == date(2025, 7, 18)
    assert modern.date_precision == "day"


def test_discover_falls_back_to_year_precision_when_the_page_has_no_day(fetcher_factory):
    """A page with no published-date block still yields its paper, dated
    1 January of the sitemap's year and LABELLED `year` — the corpus keeps the
    document and stays honest about how precise its date is, instead of
    dropping it or passing 1 January off as the real publication day."""
    f = _walk_fetcher(fetcher_factory)

    recs = list(discover_boe_wp(f))

    old = next(r for r in recs if r.source_url.endswith(WP_1992))
    assert old.date == date(1992, 1, 1)
    assert old.date_precision == "year"
    assert old.title == "Financial Deregulation and Household Saving"


def test_discover_skips_papers_whose_page_is_unreachable(fetcher_factory):
    """Three of the five listed papers have no reachable page. Each is
    attempted and then skipped: a handful of dead pages must cost their own
    papers, never the rest of the walk."""
    f = _walk_fetcher(fetcher_factory)

    recs = list(discover_boe_wp(f))

    assert len(f.calls) == 6                            # all five were attempted
    assert len(recs) == 2                               # only the two that answered
    assert {r.source_url for r in recs} == {
        "https://www.bankofengland.co.uk" + WP_1992,
        "https://www.bankofengland.co.uk" + WP_2025,
    }


def test_discover_years_filter_skips_other_years_before_fetching_them(fetcher_factory):
    """`years` narrows the walk at the sitemap, before any paper-page fetch:
    a nightly incremental run must cost a handful of requests, not 2 400."""
    f = fetcher_factory({
        SITEMAP: _read("sitemap_staff_working_paper_truncated.html"),
        WP_2025: _read("wp_2025_game_theoretic_foundation.html"),
    })
    recs = list(discover_boe_wp(f, years={2025}))

    assert not any("/working-paper/1992/" in c for c in f.calls)
    assert len(f.calls) == 4                            # 1 sitemap + 2025's 3 papers
    assert [r.date for r in recs] == [date(2025, 7, 18)]


def test_discover_since_drops_papers_older_than_the_cutoff(fetcher_factory):
    """With `since`, the sitemap walk is bounded to that year onwards AND any
    paper whose real published day predates the cutoff is dropped — so an
    incremental run never re-emits the back-catalogue."""
    f = fetcher_factory({
        SITEMAP: _read("sitemap_staff_working_paper_truncated.html"),
        WP_1992: _read("wp_1992_financial_deregulation.html"),
        WP_2025: _read("wp_2025_game_theoretic_foundation.html"),
    })
    recs = list(discover_boe_wp(f, since=date(2025, 8, 1)))

    assert not any("/working-paper/1992/" in c for c in f.calls)
    assert recs == []                                   # 18 July 2025 is before the cutoff
