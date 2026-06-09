"""Orchestration: discover -> (download) -> manifest, then completeness."""
from __future__ import annotations

import json
import sys
from datetime import date
from typing import Iterable, Iterator, Optional

from .adapters.base import get_adapter
from .banks import BIS_63
from .config import Config
from .http import Fetcher
from .models import DocRecord
from .sources.bis_speeches import BISSpeechIndex
from .sources.recovery import Source
from .storage import Storage
from .taxonomy import DocType, FULL_SCOPE


def _make_storage(config: Optional[Config] = None,
                  html_to_pdf: Optional[bool] = None) -> tuple[Config, Fetcher, Storage]:
    """Build the (cfg, fetcher, storage) triplet every entry point needs.

    `html_to_pdf` overrides the config flag (e.g. False to keep HTML-only without
    spawning Chrome). Centralises what used to be ~9 copy-pasted blocks.
    """
    cfg = config or Config()
    if html_to_pdf is not None:
        cfg.html_to_pdf = html_to_pdf
    fetcher = Fetcher(cfg)
    return cfg, fetcher, Storage(cfg, fetcher)


def run_source(source: Source, *, dry_run: bool = False,
               config: Optional[Config] = None) -> dict[str, int]:
    """Drive a `Source`: make storage, enumerate its items, save them.

    The single uniform recovery entry point — a new official source only needs to
    implement `Source.items`, not re-derive the Storage/dedup/save plumbing.
    """
    cfg, fetcher, storage = _make_storage(config, source.html_to_pdf)
    return storage.save_many(source.items(fetcher, storage),
                             dry_run=dry_run, label=source.label)


def _record_discovery_errors(cfg: Config, errors: list[dict]) -> None:
    """Append discovery failures to data/discovery_errors.jsonl (audit trail)."""
    if not errors:
        return
    path = cfg.data_dir / "discovery_errors.jsonl"
    with path.open("a") as fh:
        for e in errors:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def run(bank_codes: Optional[Iterable[str]] = None,
        scope: tuple[DocType, ...] = FULL_SCOPE,
        since: Optional[date] = None,
        dry_run: bool = True,
        config: Optional[Config] = None,
        max_rounds: int = 1) -> dict[str, dict[str, int]]:
    """Crawl + (optionally) download. dry_run=True only indexes URLs.

    With ``max_rounds > 1`` the crawl repeats until a round downloads nothing
    new AND reports no errors (download + discovery), or the cap is reached.
    Because saving is idempotent (dedup on doc_id + sha256), re-running only
    fills gaps left by transient failures — this is what makes a full rebuild
    converge to completeness instead of silently stopping one blip short.

    Returns {bank_code: {status: count}} for the last round. Any unresolved
    discovery failures are written to ``data/discovery_errors.jsonl``.
    """
    cfg, fetcher, storage = _make_storage(config)
    codes = list(bank_codes) if bank_codes else [b.code for b in BIS_63]
    results: dict[str, dict[str, int]] = {}
    rounds = 1 if dry_run else max(1, max_rounds)

    round_no = 0
    last_disc_errors: list[dict] = []
    for round_no in range(1, rounds + 1):
        round_saved = round_errors = 0
        last_disc_errors = []
        for code in codes:
            adapter = get_adapter(code, fetcher)
            recs = adapter.discover_all(scope=scope, since=since)
            counts = storage.save_many(recs, dry_run=dry_run, label=code)
            results[code] = counts
            round_saved += counts.get("saved", 0)
            round_errors += counts.get("error", 0)
            last_disc_errors.extend(adapter.errors)
        _record_discovery_errors(cfg, last_disc_errors)
        if rounds > 1:
            print(f"[round {round_no}/{rounds}] saved={round_saved} "
                  f"errors={round_errors} discovery_failures={len(last_disc_errors)}",
                  file=sys.stderr, flush=True)
        if dry_run:
            break
        if round_saved == 0 and round_errors == 0 and not last_disc_errors:
            break

    if last_disc_errors or (not dry_run and round_errors):
        print(f"!! INCOMPLETE after {round_no} round(s): "
              f"{len(last_disc_errors)} discovery failure(s) remain — see "
              f"{cfg.data_dir / 'discovery_errors.jsonl'}. Re-run to fill.",
              file=sys.stderr, flush=True)
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
    cfg, fetcher, storage = _make_storage(config)
    bis = BISSpeechIndex(fetcher)
    recs: Iterator[DocRecord] = bis.discover(
        since=since, until=until, only_banks=only_banks,
        max_per_year=max_per_year,
        skip_url=storage.is_known_url,
    )
    return storage.save_many(recs, dry_run=dry_run, label="bis-sitemap")


