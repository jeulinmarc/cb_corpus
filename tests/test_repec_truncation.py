"""B4: the three IDEAS/RePEc pagination call sites must record a page-fetch
failure as TRUNCATION, never as a silent end-of-list.

Site 1: cb_corpus/sources/repec.py -- RePEcDiscovery._series_paper_pages
         (discovery, driven through discover_bank).
Site 2: cb_corpus/repec_check.py -- enumerate_series (the completeness audit
         tool).
Site 3: cb_corpus/pipeline.py -- run_repec_wayback_recovery. It has no
         pagination loop of its own (it re-discovers via the SAME
         RePEcDiscovery.discover_bank as site 1) but, before this task, it
         never threaded a SourceStats down into that walk at all -- a page
         failure during Wayback re-discovery was invisible, full stop. This
         is the "third pagination copy" the task brief points at in the
         wayback/recovery path.

Listing markup below mirrors the REAL builders already used elsewhere in
this suite to drive these exact parsers (tests/fixtures has no RePEc/IDEAS
HTML of its own -- `grep -rl "repec|ideas" tests/fixtures` is empty):
  - the `/p/<arch>/<slug>/<pid>.html` <li><a> shape from
    tests/test_repec_incremental.py::_series_html (parse_series_page,
    site 1) and tests/test_repec_reconcile.py::_series_html
    (parse_series_listing, site 2) -- both already verified against the
    production regexes they feed.
"""
from cb_corpus.repec_check import enumerate_series
from cb_corpus.runreport import RunReport, SourceStats
from cb_corpus.sources.repec import IDEAS, RePEcDiscovery


class FakeFetcher:
    def __init__(self, pages):
        self.pages = pages  # page number -> html | Exception
        self.calls = 0

    def get_text(self, url):
        self.calls += 1
        # page 1 is series.html, page N is seriesN.html
        import re
        m = re.search(r"(\d+)\.html$", url)
        page = int(m.group(1)) if m else 1
        val = self.pages.get(page)
        if isinstance(val, Exception):
            raise val
        if val is None:
            raise AssertionError(f"unexpected page {page}")
        return val


def _page_html(ids):
    """An IDEAS series-listing page for repec_check.enumerate_series
    (handle "arch:series"), mirroring test_repec_reconcile.py::_series_html
    (real, already-regex-verified markup for parse_series_listing) --
    templated here to carry arbitrary paper ids.
    """
    links = "".join(
        f'<li><a href="/p/arch/series/{i}.html">Paper {i}</a></li>'
        for i in ids
    )
    return f"<html><body><ul>{links}</ul></body></html>"


def _discovery_page_html(ids):
    """An IDEAS series-listing page for RePEcDiscovery._series_paper_pages
    (handle "hhs:rbnkwp"), mirroring test_repec_incremental.py::_series_html
    (real, already-verified markup for parse_series_page).
    """
    links = "".join(
        f'<li><a href="/p/hhs/rbnkwp/{i}.html">P{i}</a></li>' for i in ids
    )
    return f"<html><body><ul>{links}</ul></body></html>"


def _mk_discovery(fetcher):
    d = RePEcDiscovery.__new__(RePEcDiscovery)
    d.fetcher = fetcher
    d.max_pages = 80
    d.max_items = 5000
    return d


# ---------------------------------------------------------------------------
# Site 2: repec_check.enumerate_series
# ---------------------------------------------------------------------------

def test_page_failure_is_recorded_as_truncation():
    fetcher = FakeFetcher({1: _page_html(["p1", "p2"]), 2: ConnectionError("boom")})
    stats = SourceStats("ecb")
    rows = enumerate_series(fetcher, "arch:series", stats=stats)
    assert len(rows) == 2  # page 1 kept
    assert stats.truncated is True and stats.fetch_errors == 1


def test_true_end_of_pagination_is_not_truncation():
    fetcher = FakeFetcher({1: _page_html(["p1"]), 2: _page_html(["p1"])})  # no new -> stop
    stats = SourceStats("ecb")
    enumerate_series(fetcher, "arch:series", stats=stats)
    assert stats.truncated is False and stats.fetch_errors == 0


