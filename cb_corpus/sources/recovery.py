"""Uniform recovery-source abstraction.

Every gap-recovery this corpus needed (Wayback CDX, ECB per-section includes, BoE
sitemaps, constructed URLs, OAI-PMH harvests…) follows the same shape: enumerate
candidate documents from some official endpoint, skip the ones we already have,
yield `DocRecord`s. The pipeline driver (`run_source`) owns the boilerplate —
Storage creation, dedup, saving — so a new source is just an `items()` method.

This replaces the ~9 copies of `cfg/fetcher/storage` + `save_many` that the
hand-written `run_*_recovery` functions used to each carry.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterator, Optional

from ..models import DocRecord


class Source(ABC):
    """A recovery source. Subclass and implement `items`.

    - `label`: shown in progress output (e.g. "ecb:E2").
    - `html_to_pdf`: per-source override of Chrome rendering (None = config
      default; False = keep HTML only, the common case for HTML-native docs).
    """

    label: str = "source"
    html_to_pdf: Optional[bool] = None

    @abstractmethod
    def items(self, fetcher, storage) -> Iterator[DocRecord]:
        """Yield the `DocRecord`s to persist.

        Implementations should skip documents already in the corpus via
        `storage.is_known_url(url)` (or a doc-type-specific key) before yielding,
        so re-runs stay cheap and idempotent.
        """
        raise NotImplementedError
