"""Bank of England adapter.

Native D1 Staff Working Papers from the BoE's own staff-WP sitemap (the exact
publication day is on each paper page) — see sources/boe_wp.py. Speeches (C1) and
the external-MPC discussion-paper series (D2) come from the base class for now.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator, Optional

from ..models import DocRecord
from ..taxonomy import DocType
from .base import BankAdapter, register


@register("gb")
class BoEAdapter(BankAdapter):
    native_types = (DocType.D1,)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        if doc_type == DocType.D1:
            from ..sources.boe_wp import discover_boe_wp
            yield from discover_boe_wp(self.fetcher, since)
