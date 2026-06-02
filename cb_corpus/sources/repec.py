"""RePEc / IDEAS working-paper URL discovery  ->  DocRecord(D1/D2).

IDEAS lists papers by series; each paper page has a "download" link to the
canonical PDF. In v2 we accept that PDF regardless of host: the bank's own
domain is preferred, but EconStor / SSRN / RePEc-cached copies are kept as
fallback so we don't drop documents whose only public source is non-bank.

IDEAS series page:  https://ideas.repec.org/s/<handle>.html
IDEAS paper page:   https://ideas.repec.org/<...>.html  -> has a download link

SERIES is a seed registry for the major banks. Handles should be confirmed on
IDEAS and extended toward all 63 banks; the crawler accepts any handle.
"""
from __future__ import annotations

from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..banks import get_bank
from ..http import Fetcher, host_matches
from ..models import DocRecord
from ..taxonomy import DocType

IDEAS = "https://ideas.repec.org"

# bank_code -> [(repec_handle, doc_type)]  (verified seed; extend cautiously)
SERIES: dict[str, list[tuple[str, DocType]]] = {
    "ecb": [("ecb:ecbwps", DocType.D1), ("ecb:ecbops", DocType.D2)],
    "us":  [("fip:fedgfe", DocType.D1), ("fip:fedgif", DocType.D1)],
    "gb":  [("boe:boeewp", DocType.D1)],
    "de":  [("zbw:bubdps", DocType.D1)],
    "it":  [("bdi:wptemi", DocType.D1)],
    "es":  [("bde:wpaper", DocType.D1)],
    "fr":  [("bfr:banfra", DocType.D1)],
    "ca":  [("bca:bocawp", DocType.D1)],
    "jp":  [("boj:bojwps", DocType.D1)],
    "ch":  [("snb:snbwpa", DocType.D1)],
    "se":  [("hhs:rbnkwp", DocType.D1)],
    "nl":  [("dnb:dnbwpp", DocType.D1)],
    "au":  [("rba:rbardp", DocType.D1)],
}


def parse_series_page(html: str, base_url: str = IDEAS) -> list[str]:
    """Return absolute IDEAS paper-page URLs listed on a series page."""
    soup = BeautifulSoup(html, "lxml")
    urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if (href.startswith(("/a/", "/p/", "/h/")) or "/p/" in href) \
                and href.endswith(".html"):
            urls.append(urljoin(base_url, href))
    return list(dict.fromkeys(urls))


def extract_pdf(paper_html: str, bank_homepage: Optional[str] = None) -> Optional[str]:
    """Return the best PDF link found on an IDEAS paper page.

    Preference order:
      1. PDF on the bank's own domain (e.g. boe.co.uk for Bank of England)
      2. PDF on bis.org (BIS re-hosts some)
      3. Any other absolute PDF link (EconStor, SSRN, RePEc cached, ...).

    Returns None if no PDF link is present.
    """
    soup = BeautifulSoup(paper_html, "lxml")
    candidates: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.startswith("http"):
            continue
        if not (href.lower().endswith(".pdf") or "pdf" in href.lower()):
            continue
        candidates.append(href)
    if not candidates:
        return None
    if bank_homepage:
        for href in candidates:
            if host_matches(href, bank_homepage):
                return href
    for href in candidates:
        if host_matches(href, "bis.org"):
            return href
    return candidates[0]


def extract_official_pdf(paper_html: str, bank_homepage: str,
                         allow_bis: bool = True) -> Optional[str]:
    """Legacy strict variant: only return a PDF on the bank's domain (or BIS).

    Kept for backward compatibility with the existing test fixture.
    """
    soup = BeautifulSoup(paper_html, "lxml")
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if not href.lower().endswith(".pdf") and "pdf" not in href.lower():
            continue
        if not href.startswith("http"):
            continue
        if host_matches(href, bank_homepage):
            return href
        if allow_bis and host_matches(href, "bis.org"):
            return href
    return None


class RePEcDiscovery:
    def __init__(self, fetcher: Optional[Fetcher] = None,
                 max_items_per_series: int = 5000):
        self.fetcher = fetcher or Fetcher()
        self.max_items = max_items_per_series

    def discover_bank(self, bank_code: str) -> Iterator[DocRecord]:
        bank = get_bank(bank_code)
        for handle, doc_type in SERIES.get(bank_code, []):
            series_url = f"{IDEAS}/s/{handle.replace(':', '/')}.html"
            try:
                listing = self.fetcher.get_text(series_url)
            except Exception:
                continue
            for paper_url in parse_series_page(listing)[: self.max_items]:
                try:
                    paper_html = self.fetcher.get_text(paper_url)
                except Exception:
                    continue
                pdf = extract_pdf(paper_html, bank.homepage)
                if pdf is None:
                    continue
                title = _title_of(paper_html)
                yield DocRecord(
                    bank_code=bank_code,
                    doc_type=doc_type,
                    title=title,
                    pdf_url=pdf,
                    source_url=paper_url,
                    provenance="repec_discovery",
                    mime_type="application/pdf",
                )


def _title_of(paper_html: str) -> str:
    soup = BeautifulSoup(paper_html, "lxml")
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(" ", strip=True)
    if soup.title:
        return soup.title.get_text(strip=True)
    return "(untitled)"
