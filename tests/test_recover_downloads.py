"""Tests for `recover-downloads` — inventory-driven Wayback recovery
(cb_corpus/recover.py) and the `latest_capture` CDX helper it relies on
(cb_corpus/sources/wayback.py).

The inventory (`data/download_errors.jsonl`) and the Wayback CDX network are
both faked with tiny stub fetchers — no real HTTP in this suite. Fixtures use
adversarial shapes on purpose: a duplicated pdf_url (dedup-by-latest), an
entry converged only by source_url, a bank filter, a missing inventory file,
a source page fetch that raises, and UTF-8 titles.
"""
from __future__ import annotations

import csv
import json
from datetime import date

from cb_corpus.config import Config
from cb_corpus.storage import Storage, iter_manifest_rows


# ---------------------------------------------------------------------------
# latest_capture (sources/wayback.py)
# ---------------------------------------------------------------------------

class _CdxFetcher:
    """get_text -> canned CDX JSON; records every query for shape assertions."""

    def __init__(self, rows):
        self.rows = rows
        self.queries: list[str] = []

    def get_text(self, url):
        self.queries.append(url)
        return json.dumps(self.rows)


def test_latest_capture_parses_and_uses_exact_url_no_prefix():
    from cb_corpus.sources.wayback import latest_capture

    f = _CdxFetcher([["timestamp"], ["20230515000000"]])
    ts = latest_capture(f, "https://www.banque-france.fr/dt986.pdf")
    assert ts == "20230515000000"
    assert len(f.queries) == 1
    q = f.queries[0]
    assert "matchType=prefix" not in q
    assert "url=https://www.banque-france.fr/dt986.pdf" in q
    assert "mimetype:application/pdf" in q


def test_latest_capture_empty_cdx_is_none():
    from cb_corpus.sources.wayback import latest_capture

    f = _CdxFetcher([["timestamp"]])
    assert latest_capture(f, "https://x.example/none.pdf") is None


def test_latest_capture_custom_mimetype():
    from cb_corpus.sources.wayback import latest_capture

    f = _CdxFetcher([["timestamp"], ["20200101000000"]])
    latest_capture(f, "https://x.example/page.html", mimetype="text/html")
    assert "mimetype:text/html" in f.queries[0]


def test_wayback_for_url_still_works_after_refactor():
    """wayback_for_url now delegates to latest_capture -- keep its contract."""
    from cb_corpus.sources.wayback import wayback_for_url

    f = _CdxFetcher([["timestamp"], ["20170811213710"]])
    assert wayback_for_url(f, "http://riksbank.com/upload/993/x.pdf") == (
        "https://web.archive.org/web/20170811213710id_/"
        "http://riksbank.com/upload/993/x.pdf")
    assert wayback_for_url(_CdxFetcher([["timestamp"]]), "http://x/none.pdf") is None


# ---------------------------------------------------------------------------
# run_recover_downloads (cb_corpus/recover.py)
# ---------------------------------------------------------------------------

class _StubFetcher:
    """Configurable fetcher for the full recover-downloads flow.

    - `cdx_hits`: {url_substring: timestamp}. Any CDX query whose target url
      contains one of these substrings returns that timestamp; otherwise the
      CDX query returns an empty resultset.
    - `pages`: {url_substring: html} for non-CDX get_text calls (IDEAS pages).
    - `bytes_ok` / `bytes_fail`: substrings of urls that succeed / raise on
      get_bytes.
    """

    def __init__(self, cdx_hits=None, pages=None, bytes_ok=None, bytes_fail=None):
        self.cdx_hits = cdx_hits or {}
        self.pages = pages or {}
        self.bytes_ok = bytes_ok or {}
        self.bytes_fail = bytes_fail or set()
        self.get_bytes_calls: list[str] = []

    def get_text(self, url):
        if "/cdx/search/cdx" in url:
            for sub, ts in self.cdx_hits.items():
                if sub in url:
                    return json.dumps([["timestamp"], [ts]])
            return json.dumps([["timestamp"]])
        for sub, html in self.pages.items():
            if sub in url:
                return html
        raise RuntimeError(f"fake 404 (get_text): {url}")

    def get_bytes(self, url):
        self.get_bytes_calls.append(url)
        # bytes_ok is checked first: a snapshot URL embeds the original (dead)
        # bank URL as a substring, so a broad bytes_fail entry for the bank
        # host must not shadow the (more specific) archive.org success match.
        for sub, payload in self.bytes_ok.items():
            if sub in url:
                return payload
        for sub in self.bytes_fail:
            if sub in url:
                raise RuntimeError(f"blocked: {url}")
        raise RuntimeError(f"fake 404 (get_bytes): {url}")

    def throttle(self, url):
        pass


