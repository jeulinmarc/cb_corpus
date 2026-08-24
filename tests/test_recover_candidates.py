"""Tests for `recover-downloads --candidates` (external-recovery injection)
and `--seed-quarantine` (cb_corpus/recover.py, cb_corpus/quarantine.py).

No network in this suite: `_refresh_metadata` is monkeypatched to a stub so
candidate rows never actually reach the fake IDEAS fetch. Fixtures use
adversarial shapes on purpose: a candidate whose dead_pdf_url matches nothing
in the audit trail, a too-small "PDF" (truncated/HTML-error-page-as-pdf), and
a candidate whose bytes hash-match a document already in the corpus.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from cb_corpus.config import Config
from cb_corpus.models import DocRecord
from cb_corpus.quarantine import Quarantine
from cb_corpus.storage import Storage, iter_manifest_rows
from cb_corpus.taxonomy import DocType


# ---------------------------------------------------------------------------
# fixtures / helpers
# ---------------------------------------------------------------------------

class _NullFetcher:
    """No candidate test in this suite should ever reach the network --
    `_refresh_metadata` is monkeypatched below. This fetcher raises if
    anything does try to use it, so a leak is caught loudly."""

    def get_text(self, url):
        raise AssertionError(f"unexpected network call: {url}")

    def get_bytes(self, url):
        raise AssertionError(f"unexpected network call: {url}")

    def throttle(self, url):
        pass


def _stub_refresh_metadata(monkeypatch, title="Refreshed Title", rec_date=None,
                           date_precision=None, date_source=None):
    """Monkeypatch `_refresh_metadata` (used by both the CDX-walk path and the
    --candidates path) to a deterministic, network-free stub."""
    import cb_corpus.recover as recover_mod

    def _fake(fetcher, entry):
        return (title, rec_date, date_precision, date_source, [])

    monkeypatch.setattr(recover_mod, "_refresh_metadata", _fake)


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


def _write_candidates(tmp_path, candidates: list[dict]) -> Path:
    path = tmp_path / "candidates.jsonl"
    with path.open("w") as fh:
        for c in candidates:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    return path


def _make_local_pdf(tmp_path, name="recovered.pdf", size=25 * 1024) -> Path:
    """A verified-shape local PDF: %PDF magic, bigger than the 20KB floor."""
    path = tmp_path / name
    body = b"%PDF-1.4 " + b"x" * size
    path.write_bytes(body)
    return path


def _csv_rows(path):
    import csv
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# ---------------------------------------------------------------------------
# provenance rules (spec decision 2)
# ---------------------------------------------------------------------------

def test_wayback_provenance_dead_url_stays_pdf_url(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/dt986.pdf"
    snapshot_url = "https://web.archive.org/web/20250101000000id_/https://www.banque-france.fr/dt986.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path, "wayback.pdf")
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": snapshot_url,
    }])
    _stub_refresh_metadata(monkeypatch, title="WP 986", rec_date=None,
                           date_precision=None, date_source=None)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recovered"] == 1

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["pdf_url"] == dead_url               # official (dead) URL preserved as identity
    assert row["alt_urls"] == [snapshot_url]         # snapshot as the fallback
    assert row["provenance"] == "wayback"
    assert row["date_source"] == "wayback"           # no repec date -> honestly wayback
    assert row["local_path"] and Path(row["local_path"]).is_file()

    # File landed exactly at Storage.target_path(rec).
    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="WP 986",
                    pdf_url=dead_url, mime_type="application/pdf")
    storage = Storage(cfg, _NullFetcher())
    assert Path(row["local_path"]) == storage.target_path(rec)

    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "recovered"


def test_bank_site_provenance_final_url_becomes_pdf_url(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/old-path/dt987.pdf"
    final_url = "https://www.banque-france.fr/new-path/dt987.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path, "banksite.pdf")
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "bank_site", "final_url": final_url,
    }])
    _stub_refresh_metadata(monkeypatch, title="WP 987")

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recovered"] == 1

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["pdf_url"] == final_url               # live URL points at reality
    assert row["alt_urls"] == [dead_url]              # dead URL kept for history
    assert row["provenance"] == "bank_site"
    assert row["date_source"] == "bank_site"          # DocRecord default, untouched

    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="WP 987",
                    pdf_url=final_url, mime_type="application/pdf")
    storage = Storage(cfg, _NullFetcher())
    assert Path(row["local_path"]) == storage.target_path(rec)


def test_mirror_provenance_dead_url_stays_pdf_url(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/dt988.pdf"
    mirror_url = "https://www.econstor.eu/bitstream/dt988.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path, "mirror.pdf")
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "mirror", "final_url": mirror_url,
    }])
    _stub_refresh_metadata(monkeypatch, title="WP 988")

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recovered"] == 1

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    row = rows[0]
    assert row["pdf_url"] == dead_url                 # official URL is the WP's identity
    assert row["alt_urls"] == [mirror_url]
    assert row["provenance"] == "mirror"

    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="WP 988",
                    pdf_url=dead_url, mime_type="application/pdf")
    storage = Storage(cfg, _NullFetcher())
    assert Path(row["local_path"]) == storage.target_path(rec)


# ---------------------------------------------------------------------------
# bad-file / unknown-entry
# ---------------------------------------------------------------------------

def test_too_small_file_is_bad_file_never_registered(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/truncated.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    tiny = tmp_path / "tiny.pdf"
    tiny.write_bytes(b"%PDF-1.4 too small")   # well under 20KB
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(tiny),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/x",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-file"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-file"


def test_missing_pdf_magic_is_bad_file(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/notreally.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    fake = tmp_path / "fake.pdf"
    fake.write_bytes(b"<html>404 not found</html>" + b"x" * (25 * 1024))  # big but no magic
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(fake),
        "recovered_from": "mirror", "final_url": "https://econstor.eu/x.pdf",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-file"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []


def test_unknown_dead_url_is_unknown_entry(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    _write_inventory(cfg, [_entry(bank="fr", pdf_url="https://www.banque-france.fr/known.pdf")])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": "https://www.banque-france.fr/never-in-inventory.pdf",
        "file_path": str(local_pdf), "recovered_from": "wayback",
        "final_url": "https://web.archive.org/x",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["_unknown"]["unknown-entry"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "unknown-entry"
    assert csv_rows[0]["pdf_url"] == "https://www.banque-france.fr/never-in-inventory.pdf"


# ---------------------------------------------------------------------------
# duplicate-content dedup + orphan-file removal
# ---------------------------------------------------------------------------

def test_duplicate_content_is_deduped_and_orphan_file_removed(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dup_bytes = b"%PDF-1.4 duplicate-body " + b"y" * (25 * 1024)

    # Pre-seed the corpus with this exact content under a different, live URL.
    seed_storage = Storage(cfg, _NullFetcher())
    seed_rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="Existing copy",
                        pdf_url="https://www.banque-france.fr/existing.pdf",
                        date=date(2020, 1, 1), mime_type="application/pdf")
    seed_path = cfg.data_dir / "seed-source.pdf"
    seed_path.write_bytes(dup_bytes)
    assert seed_storage.reindex(seed_rec, seed_path) == "reindexed"

    dead_url = "https://www.banque-france.fr/dt-alias.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url, title="Alias copy")])
    local_pdf = tmp_path / "alias.pdf"
    local_pdf.write_bytes(dup_bytes)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/alias",
    }])
    _stub_refresh_metadata(monkeypatch, title="Alias copy")

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["duplicate"] == 1
    assert results["fr"]["recovered"] == 0

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1   # only the pre-seeded row -- nothing new appended
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "duplicate"

    # No orphan bytes: the copy made into the corpus layout for the alias
    # doc_id must have been removed again since it was never indexed.
    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="Alias copy",
                    pdf_url=dead_url, mime_type="application/pdf")
    storage = Storage(cfg, _NullFetcher())
    assert not storage.target_path(rec).exists()


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/dryrun.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/dryrun",
    }])
    _stub_refresh_metadata(monkeypatch, title="Dry run WP")

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=False,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recoverable"] == 1
    assert results["fr"]["recovered"] == 0
    assert list(iter_manifest_rows(cfg, "fr")) == []

    rec = DocRecord(bank_code="fr", doc_type=DocType.D1, title="Dry run WP",
                    pdf_url=dead_url, mime_type="application/pdf")
    storage = Storage(cfg, _NullFetcher())
    assert not storage.target_path(rec).exists()

    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "recoverable"


# ---------------------------------------------------------------------------
# --seed-quarantine
# ---------------------------------------------------------------------------

def test_seed_quarantine_flag_applies_seed_before_the_pass(tmp_path):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    url = "https://www.banque-france.fr/unrecoverable.pdf"
    seed_path = tmp_path / "unrecoverable.jsonl"
    seed_path.write_text(json.dumps({"url": url, "reason": "hunt exhausted"}) + "\n")

    run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                          seed_quarantine=str(seed_path),
                          csv_path=str(tmp_path / "r.csv"))

    q = Quarantine(cfg)
    assert q.is_quarantined(url) is True
    lines = (cfg.data_dir / "download_quarantine.jsonl").read_text().splitlines()
    assert json.loads(lines[-1]) == {"url": url, "seeded": "hunt exhausted", "quarantined": True}


def test_apply_seed_quarantine_validates_whole_file_before_applying_any_seed(tmp_path):
    """A malformed line ANYWHERE in the seed file must abort the whole
    application with NO partial effect -- not seed the good lines that came
    before it and then blow up on the bad one."""
    from cb_corpus.recover import _apply_seed_quarantine

    cfg = Config(data_dir=tmp_path)
    q = Quarantine(cfg)
    good_url = "https://www.banque-france.fr/good.pdf"
    seed_path = tmp_path / "seed.jsonl"
    seed_path.write_text(
        json.dumps({"url": good_url, "reason": "ok"}) + "\n"
        '{"url": "https://bad.test/x.pdf", "reason": '  # malformed JSON (no closing)
    )

    with pytest.raises(Exception):
        _apply_seed_quarantine(q, str(seed_path))

    # No partial application: the good line above the bad one must not have
    # been seeded either.
    assert q.is_quarantined(good_url) is False


# ---------------------------------------------------------------------------
# CRITICAL — orphan unlink must never delete a canonical corpus file
# ---------------------------------------------------------------------------

def test_rerunning_same_candidates_file_twice_does_not_delete_corpus_file(tmp_path, monkeypatch):
    """The exact data-loss scenario: a --candidates pass is re-run (e.g. a
    retry) after the corpus already converged on these docs. dest ==
    storage.target_path(rec) for the SAME doc_id as the already-indexed row
    -- the second run must recognise that via a dry-run probe BEFORE copying
    anything, never copy2-then-unlink the real corpus file."""
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/rerun.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url, title="Rerun WP")])
    local_pdf = _make_local_pdf(tmp_path, "rerun.pdf")
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/rerun",
    }])
    _stub_refresh_metadata(monkeypatch, title="Rerun WP")

    results1 = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                     candidates=str(cand_path), download=True,
                                     csv_path=str(tmp_path / "r1.csv"))
    assert results1["fr"]["recovered"] == 1

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    local_path = Path(rows[0]["local_path"])
    assert local_path.is_file()

    # Re-run the exact same candidates file against the now-converged corpus.
    results2 = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                     candidates=str(cand_path), download=True,
                                     csv_path=str(tmp_path / "r2.csv"))
    assert results2["fr"]["duplicate"] == 1
    assert results2["fr"].get("recovered", 0) == 0

    # The real corpus file must still be present -- the exact bug this fix
    # prevents (copy2 -> reindex -> skip:already-indexed -> unlink(dest)).
    assert local_path.is_file()
    rows_after = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows_after) == 1

    csv_rows = _csv_rows(tmp_path / "r2.csv")
    assert csv_rows[0]["action"] == "duplicate"


def test_duplicated_candidate_line_within_one_run_is_deduped_without_deleting_file(tmp_path, monkeypatch):
    """Same scenario as above but within a SINGLE run: the same candidate
    line appears twice in the file (an operator mistake / a hand-hunted
    list built by concatenating sources)."""
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/dupline.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url, title="Dup line WP")])
    local_pdf = _make_local_pdf(tmp_path, "dupline.pdf")
    cand = {
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/dupline",
    }
    cand_path = _write_candidates(tmp_path, [cand, cand])
    _stub_refresh_metadata(monkeypatch, title="Dup line WP")

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recovered"] == 1
    assert results["fr"]["duplicate"] == 1

    rows = list(iter_manifest_rows(cfg, "fr"))
    assert len(rows) == 1
    assert Path(rows[0]["local_path"]).is_file()

    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert [r["action"] for r in csv_rows] == ["recovered", "duplicate"]


# ---------------------------------------------------------------------------
# IMPORTANT A — empty/missing final_url must be rejected before provenance
# ---------------------------------------------------------------------------

def test_wayback_empty_final_url_is_bad_candidate(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/nofinalurl-wb.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-candidate"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-candidate"


def test_bank_site_missing_final_url_key_is_bad_candidate(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/nofinalurl-bs.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "bank_site",   # no "final_url" key at all
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-candidate"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-candidate"


def test_mirror_empty_final_url_is_bad_candidate(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/nofinalurl-mirror.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "mirror", "final_url": "",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-candidate"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-candidate"


# ---------------------------------------------------------------------------
# MINOR 1 — copy2/reindex exceptions stay recoverable + audited, never crash
# ---------------------------------------------------------------------------

def test_copy_exception_stays_recoverable_and_is_audited(tmp_path, monkeypatch):
    from cb_corpus import recover as recover_mod
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/copyfail.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url, title="Copy fail WP")])
    local_pdf = _make_local_pdf(tmp_path, "copyfail.pdf")
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/copyfail",
    }])
    _stub_refresh_metadata(monkeypatch, title="Copy fail WP")

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(recover_mod.shutil, "copy2", _boom)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["recoverable"] == 1
    assert results["fr"].get("recovered", 0) == 0
    assert list(iter_manifest_rows(cfg, "fr")) == []

    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "recoverable"

    errors_path = cfg.data_dir / "download_errors.jsonl"
    error_lines = [json.loads(l) for l in errors_path.read_text().splitlines() if l.strip()]
    reaudited = [e for e in error_lines if e.get("label") == "recover-downloads-candidates"]
    assert len(reaudited) == 1
    assert reaudited[0]["pdf_url"] == dead_url


# ---------------------------------------------------------------------------
# MINOR 2 — _read_candidates tolerates a malformed JSON line
# ---------------------------------------------------------------------------

def test_read_candidates_skips_malformed_line_with_one_warning(tmp_path, capsys):
    from cb_corpus.recover import _read_candidates

    path = tmp_path / "candidates.jsonl"
    good1 = json.dumps({"dead_pdf_url": "https://x.test/a.pdf", "file_path": "a.pdf",
                        "recovered_from": "wayback", "final_url": "https://web.archive.org/a"})
    bad = '{"dead_pdf_url": "https://x.test/b.pdf", '  # malformed JSON
    good2 = json.dumps({"dead_pdf_url": "https://x.test/c.pdf", "file_path": "c.pdf",
                        "recovered_from": "wayback", "final_url": "https://web.archive.org/c"})
    path.write_text(good1 + "\n" + bad + "\n" + good2 + "\n")

    rows = _read_candidates(str(path))

    assert [r["dead_pdf_url"] for r in rows] == ["https://x.test/a.pdf", "https://x.test/c.pdf"]
    err = capsys.readouterr().err
    assert err.count("WARNING") == 1


# ---------------------------------------------------------------------------
# MINOR 4 — --banks filter + candidates: out-of-scope match is "filtered"
# ---------------------------------------------------------------------------

def test_out_of_scope_candidate_is_filtered_not_unknown_entry(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.bundesbank.de/oos.pdf"
    _write_inventory(cfg, [_entry(bank="de", pdf_url=dead_url, title="DE WP")])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/oos",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(bank_codes=["fr"], config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["de"]["filtered"] == 1
    assert results.get("_unknown", {}).get("unknown-entry", 0) == 0
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "filtered"
    assert list(iter_manifest_rows(cfg, "de")) == []


# ---------------------------------------------------------------------------
# MINOR 5 — coverage for the existing bad-doc-type / bad-provenance actions
# ---------------------------------------------------------------------------

def test_bad_doc_type_action(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/baddoctype.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url, doc_type="ZZZ")])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "wayback", "final_url": "https://web.archive.org/baddoctype",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-doc-type"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-doc-type"


def test_bad_provenance_action(tmp_path, monkeypatch):
    from cb_corpus.recover import run_recover_downloads

    cfg = Config(data_dir=tmp_path)
    dead_url = "https://www.banque-france.fr/badprov.pdf"
    _write_inventory(cfg, [_entry(bank="fr", pdf_url=dead_url)])
    local_pdf = _make_local_pdf(tmp_path)
    cand_path = _write_candidates(tmp_path, [{
        "dead_pdf_url": dead_url, "file_path": str(local_pdf),
        "recovered_from": "some_unrecognised_source", "final_url": "https://example.org/x",
    }])
    _stub_refresh_metadata(monkeypatch)

    results = run_recover_downloads(config=cfg, fetcher=_NullFetcher(),
                                    candidates=str(cand_path), download=True,
                                    csv_path=str(tmp_path / "r.csv"))
    assert results["fr"]["bad-provenance"] == 1
    assert list(iter_manifest_rows(cfg, "fr")) == []
    csv_rows = _csv_rows(tmp_path / "r.csv")
    assert csv_rows[0]["action"] == "bad-provenance"