def run_repec(bank_codes: Optional[Iterable[str]] = None,
              dry_run: bool = True,
              config: Optional[Config] = None) -> dict[str, dict[str, int]]:
    """Discover + (optionally) download RePEc working papers (D1/D2) for every
    SERIES-wired bank, following IDEAS pagination so the full back-catalogue is
    captured (not just the ~200 newest per series).

    One pass per bank, idempotent (dedup on doc_id + sha256), so re-running only
    fills gaps. Returns {bank_code: {status: count}}.
    """
    from .sources.repec import RePEcDiscovery, SERIES
    cfg, fetcher, storage = _make_storage(config)
    rep = RePEcDiscovery(fetcher)
    codes = list(bank_codes) if bank_codes else list(SERIES.keys())
    results: dict[str, dict[str, int]] = {}
    for code in codes:
        if code not in SERIES:
            continue
        results[code] = storage.save_many(
            rep.discover_bank(code), dry_run=dry_run, label=f"repec:{code}")
    return results


def run_wayback_recovery(bank_code: str, url_prefix: str, doc_type: DocType,
                         title_fn=None, dry_run: bool = True,
                         config: Optional[Config] = None) -> dict[str, int]:
    """Recover official PDFs the bank took offline, from the Wayback Machine.

    Lists archived PDFs under `url_prefix` (CDX), and saves one record per paper
    with `pdf_url` = the original (dead) bank URL [for citation + stable doc_id]
    and the Wayback raw-snapshot URL as the fallback Storage actually downloads.
    Date is the year embedded in the URL; `provenance="wayback"`.
    """
    from .sources.wayback import WaybackSource
    return run_source(WaybackSource(bank_code, url_prefix, doc_type, title_fn),
                      dry_run=dry_run, config=config)


def run_repec_wayback_recovery(bank_code: str, dry_run: bool = False,
                               config: Optional[Config] = None) -> dict[str, int]:
    """Recover RePEc working papers whose official PDF is dead, via the Wayback
    Machine, keying on each paper's EXACT url (for opaque-path sources like the
    Riksbank). Re-discovers the bank, skips papers already saved, and for the
    missing ones adds the archived snapshot as a fallback Storage downloads.
    Dates/titles come from IDEAS; provenance is set to "wayback".
    """
    from .sources.repec import RePEcDiscovery
    from .sources.wayback import wayback_for_url
    cfg, fetcher, storage = _make_storage(config)
    rep = RePEcDiscovery(fetcher)

    def _recs() -> Iterator[DocRecord]:
        for rec in rep.discover_bank(bank_code):
            # Skip by URL (not doc_id): already-saved papers — incl. ones recovered
            # earlier with a different date precision — share the same pdf_url, so
            # this avoids both re-pinging Wayback and creating date-mismatch dupes.
            if storage.is_known_url(rec.pdf_url) or rec.doc_id in storage._ids:
                continue
            wb = wayback_for_url(fetcher, rec.pdf_url)
            if wb:
                rec.alt_urls = list(rec.alt_urls) + [wb]
                rec.provenance = "wayback"
            yield rec

    return storage.save_many(_recs(), dry_run=dry_run, label=f"repec-wb:{bank_code}")


def run_boe_wp_recovery(years=None, dry_run: bool = True,
                        config: Optional[Config] = None) -> dict[str, int]:
    """Fetch Bank of England staff working papers (D1) from the BoE's own sitemap
    — the live official source, covering papers IDEAS only has dead URLs for.
    Skips papers already in the manifest (by URL)."""
    from .sources.boe_wp import sitemap_pages, paper_pdf
    cfg, fetcher, storage = _make_storage(config)

    def _recs() -> Iterator[DocRecord]:
        for d, page, derived in sitemap_pages(fetcher, years):
            if storage.is_known_url(derived):
                continue                       # already have it (= the IDEAS media URL)
            got = paper_pdf(fetcher, page)     # read the page for the REAL pdf link
            if got is None:
                continue
            title, pdf = got
            if storage.is_known_url(pdf):
                continue
            yield DocRecord(
                bank_code="gb", doc_type=DocType.D1, title=title,
                pdf_url=pdf, date=d, provenance="bank_site",
                mime_type="application/pdf",
            )

    return storage.save_many(_recs(), dry_run=dry_run, label="boe-wp")


def run_boe_recovery(sitemap_path: str, doc_type: DocType, href_filter: str,
                     years=None, dry_run: bool = True,
                     config: Optional[Config] = None) -> dict[str, int]:
    """Generic Bank of England recovery from a sitemap section (the live official
    source). Works for any doc type — minutes (A3), MP report (E1), FSR (E2)…
    A page's linked PDF is taken when present, else the HTML page is the artifact
    (rendered to PDF by Storage). Skips docs already in the manifest (by URL)."""
    from .sources.boe_wp import doc_pages, page_doc
    cfg, fetcher, storage = _make_storage(config)

    def _recs() -> Iterator[DocRecord]:
        for d, page in doc_pages(fetcher, sitemap_path, href_filter, years):
            got = page_doc(fetcher, page)
            if got is None:
                continue
            title, pdf = got
            url = pdf or page              # PDF if linked, else the HTML page
            if storage.is_known_url(url):
                continue
            yield DocRecord(
                bank_code="gb", doc_type=doc_type, title=title,
                pdf_url=url, source_url=page, date=d, provenance="bank_site",
                mime_type="application/pdf" if pdf else "",
            )

    return storage.save_many(_recs(), dry_run=dry_run, label=f"boe:{doc_type.code}")