def test_enumerate_series_stats_none_preserves_silent_break():
    """Untouched callers (stats=None, the default) keep today's silent-break
    behavior -- no crash, just a shorter-than-expected result."""
    fetcher = FakeFetcher({1: _page_html(["p1"]), 2: ConnectionError("boom")})
    rows = enumerate_series(fetcher, "arch:series")
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# I4: the two PRODUCTION callers of enumerate_series (run_repec_check,
# run_repec_reconcile via _walk_entries) used to pass stats=None -- a
# truncated IDEAS listing was silently absorbed into "fewer papers than
# expected", indistinguishable from a genuinely short series. Both now build
# a local SourceStats per series and print a WARNING to stderr when
# stats.truncated, so these audit commands are no longer silent about it
# (they still never write to a report file -- see their docstrings).
# ---------------------------------------------------------------------------

def _boe_page_html(ids):
    """A real gb-series (boe:boeewp, SERIES["gb"] in sources/repec.py) IDEAS
    listing page, mirroring test_repec_reconcile.py::_series_html."""
    links = "".join(
        f'<li><a href="/p/boe/boeewp/{i}.html">Paper {i}</a></li>' for i in ids
    )
    return f"<html><body><ul>{links}</ul></body></html>"


def test_run_repec_check_prints_truncation_warning(tmp_path, monkeypatch, capsys):
    from cb_corpus.config import Config
    from cb_corpus import repec_check as RC

    fetcher = FakeFetcher({1: _boe_page_html(["20250001"]), 2: ConnectionError("boom")})
    monkeypatch.setattr(RC, "Fetcher", lambda cfg: fetcher)
    cfg = Config(data_dir=tmp_path / "data")
    RC.run_repec_check(bank_codes=["gb"], config=cfg)
    err = capsys.readouterr().err
    assert "WARNING: IDEAS listing truncated for boe:boeewp" in err


def test_run_repec_reconcile_prints_truncation_warning(tmp_path, capsys):
    from cb_corpus.config import Config
    from cb_corpus.repec_check import run_repec_reconcile

    fetcher = FakeFetcher({1: _boe_page_html(["20250001"]), 2: ConnectionError("boom")})
    cfg = Config(data_dir=tmp_path / "data")
    run_repec_reconcile(bank_codes=["gb"], config=cfg, fetcher=fetcher)
    err = capsys.readouterr().err
    assert "WARNING: IDEAS listing truncated for boe:boeewp" in err


# ---------------------------------------------------------------------------
# Site 1: sources/repec.py::RePEcDiscovery._series_paper_pages (via discover_bank,
# the production shape -- driven through the class exactly like real callers do)
# ---------------------------------------------------------------------------

def test_discovery_page_failure_is_recorded_as_truncation():
    fetcher = FakeFetcher({1: _discovery_page_html(["0001", "0002"]),
                           2: ConnectionError("boom")})
    d = _mk_discovery(fetcher)
    stats = SourceStats("se")
    pages = list(d._series_paper_pages("hhs:rbnkwp", stats=stats))
    assert pages == [[f"{IDEAS}/p/hhs/rbnkwp/0001.html", f"{IDEAS}/p/hhs/rbnkwp/0002.html"]]
    assert stats.truncated is True and stats.fetch_errors == 1


def test_discovery_true_end_of_pagination_is_not_truncation():
    fetcher = FakeFetcher({1: _discovery_page_html(["0001"]),
                           2: _discovery_page_html(["0001"])})  # no new -> stop
    d = _mk_discovery(fetcher)
    stats = SourceStats("se")
    list(d._series_paper_pages("hhs:rbnkwp", stats=stats))
    assert stats.truncated is False and stats.fetch_errors == 0


