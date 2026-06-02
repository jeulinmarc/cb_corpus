"""Orchestration: discover -> (download) -> manifest, then completeness."""
from __future__ import annotations

from datetime import date
from typing import Iterable, Iterator, Optional

from .adapters.base import get_adapter
from .banks import BIS_63
from .config import Config
from .http import Fetcher
from .models import DocRecord
from .sources.bis_speeches import BISSpeechIndex
from .storage import Storage
from .taxonomy import DocType, FULL_SCOPE


def run(bank_codes: Optional[Iterable[str]] = None,
        scope: tuple[DocType, ...] = FULL_SCOPE,
        since: Optional[date] = None,
        dry_run: bool = True,
        config: Optional[Config] = None) -> dict[str, dict[str, int]]:
    """Crawl + (optionally) download. dry_run=True only indexes URLs.

    Returns {bank_code: {status: count}}.
    """
    cfg = config or Config()
    fetcher = Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    codes = list(bank_codes) if bank_codes else [b.code for b in BIS_63]
    results: dict[str, dict[str, int]] = {}
    for code in codes:
        adapter = get_adapter(code, fetcher)
        recs = adapter.discover_all(scope=scope, since=since)
        results[code] = storage.save_many(recs, dry_run=dry_run, label=code)
    return results


def run_bis_sitemap(since: Optional[date] = None,
                    until: Optional[date] = None,
                    only_banks: Optional[set[str]] = None,
                    dry_run: bool = True,
                    config: Optional[Config] = None,
                    max_per_year: Optional[int] = None) -> dict[str, int]:
    """Discover C1 speeches from BIS yearly sitemaps and (optionally) download.

    Single-pass across all 63 banks at once — far faster than per-bank C1
    discovery because we only walk each yearly sitemap once. Returns a global
    {status: count} dict.
    """
    cfg = config or Config()
    fetcher = Fetcher(cfg)
    storage = Storage(cfg, fetcher)
    bis = BISSpeechIndex(fetcher)
    recs: Iterator[DocRecord] = bis.discover(
        since=since, until=until, only_banks=only_banks,
        max_per_year=max_per_year,
        skip_url=storage.is_known_url,
    )
    return storage.save_many(recs, dry_run=dry_run, label="bis-sitemap")