def run_listing_pdf_recovery(bank_code: str, doc_type: DocType, listing_urls,
                             path_regex: str, title: str, url_template=None,
                             mime: str = "application/pdf", dry_run: bool = True,
                             config: Optional[Config] = None) -> dict[str, int]:
    """Generic recovery: scrape listing page(s) and save matching PDFs.

    Two modes:
      - default: `path_regex` group 1 = full PDF path, group 2 = YYYYMMDD.
      - `url_template` set: `path_regex` group 1 = YYYYMMDD, and the PDF URL is
        `url_template.format(d=<date8>)` (for date-constructable archives the page
        only lists via JS / a landing page, e.g. press-conference transcripts).
    Matches the RAW HTML (not just <a href>); skips PDFs already in the manifest."""
    import re
    from datetime import date as _date
    from urllib.parse import urljoin
    rx = re.compile(path_regex, re.I)
    cfg, fetcher, storage = _make_storage(config)

    def _recs() -> Iterator[DocRecord]:
        seen: set[str] = set()
        for lu in listing_urls:
            try:
                html = fetcher.get_text(lu)
            except Exception:
                continue
            html = html.replace("\\/", "/")    # unescape JSON-embedded URLs (JS archives)
            for m in rx.finditer(html):
                ds = m.group(1) if url_template else m.group(2)
                try:
                    d = _date(int(ds[:4]), int(ds[4:6]), int(ds[6:8]))
                except (ValueError, IndexError):
                    continue
                url = urljoin(lu, url_template.format(d=ds) if url_template else m.group(1))
                if url in seen or storage.is_known_url(url):
                    continue
                seen.add(url)
                yield DocRecord(
                    bank_code=bank_code, doc_type=doc_type,
                    title=f"{title} {d.isoformat()}", pdf_url=url, date=d,
                    provenance="bank_site", mime_type=mime,
                )

    return storage.save_many(_recs(), dry_run=dry_run, label=f"{bank_code}:{doc_type.code}")


def run_ecb_pub_recovery(section: str, doc_type: DocType, mime: str = "application/pdf",
                         since_year: int = 1997, cdx_fallback_prefix: Optional[str] = None,
                         date_fmt: str = "auto", name_filter: Optional[str] = None,
                         dry_run: bool = False,
                         config: Optional[Config] = None) -> dict[str, int]:
    """Recover an ECB publication section.

    PRIMARY: the bank's per-section/year static includes
    (`/press/<section>/date/<year>/html/index_include.en.html`) — canonical, no JS.
    FALLBACK (kept on purpose for resilience): Wayback CDX enumeration under
    `cdx_fallback_prefix`, used only when the section serves no includes.
    `mime=""` saves HTML as-is (no Chrome render), e.g. for interviews.
    """
    import re
    from .sources.ecb_pub import section_include_docs, date_from_url, ECB
    from .sources.wayback import cdx_pdfs, raw_url
    cfg, fetcher, storage = _make_storage(config, html_to_pdf=(False if not mime else None))
    cur = date.today().year
    exts = (".en.pdf",) if mime else (".en.html", ".en.pdf")

    def _emit(url, d, alt=None):
        return DocRecord(bank_code="ecb", doc_type=doc_type,
                         title=f"ECB {doc_type.name} {d.isoformat()}",
                         pdf_url=url, alt_urls=alt or [], date=d,
                         provenance="bank_site", mime_type=mime)

    def _recs() -> Iterator[DocRecord]:
        seen: set[str] = set()
        # Probe a recent year: does this section serve per-year includes at all?
        # (Avoids hammering ~30 missing-include URLs before falling back.)
        serves_includes = any(section_include_docs(fetcher, section, y, exts=exts) is not None
                              for y in (cur, cur - 1))
        if serves_includes:                                   # PRIMARY
            for year in range(since_year, cur + 1):
                for u in section_include_docs(fetcher, section, year, exts=exts) or []:
                    u = u.split("?")[0]
                    if u in seen or storage.is_known_url(u):
                        continue
                    seen.add(u)
                    d = date_from_url(u, date_fmt)
                    if d:
                        yield _emit(u, d)
        elif cdx_fallback_prefix:                             # FALLBACK: legacy CDX method
            nf = re.compile(name_filter, re.I) if name_filter else None
            cdx_mime = "application/pdf" if mime else "text/html"
            en_ext = "en.pdf" if mime else "en.html"
            for o, t in cdx_pdfs(fetcher, cdx_fallback_prefix, mimetype=cdx_mime):
                o = o.split("?")[0]
                fn = o.rsplit("/", 1)[-1]
                if not fn.lower().endswith(en_ext):            # English-language docs only
                    continue
                if nf and not nf.search(fn):
                    continue
                if o in seen or storage.is_known_url(o):
                    continue
                seen.add(o)
                d = date_from_url(o, date_fmt)
                if d:
                    yield _emit(o, d, alt=[raw_url(o, t)])

    return storage.save_many(_recs(), dry_run=dry_run, label=f"ecb:{doc_type.code}")
