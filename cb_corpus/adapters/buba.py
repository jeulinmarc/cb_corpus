"""Deutsche Bundesbank adapter.

Native D1 Discussion Papers from the Bundesbank's own paginated listing (each
paper page carries the day + opaque blob PDF) — see sources/buba_wp.py. Speeches
(C1) and any RePEc series come from the base class.
"""
from __future__ import annotations

from datetime import date
from typing import Iterator, Optional

from ..models import DocRecord
from ..taxonomy import DocType
from .base import BankAdapter, register


@register("de")
class BubaAdapter(BankAdapter):
    native_types = (DocType.D1,)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        if doc_type == DocType.D1:
            from ..sources.buba_wp import discover_buba_wp
            yield from discover_buba_wp(self.fetcher, since)
