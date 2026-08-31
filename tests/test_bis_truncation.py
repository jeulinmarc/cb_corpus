from cb_corpus.runreport import SourceStats
from cb_corpus.sources.bis_speeches import BISSpeechIndex


class FakeFetcher:
    """Serves the sitemap index, explodes on one year, serves the other."""

    def __init__(self, responses):
        self.responses = responses  # url substring -> text | Exception

    def get_text(self, url):
        for key, val in self.responses.items():
            if key in url:
                if isinstance(val, Exception):
                    raise val
                return val
        raise AssertionError(f"unexpected url {url}")


INDEX_XML = """<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
<sitemap><loc>https://www.bis.org/sitemap_documents_2010.xml</loc></sitemap>
<sitemap><loc>https://www.bis.org/sitemap_documents_2011.xml</loc></sitemap>
</sitemapindex>"""

EMPTY_YEAR_XML = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"></urlset>"""


def test_dead_year_is_isolated_and_recorded():
    fetcher = FakeFetcher({
        "sitemapindex": INDEX_XML, "sitemap.xml": INDEX_XML,
        "documents_2010": ConnectionError("boom 2010"),
        "documents_2011": EMPTY_YEAR_XML,
    })
    stats = SourceStats("bis-sitemap")
    recs = list(BISSpeechIndex(fetcher).discover(stats=stats))
    assert recs == []  # 2011 empty, 2010 dead — but the walk finished
    assert stats.truncated is True
    assert stats.fetch_errors == 1
    assert "2010" in stats.error_samples[0]


def test_dead_index_records_truncated_instead_of_raising():
    fetcher = FakeFetcher({"sitemap": ConnectionError("index down")})
    stats = SourceStats("bis-sitemap")
    assert list(BISSpeechIndex(fetcher).discover(stats=stats)) == []
    assert stats.truncated is True


def test_without_stats_behavior_is_unchanged():
    fetcher = FakeFetcher({"sitemap": ConnectionError("index down")})
    try:
        list(BISSpeechIndex(fetcher).discover())
        raised = False
    except ConnectionError:
        raised = True
    assert raised