def _write_inventory(cfg: Config, entries: list[dict]) -> None:
    path = cfg.data_dir / "download_errors.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as fh:
        for e in entries:
            fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def _entry(bank="fr", pdf_url="https://www.banque-france.fr/dt986.pdf",
          title="Document de travail 986", source_url="", doc_type="D1",
          alt_urls=None, ts="2026-01-01T00:00:00+00:00"):
    return {
        "ts": ts, "label": f"{bank}:{doc_type}", "bank_code": bank,
        "doc_type": doc_type, "title": title, "pdf_url": pdf_url,
        "alt_urls": alt_urls or [], "source_url": source_url,
        "error": "HTTPError: 403",
    }


def _csv_rows(path):
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


def test_missing_inventory_file_is_a_no_crash_empty_run(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    results = run_recover_downloads(config=cfg,
                                    fetcher=_StubFetcher(), csv_path=str(tmp_path / "r.csv"))
    assert results == {}
    # CSV is still written (empty body, header only) -- never crashes.
    rows = _csv_rows(tmp_path / "r.csv")
    assert rows == []


def test_dedup_keeps_latest_entry_by_pdf_url(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [
        _entry(title="Ancien titre (obsolète)", ts="2026-01-01T00:00:00+00:00"),
        _entry(title="Théorie de l'inflation — note récente",
              ts="2026-02-01T00:00:00+00:00"),
    ])
    fetcher = _StubFetcher()  # no CDX hit -> unrecoverable, but dedup is what we assert
    csv_path = tmp_path / "r.csv"
    run_recover_downloads(config=cfg, fetcher=fetcher, csv_path=str(csv_path))
    rows = _csv_rows(csv_path)
    assert len(rows) == 1  # one pdf_url, not two
    assert rows[0]["title"] == "Théorie de l'inflation — note récente"


def test_banks_filter_restricts_inventory(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [
        _entry(bank="fr", pdf_url="https://www.banque-france.fr/a.pdf"),
        _entry(bank="de", pdf_url="https://www.bundesbank.de/b.pdf"),
    ])
    results = run_recover_downloads(bank_codes=["fr"], config=cfg,
                                    fetcher=_StubFetcher(), csv_path=str(tmp_path / "r.csv"))
    assert "fr" in results
    assert "de" not in results
    rows = _csv_rows(tmp_path / "r.csv")
    assert {r["bank"] for r in rows} == {"fr"}


def test_converged_via_known_pdf_url_skips_network(tmp_path):
    from cb_corpus.recover import run_recover_downloads
    from cb_corpus.models import DocRecord
    from cb_corpus.taxonomy import DocType

    cfg = Config(data_dir=tmp_path)
    url = "https://www.banque-france.fr/already-have.pdf"
    storage = Storage(cfg, _StubFetcher(bytes_ok={"already-have": (b"%PDF-1.4 x", "application/pdf")}))
    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="Already here",
                    pdf_url=url, date=date(2020, 1, 1), mime_type="application/pdf")
    storage.save(rec)

    _write_inventory(cfg, [_entry(bank="fr", pdf_url=url)])
    results = run_recover_downloads(config=cfg, fetcher=_StubFetcher(),
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["converged"] == 1
    assert results["fr"]["recoverable"] == 0
    rows = _csv_rows(tmp_path / "r.csv")
    assert rows[0]["action"] == "converged"
    assert rows[0]["snapshot_ts"] == ""


def test_converged_via_known_source_url(tmp_path):
    from cb_corpus.recover import run_recover_downloads
    from cb_corpus.models import DocRecord
    from cb_corpus.taxonomy import DocType

    cfg = Config(data_dir=tmp_path)
    source = "https://ideas.repec.org/p/bfr/banfra/981.html"
    storage = Storage(cfg, _StubFetcher(bytes_ok={"x.pdf": (b"%PDF-1.4 x", "application/pdf")}))
    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="Native copy",
                    pdf_url="https://www.banque-france.fr/native-x.pdf",
                    source_url=source, date=date(2020, 1, 1), mime_type="application/pdf")
    storage.save(rec)

    # Same source_url, but a DIFFERENT (dead) pdf_url in the audit entry --
    # the reconciliation already happened, this download attempt is stale.
    _write_inventory(cfg, [_entry(bank="fr",
                                  pdf_url="https://www.banque-france.fr/dead-x.pdf",
                                  source_url=source)])
    results = run_recover_downloads(config=cfg, fetcher=_StubFetcher(),
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["converged"] == 1
    assert results["fr"]["recoverable"] == 0


def test_recoverable_dry_run_never_calls_get_bytes(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    url = "https://www.banque-france.fr/dt986.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=url)])
    fetcher = _StubFetcher(cdx_hits={"dt986.pdf": "20250207000000"})
    results = run_recover_downloads(config=cfg, fetcher=fetcher,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recoverable"] == 1
    assert results["fr"]["recovered"] == 0    # dry-run: never counts as recovered
    assert fetcher.get_bytes_calls == []       # spy: nothing downloaded
    rows = _csv_rows(tmp_path / "r.csv")
    assert rows[0]["action"] == "recoverable"
    assert rows[0]["snapshot_ts"] == "20250207000000"
    assert rows[0]["pdf_url"] == url


def test_unrecoverable_when_no_snapshot_anywhere(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [_entry(bank="fr",
                                  pdf_url="https://www.banque-france.fr/gone.pdf",
                                  alt_urls=["https://econstor.eu/also-gone.pdf"])])
    results = run_recover_downloads(config=cfg, fetcher=_StubFetcher(),
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["unrecoverable"] == 1
    rows = _csv_rows(tmp_path / "r.csv")
    assert rows[0]["action"] == "unrecoverable"
    assert rows[0]["snapshot_ts"] == ""


def test_csv_columns_shape(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [_entry()])
    run_recover_downloads(config=cfg, fetcher=_StubFetcher(), csv_path=str(tmp_path / "r.csv"))
    with open(tmp_path / "r.csv", newline="") as fh:
        header = next(csv.reader(fh))
    assert header == ["bank", "pdf_url", "action", "snapshot_ts", "title"]


def test_counts_dict_has_all_four_action_keys(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [_entry()])
    results = run_recover_downloads(config=cfg, fetcher=_StubFetcher(),
                                    csv_path=str(tmp_path / "r.csv"))
    assert set(results["fr"]) == {"recoverable", "recovered", "unrecoverable", "converged"}


# --- metadata refresh from the IDEAS source page --------------------------

_IDEAS_PAGE = """
<html><head>
<meta name="citation_title" content="Théorie de l'inflation importée">
<meta name="citation_publication_date" content="2025/02">
</head><body>
<input name="url" value="https://www.banque-france.fr/dt986.pdf">
<a href="https://econstor.eu/bitstream/dt986.pdf">alt</a>
</body></html>
"""


def test_metadata_refresh_from_ideas_page_sets_month_precision_repec(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    ideas_url = "https://ideas.repec.org/p/bfr/banfra/981.html"
    pdf_url = "https://www.banque-france.fr/dt986.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=pdf_url, source_url=ideas_url,
                                  title="stale audit title")])
    fetcher = _StubFetcher(
        pages={ideas_url: _IDEAS_PAGE},
        cdx_hits={"dt986.pdf": "20250207000000"},
        bytes_ok={"dt986.pdf": (b"%PDF-1.4 official", "application/pdf"),
                 "web.archive.org/web/20250207000000id_": (b"%PDF-1.4 snap", "application/pdf")},
    )
    run_recover_downloads(bank_codes=["fr"], download=True, config=cfg, fetcher=fetcher,
                          csv_path=str(tmp_path / "r.csv"))
    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Théorie de l'inflation importée"
    assert row["date"] == "2025-02-01"
    assert row["date_precision"] == "month"
    assert row["date_source"] == "repec"
    assert row["provenance"] == "wayback"
    assert row["pdf_url"] == pdf_url                       # official URL preserved
    assert row["alt_urls"][0].startswith(
        "https://web.archive.org/web/20250207000000id_/")   # raw snapshot FIRST
    assert row["alt_urls"][0].endswith(pdf_url)


def test_metadata_refresh_falls_back_to_audit_fields_on_fetch_failure(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    ideas_url = "https://ideas.repec.org/p/bfr/banfra/999.html"
    pdf_url = "https://www.banque-france.fr/dt999.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=pdf_url, source_url=ideas_url,
                                  title="Titre venant de l'audit")])
    # No `pages` entry for ideas_url -> get_text raises -> refresh fails.
    fetcher = _StubFetcher(
        cdx_hits={"dt999.pdf": "20250101000000"},
        bytes_ok={"dt999.pdf": (b"%PDF-1.4 official", "application/pdf"),
                 "web.archive.org/web/20250101000000id_": (b"%PDF-1.4 snap", "application/pdf")},
    )
    run_recover_downloads(bank_codes=["fr"], download=True, config=cfg, fetcher=fetcher,
                          csv_path=str(tmp_path / "r.csv"))
    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["title"] == "Titre venant de l'audit"   # fallback to the audit line
    assert row["date"] is None                          # undated -- mirrors WaybackSource
    assert row["date_precision"] == "day"               # DocRecord default, left untouched
    assert row["date_source"] == "bank_site"            # DocRecord default, left untouched


