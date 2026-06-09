"""Per-bank adapter framework.

A `BankAdapter` knows how to (1) discover official documents for its bank across
doc types A-F and (2) state the *expected* number of a given type in a year
(from the bank's published meeting calendar / archive) for the completeness
matrix.

`GenericAdapter` is the zero-config fallback registered for every BIS-63 bank:
it already yields speeches (C1, via the BIS index) and working papers (D, via
RePEc discovery). Concrete adapters subclass it and add the bank-specific
listings (A1/A2/A3, B, E, F) by implementing `_discover_native`.
"""
from __future__ import annotations

import sys
from abc import ABC
from datetime import date
from typing import Callable, Iterator, Optional

from ..banks import Bank, BIS_63, get_bank
from ..http import Fetcher
from ..models import DocRecord
from ..sources.bis_speeches import BISSpeechIndex
from ..sources.repec import RePEcDiscovery
from ..taxonomy import DocType, FULL_SCOPE

# Class-based registry: bespoke hand-written adapters (Fed, ECB, ...).
ADAPTERS: dict[str, type["BankAdapter"]] = {}

# Instance-factory registry: declarative TOML-configured adapters.
# Hand-written ADAPTERS take precedence over factories.
INSTANCE_FACTORIES: dict[str, Callable[[Bank, Optional[Fetcher]], "BankAdapter"]] = {}


def register(*bank_codes: str) -> Callable[[type["BankAdapter"]], type["BankAdapter"]]:
    def deco(cls: type["BankAdapter"]) -> type["BankAdapter"]:
        for code in bank_codes:
            ADAPTERS[code] = cls
        return cls
    return deco


class BankAdapter(ABC):
    #: doc types this adapter can pull directly from the bank's own site
    native_types: tuple[DocType, ...] = ()
    #: expected items per year per native type (from meeting calendar/archive);
    #: None => "count the listing at runtime"
    expected_per_year: dict[DocType, Optional[int]] = {}

    def __init__(self, bank: Bank, fetcher: Optional[Fetcher] = None):
        self.bank = bank
        self.fetcher = fetcher or Fetcher()
        self._bis = BISSpeechIndex(self.fetcher)
        self._repec = RePEcDiscovery(self.fetcher)
        #: discovery-time fetch failures recorded during this adapter's life.
        #: A non-empty list means discovery was PARTIAL — the caller should
        #: re-run (discovery is idempotent) rather than trust the result as
        #: complete. This is what stops a transient blip from silently
        #: dropping a whole year/listing.
        self.errors: list[dict] = []

    # ---- discovery -----------------------------------------------------
    def _fetch_text(self, url: str, *, context: str = "") -> Optional[str]:
        """Fetch `url`, or record the failure and return None.

        Use this instead of a bare ``try/except: continue`` so a failed listing
        fetch is visible (logged + appended to ``self.errors``) instead of being
        swallowed silently. Returning None lets the caller skip *this* item while
        the recorded error signals that a re-run is needed for completeness.
        """
        try:
            return self.fetcher.get_text(url)
        except Exception as exc:  # noqa: BLE001 - recorded, not hidden
            self.errors.append({
                "bank": self.bank.code,
                "context": context,
                "url": url,
                "error": f"{type(exc).__name__}: {exc}",
            })
            print(f"!! discovery fetch FAILED [{self.bank.code} {context}]: "
                  f"{url} -> {type(exc).__name__}", file=sys.stderr, flush=True)
            return None
    def supported_types(self) -> tuple[DocType, ...]:
        extra = (DocType.C1, DocType.D1, DocType.D2)
        return tuple(dict.fromkeys(self.native_types + extra))

    def discover(self, doc_type: DocType,
                 since: Optional[date] = None) -> Iterator[DocRecord]:
        if doc_type == DocType.C1:
            yield from self._bis.discover(since=since, only_banks={self.bank.code})
        elif doc_type in (DocType.D1, DocType.D2):
            yield from (r for r in self._repec.discover_bank(self.bank.code)
                        if r.doc_type == doc_type)
        else:
            yield from self._discover_native(doc_type, since)

    def discover_all(self, scope: tuple[DocType, ...] = FULL_SCOPE,
                     since: Optional[date] = None) -> Iterator[DocRecord]:
        for dt in scope:
            if dt in self.supported_types():
                yield from self.discover(dt, since=since)

    def _discover_native(self, doc_type: DocType,
                         since: Optional[date]) -> Iterator[DocRecord]:
        """Override in concrete adapters for A/B/E/F. Default: nothing."""
        return iter(())

    # ---- completeness --------------------------------------------------
    def expected_count(self, doc_type: DocType, year: int) -> Optional[int]:
        """Expected number of `doc_type` documents in `year`.

        Returns None when unknown (the matrix then falls back to the listing
        count crawled at runtime).
        """
        return self.expected_per_year.get(doc_type)


@register(*[b.code for b in BIS_63])
class GenericAdapter(BankAdapter):
    """Fallback: speeches + working papers only, for every bank."""
    native_types = ()


def get_adapter(bank_code: str, fetcher: Optional[Fetcher] = None) -> BankAdapter:
    bank = get_bank(bank_code)
    # 1. Bespoke hand-written class wins.
    cls = ADAPTERS.get(bank_code)
    if cls is not None and cls is not GenericAdapter:
        return cls(bank, fetcher)
    # 2. Declarative TOML factory.
    factory = INSTANCE_FACTORIES.get(bank_code)
    if factory is not None:
        return factory(bank, fetcher)
    # 3. Generic fallback.
    return GenericAdapter(bank, fetcher)