# ---------------------------------------------------------------------------
# Site 3: pipeline.run_repec_wayback_recovery -- no pagination loop of its
# own; before this task it never threaded a SourceStats into the shared
# RePEcDiscovery.discover_bank walk at all, so a page failure during
# Wayback re-discovery was silently invisible. Exercised end-to-end through
# the pipeline function (real RePEcDiscovery, real _series_paper_pages) with
# _make_storage monkeypatched to a minimal fake storage/fetcher pair --
# this is mutation-proof against BOTH reverting site 1's fix AND reverting
# the stats-threading this task adds to run_repec_wayback_recovery itself.
# ---------------------------------------------------------------------------

class _ExactURLFetcher:
    """Exact-URL-keyed fake fetcher -- the same shape as the REAL precedent
    tests/test_repec_incremental.py::FakeFetcher, used here because
    run_repec_wayback_recovery drives discover_bank end-to-end, which fetches
    BOTH listing pages and individual paper pages: a page-number/regex fetcher
    (like the module-level FakeFetcher above, built for enumerate_series which
    only ever fetches listing pages) would collide numeric paper ids with page
    numbers. Unlisted URLs 404 (RuntimeError), which discover_bank already
    silently skips per-paper -- unrelated to the pagination truncation this
    test targets.
    """
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def get_text(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError(f"404 {url}")
        return self.pages[url]


_BASE = f"{IDEAS}/s/hhs/rbnkwp"


class _FakeStorage:
    _ids = set()

    def is_known_url(self, url):
        return False

    def save_many(self, gen, dry_run=True, label=""):
        rows = list(gen)
        return {"saved": len(rows)}


def _patch_make_storage(monkeypatch, fetcher):
    import cb_corpus.pipeline as P
    monkeypatch.setattr(P, "_make_storage",
                        lambda config=None, html_to_pdf=None: (config, fetcher, _FakeStorage()))


def test_wayback_recovery_page_failure_is_recorded_as_truncation(monkeypatch):
    import cb_corpus.pipeline as P
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType

    monkeypatch.setitem(R.SERIES, "se", [("hhs:rbnkwp", DocType.D1)])
    fetcher = _ExactURLFetcher({f"{_BASE}.html": _discovery_page_html(["0001", "0002"])})
    # page 2 (f"{_BASE}2.html") is deliberately absent -> 404 -> page-fetch failure
    _patch_make_storage(monkeypatch, fetcher)

    report = RunReport("central-bank-corpus", "repec-wb")
    P.run_repec_wayback_recovery("se", dry_run=True, report=report)

    stats = report.source("repec-wb:se")
    assert stats.truncated is True and stats.fetch_errors == 1


def test_wayback_recovery_true_end_of_pagination_is_not_truncation(monkeypatch):
    import cb_corpus.pipeline as P
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType

    monkeypatch.setitem(R.SERIES, "se", [("hhs:rbnkwp", DocType.D1)])
    fetcher = _ExactURLFetcher({
        f"{_BASE}.html": _discovery_page_html(["0001"]),
        f"{_BASE}2.html": _discovery_page_html(["0001"]),  # no new -> true end
    })
    _patch_make_storage(monkeypatch, fetcher)

    report = RunReport("central-bank-corpus", "repec-wb")
    P.run_repec_wayback_recovery("se", dry_run=True, report=report)

    stats = report.source("repec-wb:se")
    assert stats.truncated is False and stats.fetch_errors == 0


def test_wayback_recovery_report_none_preserves_silent_behavior(monkeypatch):
    """report=None (the default -- unwired callers) must not crash: discover_bank
    gets stats=None, same as before this task."""
    import cb_corpus.pipeline as P
    from cb_corpus.sources import repec as R
    from cb_corpus.taxonomy import DocType

    monkeypatch.setitem(R.SERIES, "se", [("hhs:rbnkwp", DocType.D1)])
    fetcher = _ExactURLFetcher({f"{_BASE}.html": _discovery_page_html(["0001"])})
    _patch_make_storage(monkeypatch, fetcher)

    counts = P.run_repec_wayback_recovery("se", dry_run=True)
    assert counts == {"saved": 0}  # paper page itself 404s -> extract skipped, nothing saved
