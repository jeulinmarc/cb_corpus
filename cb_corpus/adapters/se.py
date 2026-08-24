"""Sveriges Riksbank adapter.

Native D1 Working Papers from the bank's own paginated listing (exact
publication day, PDF link inline — no per-paper fetch) — see
sources/riksbank_wp.py. RePEc (hhs:rbnkwp) lagged the bank site (IDEAS stopped
at #465 while riksbank.se already listed #466-471 live); this native source is
now the current one for D1. Speeches (C1) come from the base class.

Native coverage starts ~2016 (the site's own year filter stops there; older
papers sit behind a separate "Archive" mirror, out of scope). Those older years
keep their existing RePEc/Wayback-sourced manifest rows untouched — this
adapter does not rediscover them, but does not drop them either.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator, Optional

from ..models import DocRecord
from ..taxonomy import DocType
from .base import BankAdapter, register


@register("se")
class RiksbankAdapter(BankAdapter):
    native_types = (DocType.D1,)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        if doc_type == DocType.D1:
            from ..sources.riksbank_wp import discover_riksbank_wp
            yield from discover_riksbank_wp(self.fetcher, since)
