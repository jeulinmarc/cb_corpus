"""Deutsche Bundesbank Discussion Papers (D1) from bundesbank.de.

The paginated listing ``/en/publications/research/discussion-papers?page=N``
links paper pages ``/discussion-papers/{slug}-{id}``. Each paper page carries the
title (og:title), the DP number ("No 14/2026"), the publication day, and an
**opaque blob PDF** ``/resource/blob/{id}/.../{YYYY-MM-DD}-dkp-{NN}-data.pdf``
that must be read from the page, never derived. The blob filename conveniently
embeds both the ISO date and the DP number.

Match key: the DP number ``(num, year)`` — parseable from the blob filename and
from the RePEc handle ``zbw:bubdps:{NN}{YYYY}``. Newer handles are bare global
EconStor ids with no DP number; those fall through to the title tier in
wp_migrate. (The manifest's de rows are EconStor copies, so URL/number never
match directly — number-from-handle + title carry the join.)
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import Fetcher
from ..models import DocRecord
from ..taxonomy import DocType

BUBA = "https://www.bundesbank.de"
LISTING = BUBA + "/en/publications/research/discussion-papers"
_PAPER_RE = re.compile(r"/en/publications/research/discussion-papers/[a-z0-9][a-z0-9\-]+-\d+")
_BLOB_RE = re.compile(r"/resource/blob/\d+/[a-f0-9]+/[A-F0-9]+/[^\"'\s]+-data\.pdf", re.I)
_BLOB_DATE_NUM = re.compile(r"/(\d{4})-(\d{2})-(\d{2})-dkp-?(\d+)", re.I)
_DATE_DMY = re.compile(r"(\d{1,2})\.(\d{1,2})\.((?:19|20)\d{2})")


def de_blob_key(url: str):
    """(num, year) from a blob PDF filename (…/YYYY-MM-DD-dkp-NN-data.pdf), else None."""
    m = _BLOB_DATE_NUM.search(url or "")
    return (int(m.group(4)), int(m.group(1))) if m else None


def de_handle_key(handle: str):
    """(num, year) from a zbw:bubdps handle ``{NN}{YYYY}``; None for the bare global
    EconStor ids used for recent papers (year out of range)."""
    m = re.search(r"bubdps[:/](\d{1,3})(\d{4})[a-z]?(?:\.|$|/)", handle or "", re.I)
    if not m:
        return None
    num, year = int(m.group(1)), int(m.group(2))
    return (num, year) if 1990 <= year <= 2035 else None


def parse_de_paper(html: str, base_url: str = BUBA
                   ) -> Optional[tuple[Optional[date], str, str]]:
    """(date, title, blob_pdf_url) from a Bundesbank discussion-paper page, else None.

    Date is taken from the blob filename's ISO prefix (unambiguous); falls back to
    the first DD.MM.YYYY on the page.
    """
    m = _BLOB_RE.search(html or "")
    if not m:
        return None
    pdf = urljoin(base_url, m.group(0))
    d = None
    mb = _BLOB_DATE_NUM.search(pdf)
    if mb:
        try:
            d = date(int(mb.group(1)), int(mb.group(2)), int(mb.group(3)))
        except ValueError:
            d = None
    if d is None:
        md = _DATE_DMY.search(html)
        if md:
            try:
                d = date(int(md.group(3)), int(md.group(2)), int(md.group(1)))
            except ValueError:
                d = None
    soup = BeautifulSoup(html, "lxml")
    title = ""
    for meta in soup.find_all("meta"):
        if (meta.get("property") or "").lower() == "og:title":
            title = (meta.get("content") or "").strip()
            break
    if not title and soup.title:
        title = soup.title.get_text(strip=True)
    return d, title, pdf


def _listing_paper_urls(fetcher: Fetcher, max_pages: int = 200) -> Iterator[str]:
    """Yield paper-page URLs across the paginated listing (stops when a page adds
    no new links)."""
    seen: set[str] = set()
    for page in range(1, max_pages + 1):
        url = LISTING if page == 1 else f"{LISTING}?page={page}"
        try:
            html = fetcher.get_text(url)
        except Exception:
            break
        new = [urljoin(BUBA, h) for h in dict.fromkeys(_PAPER_RE.findall(html))
               if urljoin(BUBA, h) not in seen]
        if not new:
            break
        for u in new:
            seen.add(u)
            yield u


def discover_buba_wp(fetcher: Fetcher, since: Optional[date] = None,
                     max_pages: int = 200) -> Iterator[DocRecord]:
    """Yield Bundesbank Discussion Papers (D1) with the exact day from each page.

    Walks the paginated listing newest-first, reading each paper page for its
    blob PDF + date + title. With `since`, stops once an entire listing page is
    older than the cutoff (the listing is reverse-chronological).
    """
    for page_url in _listing_paper_urls(fetcher, max_pages):
        try:
            html = fetcher.get_text(page_url)
        except Exception:
            continue
        got = parse_de_paper(html, page_url)
        if got is None:
            continue
        d, title, pdf = got
        if since and d and d < since:
            continue
        yield DocRecord(
            bank_code="de", doc_type=DocType.D1, title=title or page_url.rsplit("/", 1)[-1],
            pdf_url=pdf, source_url=page_url, date=d, provenance="bank_site",
            mime_type="application/pdf",
            date_precision="day" if d else "month", date_source="bank_site",
        )
