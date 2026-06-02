"""European Central Bank adapter -- v2.

Native ECB listings on ecb.europa.eu:
  A3  monetary policy accounts   ~8/yr  (HTML-only since 2024 — stored as .html)
  E4  Economic Bulletin          ~8/yr  (PDF)

Accounts are now lazy-loaded year-by-year (`<year>/html/index_include.en.html`)
and ECB no longer publishes a PDF version — the HTML is the canonical artifact.
The Economic Bulletin index (`all_releases.en.html`) still inlines all PDF
links for every release across years.

Speeches (C1) and WPS/Occasional papers (D1/D2) come from the base class.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import DocRecord
from ..taxonomy import DocType
from .base import BankAdapter, register

ECB = "https://www.ecb.europa.eu"
ACCOUNTS_INDEX = ECB + "/press/accounts/html/index.en.html"
BULLETIN_INDEX = ECB + "/press/economic-bulletin/html/all_releases.en.html"

_ACCOUNT_HTML_RE = re.compile(r"/press/accounts/\d{4}/html/ecb\.mg(\d{6})[~a-z0-9]*\.en\.html$",
                              re.I)
_BULLETIN_PDF_RE = re.compile(r"/pub/pdf/ecbu/eb(\d{4})(\d{2})\.en\.pdf$", re.I)
_DATE_IN_HREF = re.compile(r"(\d{4})(\d{2})(\d{2})")


def _yymmdd_to_date(s: str) -> Optional[date]:
    try:
        yy, mm, dd = int(s[:2]), int(s[2:4]), int(s[4:6])
    except ValueError:
        return None
    yyyy = 1900 + yy if yy >= 90 else 2000 + yy
    try:
        return date(yyyy, mm, dd)
    except ValueError:
        return None


def parse_year_includes(html: str) -> list[str]:
    """Extract the per-year include URLs from the accounts index page."""
    soup = BeautifulSoup(html, "lxml")
    dl = soup.find(id="lazyload-container")
    if dl is None:
        return []
    snips = dl.get("data-snippets") or ""
    return [s.strip() for s in snips.split(",") if s.strip()]


def parse_account_items(html: str, base_url: str = ACCOUNTS_INDEX) -> list[tuple[date, str]]:
    """From an accounts year-include, return (date, html_url) pairs."""
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[date, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = _ACCOUNT_HTML_RE.search(href)
        if not m:
            continue
        d = _yymmdd_to_date(m.group(1))
        if d is None:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        out.append((d, url))
    return out


def parse_bulletin_pdfs(html: str, base_url: str = BULLETIN_INDEX
                       ) -> list[tuple[Optional[date], str, str]]:
    """From the Economic Bulletin all-releases page, return (date, title, pdf_url).

    Keeps only the English-language PDFs (`eb<YYYY><II>.en.pdf`).
    """
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[Optional[date], str, str]] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        m = _BULLETIN_PDF_RE.search(href)
        if not m:
            continue
        url = urljoin(base_url, href)
        if url in seen:
            continue
        seen.add(url)
        year, issue = int(m.group(1)), int(m.group(2))
        # Issue number is sequential within the year; we don't have a precise
        # date in the URL, so use Jan 1 of that year as a placeholder.
        d = date(year, 1, 1)
        title = a.get_text(" ", strip=True) or f"Economic Bulletin {year}/{issue}"
        out.append((d, title, url))
    return out


# legacy fixture parser - kept so the existing unit test still runs
def parse_index(html: str, base_url: str = ECB,
                href_must_contain: str = "") -> list[tuple[Optional[date], str, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[Optional[date], str, str]] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href_must_contain and href_must_contain not in href:
            continue
        if not href.lower().endswith(".pdf"):
            continue
        d = None
        m = _DATE_IN_HREF.search(href)
        if m:
            try:
                d = datetime.strptime("".join(m.groups()), "%Y%m%d").date()
            except ValueError:
                d = None
        out.append((d, a.get_text(" ", strip=True) or href, urljoin(base_url, href)))
    return out


@register("ecb")
class ECBAdapter(BankAdapter):
    native_types = (DocType.A3, DocType.E4)
    expected_per_year = {DocType.A3: 8, DocType.E4: 8}

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        if doc_type == DocType.A3:
            yield from self._discover_accounts(since)
        elif doc_type == DocType.E4:
            yield from self._discover_bulletin(since)

    def _discover_accounts(self, since: Optional[date]) -> Iterator[DocRecord]:
        try:
            idx = self.fetcher.get_text(ACCOUNTS_INDEX)
        except Exception:
            return
        includes = parse_year_includes(idx)
        for snippet in includes:
            year_url = urljoin(ACCOUNTS_INDEX, snippet)
            try:
                year_html = self.fetcher.get_text(year_url)
            except Exception:
                continue
            for d, url in parse_account_items(year_html, year_url):
                if since and d < since:
                    continue
                yield DocRecord(
                    bank_code="ecb", doc_type=DocType.A3,
                    title=f"Monetary policy account {d.isoformat()}",
                    pdf_url=url,
                    source_url=year_url,
                    date=d,
                    provenance="bank_site",
                    # Source page is HTML-only; Storage will render to PDF
                    # via headless Chrome when cfg.html_to_pdf is True.
                )

    def _discover_bulletin(self, since: Optional[date]) -> Iterator[DocRecord]:
        try:
            html = self.fetcher.get_text(BULLETIN_INDEX)
        except Exception:
            return
        for d, title, pdf in parse_bulletin_pdfs(html):
            if since and d and d < since:
                continue
            yield DocRecord(
                bank_code="ecb", doc_type=DocType.E4,
                title=title, pdf_url=pdf,
                source_url=BULLETIN_INDEX,
                date=d,
                provenance="bank_site",
                mime_type="application/pdf",
            )
