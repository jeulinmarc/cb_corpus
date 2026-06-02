"""Load declarative adapter configs from `banks_sources.toml`.

Each bank entry in the TOML produces a factory that builds either a
`GenericSitemapAdapter` or a `ListingCrawlerAdapter` on demand. Factories are
registered in `base.INSTANCE_FACTORIES`, behind hand-written ADAPTERS.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Optional

from ..banks import Bank
from ..http import Fetcher
from ..taxonomy import DocType, by_code
from .base import BankAdapter, INSTANCE_FACTORIES
from .generic_sitemap import GenericSitemapAdapter
from .listing_crawler import ListingCrawlerAdapter

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_TOML = Path(__file__).resolve().parents[1] / "banks_sources.toml"


def _resolve_types(d: dict) -> dict[DocType, object]:
    return {by_code(k): v for k, v in d.items()}


def _make_sitemap_factory(cfg: dict) -> Callable[[Bank, Optional[Fetcher]], BankAdapter]:
    sitemap_url = cfg["sitemap_url"]
    patterns = _resolve_types(cfg.get("patterns", {}))
    expected = _resolve_types(cfg.get("expected_per_year", {}))

    def factory(bank: Bank, fetcher: Optional[Fetcher] = None) -> BankAdapter:
        return GenericSitemapAdapter(
            bank, fetcher,
            sitemap_url=sitemap_url,
            patterns=patterns,
            expected_per_year=expected,
        )
    return factory


def _make_listing_factory(cfg: dict) -> Callable[[Bank, Optional[Fetcher]], BankAdapter]:
    raw_entries = cfg.get("entries", {})
    entries: dict[DocType, list[tuple[str, str]]] = {}
    for k, lst in raw_entries.items():
        dt = by_code(k)
        # lst is a list of [url, regex] pairs
        entries[dt] = [(item[0], item[1]) for item in lst]
    expected = _resolve_types(cfg.get("expected_per_year", {}))
    year_range = tuple(cfg["year_range"]) if "year_range" in cfg else None

    def factory(bank: Bank, fetcher: Optional[Fetcher] = None) -> BankAdapter:
        return ListingCrawlerAdapter(
            bank, fetcher,
            entries=entries,
            expected_per_year=expected,
            year_range=year_range,
        )
    return factory


def load_toml(path: Path = DEFAULT_TOML) -> int:
    """Register factories from a TOML config. Returns number of banks registered."""
    if not path.exists():
        return 0
    data = tomllib.loads(path.read_text())
    n = 0
    for bank_code, sections in data.items():
        if "sitemap" in sections:
            INSTANCE_FACTORIES[bank_code] = _make_sitemap_factory(sections["sitemap"])
            n += 1
        elif "listing" in sections:
            INSTANCE_FACTORIES[bank_code] = _make_listing_factory(sections["listing"])
            n += 1
    return n
