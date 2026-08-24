"""Banque de France Working Papers (D1) from publications.banque-france.fr /
www.banque-france.fr.

Two publication systems (see spec docs/superpowers/specs/2026-08-24-fr-native-wp-design.md):

- **Legacy** ``publications.banque-france.fr`` — frozen archive 1994 -> Sept
  2023 (last n°924). One page per calendar year,
  ``/liste-chronologique/documents-de-travail_year={YYYY}.html``, listing
  every paper of that year in a ``div.node-publication`` block per paper: the
  category span carries ``n°NUM``, ``div.element2`` the title, the
  ``date-display-single`` span the day (machine-readable ``content`` ISO
  datetime, human text "Publié le DD/MM/YYYY" as fallback), and a ``.pdf``
  link somewhere in the block. Filenames are wildly inconsistent across years
  — ``document-de-travail_NNN_YYYY.pdf``, ``working-paper-NNN-month-YYYY.pdf``,
  ``doc_de_travail_NNN_-_YYYYMMDD.pdf``, ``dtNNN.pdf``, ``wpNNN.pdf``,
  ``wpNNN_0.pdf`` / ``wpNNN_1.pdf`` (re-upload / revised-version suffixes) —
  so the PDF is ALWAYS the link the row itself carries, never derived from a
  pattern. 1995 is a confirmed gap (404); any other missing/absent/empty year
  page is skipped the same way (per boj_wp's pattern).

  Days are a uniform CMS placeholder of "01" for older years (no real day was
  ever recorded) but genuine for later years. Since a paper's own row gives no
  way to tell "true 1st" from "placeholder 1st" in isolation, precision is
  decided PER YEAR PAGE from the full sample: if every row on the page falls
  on day 1, the page is month precision (dates normalized to day 1); if any
  row shows a different day, that proves the page carries real days, so ALL
  its rows (including any genuine 1sts) are trusted as day precision.

- **New** ``www.banque-france.fr/.../working-papers`` — the paginated,
  day-precision listing (2018 -> present). Not implemented here (Task 2).

This module currently exposes only the legacy-archive walker
(``_iter_legacy``); the new-system walker and the merged
``discover_fr_wp`` entry point land in later tasks of the same plan.
"""
from __future__ import annotations

import re
import sys
from datetime import date
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import Fetcher
from ..models import DocRecord
from ..taxonomy import DocType

BDF_LEGACY = "https://publications.banque-france.fr"
_LEGACY_YEAR = BDF_LEGACY + "/liste-chronologique/documents-de-travail_year={year}.html"
# Frozen archive bounds: earliest year page that exists, and the last year
# before the site's own hand-off to the new system (last legacy paper n°924,
# Sept 2023). 1995 is a confirmed 404 gap; probed anyway like every other year.
_LEGACY_FIRST_YEAR = 1994
_LEGACY_LAST_YEAR = 2023

_NUM_RE = re.compile(r"n°\s*(\d+)", re.I)
_DMY_RE = re.compile(r"(\d{1,2})/(\d{1,2})/((?:19|20)\d{2})")


def _legacy_row_date(row) -> Optional[date]:
    """Parse a legacy row's date span: prefer the machine-readable ISO
    ``content`` attribute, fall back to the "Publié le DD/MM/YYYY" text."""
    span = row.find("span", class_="date-display-single")
    if span is None:
        return None
    content = (span.get("content") or "").strip()
    if content:
        try:
            return date.fromisoformat(content[:10])
        except ValueError:
            pass
    m = _DMY_RE.search(span.get_text(" ", strip=True))
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            return None
    return None


def parse_legacy_year(html: str, base_url: str = BDF_LEGACY
                      ) -> list[tuple[Optional[date], str, int, str, str]]:
    """From a BdF legacy year-listing page, return (date, precision, number,
    title, pdf_url) per paper.

    Content-based: each ``div.node-publication`` block is one paper. A row
    missing its number, date, or PDF link is malformed and is skipped with a
    stderr warning rather than raising or silently dropping the whole page.
    See the module docstring for the per-page precision rule.
    """
    soup = BeautifulSoup(html, "lxml")
    parsed: list[tuple[date, int, str, str]] = []
    for row in soup.find_all("div", class_="node-publication"):
        cat = row.find("span", class_="category")
        m = _NUM_RE.search(cat.get_text(" ", strip=True)) if cat else None
        if not m:
            print(f"WARNING [bdf-legacy] row with no paper number, skipped: {base_url}",
                  file=sys.stderr, flush=True)
            continue
        number = int(m.group(1))

        pdf_a = next((a for a in row.find_all("a", href=True)
                      if a["href"].lower().endswith(".pdf")), None)
        if pdf_a is None:
            print(f"WARNING [bdf-legacy] n°{number} has no PDF link, skipped: {base_url}",
                  file=sys.stderr, flush=True)
            continue
        pdf_url = urljoin(base_url, pdf_a["href"])

        d = _legacy_row_date(row)
        if d is None:
            print(f"WARNING [bdf-legacy] n°{number} has no parseable date, skipped: {base_url}",
                  file=sys.stderr, flush=True)
            continue

        title_el = row.find("div", class_="element2")
        title = title_el.get_text(" ", strip=True) if title_el else ""
        if not title:
            title = f"Documents de travail n°{number}"

        parsed.append((d, number, title, pdf_url))

    if not parsed:
        return []

    # Per-page precision: any non-1 day proves the page carries real days.
    page_is_day = any(d.day != 1 for d, *_ in parsed)
    prec = "day" if page_is_day else "month"
    out: list[tuple[Optional[date], str, int, str, str]] = []
    for d, number, title, pdf_url in parsed:
        dd = d if page_is_day else date(d.year, d.month, 1)
        out.append((dd, prec, number, title, pdf_url))
    return out


def _iter_legacy(fetcher: Fetcher, years: Optional[set] = None
                 ) -> Iterator[tuple[int, DocRecord]]:
    """Yield (WP number, DocRecord) for BdF legacy Working Papers (D1),
    1994..2023 (frozen archive, last n°924). Walks year pages newest-first; a
    year whose page is missing, absent (e.g. the 1995 gap), or empty is
    skipped gracefully. `years` restricts the walk to those calendar years.

    The number is yielded alongside the record (private, single-consumer
    contract) because it is IRRECOVERABLE from the built DocRecord alone —
    titles don't reliably carry it and filenames are inconsistent (see module
    docstring) — and Task 3's cross-system merge needs it to dedup legacy
    rows against the new-system walker's overlap-era papers."""
    for y in range(_LEGACY_LAST_YEAR, _LEGACY_FIRST_YEAR - 1, -1):
        if years is not None and y not in years:
            continue
        url = _LEGACY_YEAR.format(year=y)
        try:
            html = fetcher.get_text(url)
        except Exception:
            continue
        for d, prec, number, title, pdf_url in parse_legacy_year(html, url):
            yield number, DocRecord(
                bank_code="fr", doc_type=DocType.D1, title=title,
                pdf_url=pdf_url, source_url=url, date=d, provenance="bank_site",
                mime_type="application/pdf",
                date_precision=prec, date_source="bank_site",
            )
