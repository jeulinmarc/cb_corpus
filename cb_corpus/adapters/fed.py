"""Federal Reserve (Board of Governors) adapter -- worked example.

Adds the FOMC native listings on federalreserve.gov:
  A2  policy statements      ~8/yr
  A3  FOMC minutes           ~8/yr   files/fomcminutes<YYYYMMDD>.pdf
  F1  SEP projections        ~4/yr   files/fomcprojtabl<YYYYMMDD>.pdf

Selectors/URL templates follow the published federalreserve.gov layout and are
isolated in pure helpers (`parse_minutes_links`) for easy validation. Speeches
(C1) and FEDS/IFDP papers (D1) come from the base class automatically.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import DocRecord
from ..taxonomy import DocType
from .base import BankAdapter, register

FED = "https://www.federalreserve.gov"
CAL = FED + "/monetarypolicy/fomccalendars.htm"
HIST = FED + "/monetarypolicy/fomchistorical{year}.htm"

_MIN_RE = re.compile(r"fomcminutes(\d{8})\.pdf$", re.I)
_SEP_RE = re.compile(r"fomcprojtabl(\d{8})\.pdf$", re.I)


def _date8(s: str) -> Optional[date]:
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except ValueError:
        return None


def parse_minutes_links(html: str, base_url: str = FED) -> list[tuple[date, str]]:
    """Return (meeting_date, pdf_url) for FOMC minutes linked on a page."""
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[date, str]] = []
    for a in soup.find_all("a", href=True):
        m = _MIN_RE.search(a["href"])
        if m:
            d = _date8(m.group(1))
            if d:
                out.append((d, urljoin(base_url, a["href"])))
    return out


def parse_sep_links(html: str, base_url: str = FED) -> list[tuple[date, str]]:
    soup = BeautifulSoup(html, "lxml")
    out: list[tuple[date, str]] = []
    for a in soup.find_all("a", href=True):
        m = _SEP_RE.search(a["href"])
        if m:
            d = _date8(m.group(1))
            if d:
                out.append((d, urljoin(base_url, a["href"])))
    return out


@register("us")
class FedAdapter(BankAdapter):
    native_types = (DocType.A3, DocType.F1)
    expected_per_year = {DocType.A2: 8, DocType.A3: 8, DocType.F1: 4}

    def _year_pages(self, since: Optional[date]) -> Iterator[tuple[int, str]]:
        start = since.year if since else 2000
        # Current/recent years live on the calendars page; older years on the
        # per-year historical pages.
        yield (date.today().year, CAL)
        for y in range(start, date.today().year):
            yield (y, HIST.format(year=y))

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        parser = {DocType.A3: parse_minutes_links,
                  DocType.F1: parse_sep_links}.get(doc_type)
        if parser is None:
            return
        seen: set[str] = set()
        for _year, url in self._year_pages(since):
            try:
                html = self.fetcher.get_text(url)
            except Exception:
                continue
            for d, pdf in parser(html):
                if pdf in seen or (since and d < since):
                    continue
                seen.add(pdf)
                label = ("FOMC minutes" if doc_type == DocType.A3
                         else "Summary of Economic Projections")
                yield DocRecord(
                    bank_code="us", doc_type=doc_type,
                    title=f"{label} {d.isoformat()}",
                    pdf_url=pdf, source_url=url, date=d,
                    provenance="bank_site",
                    mime_type="application/pdf",
                )
