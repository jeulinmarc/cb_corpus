"""Runtime configuration."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class Config:
    data_dir: Path = Path("./data")
    user_agent: str = "cb-corpus/0.2 (+jeulinmarc@gmail.com)"
    # Minimal anti-ban throttle per host (NOT politeness — kept low on purpose).
    min_delay_seconds: float = 0.5
    timeout: float = 30.0          # per-request connect/read (inactivity) timeout
    download_timeout: float = 90.0  # TOTAL deadline for a body download (defeats
                                    # slow-trickle hosts that never trip `timeout`)
    max_retries: int = 3
    # No robots.txt enforcement. No domain guard. We scrape whatever the
    # discovery layer hands us.
    prefer_pdf: bool = True
    accept_html_when_no_pdf: bool = True
    parallel_hosts: int = 10
    # When True, text/html responses are rendered to PDF via headless Chrome
    # so the on-disk corpus stays uniformly PDF for downstream ingestion.
    html_to_pdf: bool = True

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def manifest_path(self) -> Path:
        return self.data_dir / "manifest.jsonl"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"