# --- --download: fallback chain + recovered accounting ---------------------

def test_download_saves_via_snapshot_fallback_when_official_url_blocked(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    pdf_url = "https://www.banque-france.fr/wp1002.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=pdf_url, title="WP 1002 UTF-8 éè")])
    fetcher = _StubFetcher(
        cdx_hits={"wp1002.pdf": "20240601000000"},
        bytes_fail={"www.banque-france.fr"},    # official host still 403s
        bytes_ok={"web.archive.org/web/20240601000000id_": (b"%PDF-1.4 snap", "application/pdf")},
    )
    results = run_recover_downloads(bank_codes=["fr"], download=True, config=cfg,
                                    fetcher=fetcher, csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recovered"] == 1
    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["pdf_url"] == pdf_url
    assert row["provenance"] == "wayback"
    assert row["local_path"] and row["sha256"]
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "recovered"


def test_download_all_candidates_fail_stays_recoverable_and_reaudits(tmp_path):
    """A snapshot exists (CDX hit -> recoverable) but BOTH the official URL and
    the archive.org snapshot bytes fail -- e.g. the snapshot itself is a dead
    capture. `save()` raises; the failure must be re-audited into
    download_errors.jsonl (label "recover-downloads") and the entry must
    NEVER be counted/recorded as recovered -- the CSV row stays
    'recoverable', not 'recovered'."""
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    pdf_url = "https://www.banque-france.fr/wp1003.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=pdf_url, title="WP 1003")])
    fetcher = _StubFetcher(
        cdx_hits={"wp1003.pdf": "20240601000000"},
        bytes_fail={"www.banque-france.fr"},    # official host still 403s
        # no bytes_ok entry at all (dropped from the fallback test's setup) --
        # the archive.org snapshot has no configured success either, so it
        # falls through to the stub's generic 404 -- no candidate URL can
        # ever succeed.
    )
    results = run_recover_downloads(bank_codes=["fr"], download=True, config=cfg,
                                    fetcher=fetcher, csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recoverable"] == 1
    assert results["fr"]["recovered"] == 0
    rows = list(iter_manifest_rows(cfg, "fr"))
    assert rows == []   # nothing saved to the manifest
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "recoverable"   # never 'recovered'

    errors_path = cfg.data_dir / "download_errors.jsonl"
    error_lines = [json.loads(l) for l in errors_path.read_text().splitlines() if l.strip()]
    reaudited = [e for e in error_lines if e.get("label") == "recover-downloads"]
    assert len(reaudited) == 1
    assert reaudited[0]["pdf_url"] == pdf_url


def test_download_missing_doc_type_code_is_skipped_gracefully(tmp_path):
    """An audit entry with an unrecognised doc_type must not crash the run --
    it stays 'recoverable' (not 'recovered'), never breaking the whole pass."""
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    pdf_url = "https://www.banque-france.fr/wp1.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=pdf_url, doc_type="ZZ")])
    fetcher = _StubFetcher(cdx_hits={"wp1.pdf": "20240101000000"})
    results = run_recover_downloads(bank_codes=["fr"], download=True, config=cfg,
                                    fetcher=fetcher, csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recoverable"] == 1
    assert results["fr"]["recovered"] == 0
