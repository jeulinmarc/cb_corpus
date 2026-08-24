"""Banque de France adapter.

Native D1 Working Papers, merged across both BdF publication systems (legacy
1994-2023 + current 2018-present, deduped on the continuous WP number) --
see sources/bdf_wp.py. E2 (financial stability review) stays on the
sitemap-driven listing that used to be fr's whole config in
banks_sources.toml; that config is reproduced here, inline, because a
hand-written ADAPTERS registration takes precedence over the declarative
TOML factory (see adapters/base.py:get_adapter) and would otherwise
silently drop E2 the moment fr gets a hand-written class for D1. Speeches
(C1) and any other RePEc series come from the base class. Hand-written
(rather than the declarative TOML path) because D1 needs two python
scrapers merged, not a listing regex -- same reasoning as jp (boj.py).
"""
from __future__ import annotations

from datetime import date
from typing import Iterator, Optional

from ..models import DocRecord
from ..taxonomy import DocType
from .base import register
from .generic_sitemap import GenericSitemapAdapter

# Formerly [fr.sitemap] in banks_sources.toml -- moved here verbatim.
_SITEMAP_URL = "https://www.banque-france.fr/sitemap.xml"
_PATTERNS = {DocType.E2: r"/publications/.*stabilite-financiere.*\.pdf$"}


@register("fr")
class BdfAdapter(GenericSitemapAdapter):
    def __init__(self, bank, fetcher=None):
        super().__init__(bank, fetcher, sitemap_url=_SITEMAP_URL, patterns=_PATTERNS)
        # D1 = Working Paper Series, native (merged legacy + new-system walk).
        self.native_types = self.native_types + (DocType.D1,)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        if doc_type == DocType.D1:
            from ..sources.bdf_wp import discover_fr_wp
            yield from discover_fr_wp(self.fetcher, since)
            return
        yield from super()._discover_native(doc_type, since)
