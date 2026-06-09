"""Canonical record for one official document."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

from .taxonomy import DocType


@dataclass
class DocRecord:
    bank_code: str
    doc_type: DocType
    title: str
    pdf_url: str                       # canonical document URL (PDF preferred, HTML accepted)
    source_url: str = ""               # landing/listing page it was found on
    date: Optional[date] = None
    language: str = "en"
    provenance: str = "bank_site"      # bank_site | bis_index | repec_discovery
    mime_type: str = ""                # "application/pdf" | "text/html" | ""
    sha256: Optional[str] = None       # filled after download
    local_path: Optional[str] = None   # filled after download — the canonical artifact (PDF preferred)
    html_path: Optional[str] = None    # set when source was HTML — the original HTML sibling file
    # Runtime-only ordered fallback PDF URLs (e.g. EconStor/SSRN copies) tried by
    # Storage when the preferred `pdf_url` download fails (host 403 / dead link).
    # NOT serialized and NOT part of doc_id, so identity/dedup stay stable on the
    # preferred URL regardless of which copy actually downloaded.
    alt_urls: list[str] = field(default_factory=list)

    @property
    def year(self) -> Optional[int]:
        return self.date.year if self.date else None

    @property
    def doc_id(self) -> str:
        """Stable id used for filenames and dedup.

        Derived from IMMUTABLE identity only (bank, type, url) — deliberately NOT
        the date. Date is mutable metadata (it gets corrected/enriched); binding
        it into the id used to mean a date fix changed the id, creating a
        duplicate + a file rename. With the date out, enriching a date is a pure
        metadata update (no id churn). The url already uniquely identifies a doc.
        """
        basis = f"{self.bank_code}|{self.doc_type.code}|{self.pdf_url}"
        return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]

    def to_row(self) -> dict:
        d = asdict(self)
        d.pop("alt_urls", None)            # runtime-only, not persisted
        d["doc_type"] = self.doc_type.code
        d["date"] = self.date.isoformat() if self.date else None
        d["doc_id"] = self.doc_id
        d["year"] = self.year
        return d
