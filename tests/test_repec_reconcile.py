"""repec-reconcile: strict one-shot stamping of IDEAS source_urls onto
uniquely-matched manifest rows (spec docs/superpowers/specs/2026-07-16-
download-failures-design.md §3). Adversarial fixtures on purpose: singleton
vs. duplicated titles, empty vs. non-empty source_url, multi-byte UTF-8
titles (a French accented title, per the house lesson that ASCII-only
fixtures mask bugs)."""
import json

from cb_corpus.config import Config
from cb_corpus.repec_check import run_repec_reconcile
from cb_corpus.sources.repec import IDEAS

ARCH, SLUG = "boe", "boeewp"      # real gb series (SERIES["gb"] in sources/repec.py)
BASE = f"{IDEAS}/s/{ARCH}/{SLUG}"


def _series_html(pairs):
    """pairs: [(pid, title)] -> an IDEAS series listing page."""
    links = "".join(
        f'<li><a href="/p/{ARCH}/{SLUG}/{pid}.html">{title}</a></li>'
        for pid, title in pairs
    )
    return f"<html><body><ul>{links}</ul></body></html>"


def _paper_html(title, pdf_url, pub="2001/01"):
    return (
        "<html><head>"
        f'<meta name="citation_title" content="{title}">'
        f'<meta name="citation_publication_date" content="{pub}">'
        "</head><body>"
        f'<a href="{pdf_url}">Download PDF</a>'
        "</body></html>"
    )


def _paper_url(pid):
    return f"{IDEAS}/p/{ARCH}/{SLUG}/{pid}.html"


class FakeFetcher:
    """Serves canned pages; records every URL fetched (spy)."""
    def __init__(self, pages):
        self.pages = pages
        self.fetched = []

    def get_text(self, url):
        self.fetched.append(url)
        if url not in self.pages:
            raise RuntimeError(f"404 {url}")
        return self.pages[url]


def _row(doc_id, title, pdf_url, source_url="", alt_urls=None):
    return {
        "bank_code": "gb", "doc_type": "D1", "title": title,
        "pdf_url": pdf_url, "source_url": source_url,
        "alt_urls": alt_urls or [], "date": "2001-01-01", "year": 2001,
        "language": "en", "provenance": "bank_site",
        "mime_type": "application/pdf", "sha256": "deadbeef",
        "local_path": f"/data/{doc_id}.pdf", "doc_id": doc_id,
        "date_precision": "day", "date_source": "bank_site", "repec_handle": "",
    }


def _write_manifest(cfg, bank, rows):
    cfg.manifest_dir.mkdir(parents=True, exist_ok=True)
    path = cfg.manifest_file(bank)
    with path.open("w") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return path


def _cfg(tmp_path):
    return Config(data_dir=tmp_path / "data")


def _read_rows(cfg, bank):
    path = cfg.manifest_file(bank)
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def _read_csv(path):
    import csv
    with open(path, newline="") as fh:
        return list(csv.DictReader(fh))


# -- Case 1: unique title match, empty source_url ---------------------------

def test_unique_title_match_dry_run_reports_stamp_and_leaves_file_untouched(tmp_path):
    cfg = _cfg(tmp_path)
    row = _row("r1", "Stabilité financière",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1992/stabilite-financiere.pdf")
    manifest_path = _write_manifest(cfg, "gb", [row])
    before = manifest_path.read_bytes()

    fetcher = FakeFetcher({f"{BASE}.html": _series_html([("0001", "Stabilité financière")])})
    results = run_repec_reconcile(bank_codes=["gb"], write=False,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 1, "ambiguous": 0, "already": 0, "unmatched": 0}
    assert manifest_path.read_bytes() == before   # dry-run: byte-identical


def test_unique_title_match_write_stamps_source_url_and_preserves_other_rows(tmp_path):
    cfg = _cfg(tmp_path)
    row1 = _row("r1", "Stabilité financière",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1992/stabilite-financiere.pdf")
    row2 = _row("r2", "Untouched Sibling Row",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1993/untouched.pdf",
               source_url="https://www.bankofengland.co.uk/working-paper/1993/untouched")
    _write_manifest(cfg, "gb", [row1, row2])

    fetcher = FakeFetcher({f"{BASE}.html": _series_html([("0001", "Stabilité financière")])})
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"]["stamped"] == 1
    rows = {r["doc_id"]: r for r in _read_rows(cfg, "gb")}
    assert rows["r1"]["source_url"] == _paper_url("0001")
    assert rows["r2"] == row2   # OTHER row preserved verbatim


# -- Case 2: two rows share the same normalized title -> ambiguous ----------

def test_duplicated_title_is_ambiguous_and_never_written(tmp_path):
    cfg = _cfg(tmp_path)
    row1 = _row("d1", "Duplicate Study On Growth",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1994/dup-a.pdf")
    row2 = _row("d2", "Duplicate Study On Growth",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1995/dup-b.pdf")
    _write_manifest(cfg, "gb", [row1, row2])
    before_rows = _read_rows(cfg, "gb")

    fetcher = FakeFetcher({f"{BASE}.html": _series_html([("0002", "Duplicate Study On Growth")])})
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 0, "ambiguous": 1, "already": 0, "unmatched": 0}
    assert _read_rows(cfg, "gb") == before_rows   # zero writes even with --write


# -- Case 3: matched row already carries a non-empty source_url -------------

def test_matched_row_with_existing_source_url_is_already_and_not_overwritten(tmp_path):
    cfg = _cfg(tmp_path)
    row = _row("e1", "Existing Landing Page Paper",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1996/existing.pdf",
               source_url="https://www.bankofengland.co.uk/working-paper/1996/existing")
    _write_manifest(cfg, "gb", [row])

    fetcher = FakeFetcher({f"{BASE}.html": _series_html([("0003", "Existing Landing Page Paper")])})
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 0, "ambiguous": 0, "already": 1, "unmatched": 0}
    rows = {r["doc_id"]: r for r in _read_rows(cfg, "gb")}
    assert rows["e1"]["source_url"] == "https://www.bankofengland.co.uk/working-paper/1996/existing"


# -- Case 4: listing entry matching nothing -> unmatched, in the CSV --------

def test_no_match_is_unmatched_and_appears_in_csv(tmp_path):
    cfg = _cfg(tmp_path)
    row = _row("f1", "Completely Unrelated Row",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1997/unrelated.pdf")
    _write_manifest(cfg, "gb", [row])

    pages = {
        f"{BASE}.html": _series_html([("0004", "Nowhere Paper Nobody Owns")]),
        _paper_url("0004"): _paper_html(
            "Nowhere Paper Nobody Owns Full Title",
            "https://www.bankofengland.co.uk/-/media/boe/files/wp/1997/nowhere.pdf"),
    }
    fetcher = FakeFetcher(pages)
    csv_path = str(tmp_path / "out.csv")
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=csv_path, config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 0, "ambiguous": 0, "already": 0, "unmatched": 1}
    csv_rows = _read_csv(csv_path)
    unmatched = [r for r in csv_rows if r["action"] == "unmatched"]
    assert len(unmatched) == 1
    assert unmatched[0]["ideas_url"] == _paper_url("0004")


# -- Case 5: IDEAS URL already a row's source_url -> already, no page fetch -

def test_known_source_url_is_already_with_no_paper_page_fetch(tmp_path):
    cfg = _cfg(tmp_path)
    row = _row("g1", "Some Already Reconciled Paper",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1998/already.pdf",
               source_url=_paper_url("0005"))
    _write_manifest(cfg, "gb", [row])

    # Deliberately NO page for pid 0005 in the fake fetcher: if the code
    # fetches it despite the known source_url, the fetch raises (404) and
    # the spy assertion below catches it either way.
    fetcher = FakeFetcher({f"{BASE}.html": _series_html([("0005", "Some Title")])})
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 0, "ambiguous": 0, "already": 1, "unmatched": 0}
    assert _paper_url("0005") not in fetcher.fetched


# -- Case 6: idempotence -----------------------------------------------------

def test_write_twice_second_run_stamps_zero(tmp_path):
    cfg = _cfg(tmp_path)
    row = _row("h1", "Idempotence Check Paper",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/1999/idem.pdf")
    _write_manifest(cfg, "gb", [row])

    pages = {f"{BASE}.html": _series_html([("0006", "Idempotence Check Paper")])}
    first = run_repec_reconcile(bank_codes=["gb"], write=True,
                                csv_path=str(tmp_path / "out1.csv"),
                                config=cfg, fetcher=FakeFetcher(pages))
    assert first["gb"]["stamped"] == 1

    second = run_repec_reconcile(bank_codes=["gb"], write=True,
                                 csv_path=str(tmp_path / "out2.csv"),
                                 config=cfg, fetcher=FakeFetcher(pages))
    assert second["gb"]["stamped"] == 0
    assert second["gb"]["already"] == 1


# -- Case 7: match via second-pass PDF-candidate URL (gb slug-URL banks) ----

def test_second_pass_pdf_candidate_url_match_stamps(tmp_path):
    cfg = _cfg(tmp_path)
    # Title deliberately differs from the listing title (no first-pass match
    # possible for gb: key_handle is always None, so only the second-pass
    # PDF-candidate URL can resolve this).
    row = _row("i1", "Understanding Monetary Transmission In The UK",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/2000/monetary-transmission.pdf")
    _write_manifest(cfg, "gb", [row])

    pages = {
        f"{BASE}.html": _series_html([("0007", "On Monetary Policy (Old Listing Title)")]),
        _paper_url("0007"): _paper_html(
            "On Monetary Policy (Old Listing Title)",
            "https://www.bankofengland.co.uk/-/media/boe/files/wp/2000/monetary-transmission.pdf"),
    }
    fetcher = FakeFetcher(pages)
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=str(tmp_path / "out.csv"),
                                  config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 1, "ambiguous": 0, "already": 0, "unmatched": 0}
    rows = {r["doc_id"]: r for r in _read_rows(cfg, "gb")}
    assert rows["i1"]["source_url"] == _paper_url("0007")


# -- Case 8: reverse ambiguity — two listing entries, one manifest row ------

def test_two_listing_entries_matching_one_row_only_first_stamps(tmp_path):
    """Two DIFFERENT listing pids, sharing a normalized title, each uniquely
    resolve (via title) to the SAME single manifest row. Without a claimed-
    doc_id guard, both entries see an empty source_url (the write only
    happens after the whole listing is walked) and both classify as
    ``stamp``, so counts/CSV assert two writes for a doc_id that can only be
    written once (dict last-wins). The reverse-ambiguity rule: the FIRST
    entry stamps, the SECOND is reported ``ambiguous`` (not written)."""
    cfg = _cfg(tmp_path)
    row = _row("j1", "Reverse Ambiguity Paper",
               "https://www.bankofengland.co.uk/-/media/boe/files/wp/2002/reverse.pdf")
    _write_manifest(cfg, "gb", [row])

    pages = {f"{BASE}.html": _series_html([
        ("0008", "Reverse Ambiguity Paper"),
        ("0009", "Reverse Ambiguity Paper"),
    ])}
    fetcher = FakeFetcher(pages)
    csv_path = str(tmp_path / "out.csv")
    results = run_repec_reconcile(bank_codes=["gb"], write=True,
                                  csv_path=csv_path, config=cfg, fetcher=fetcher)

    assert results["gb"] == {"stamped": 1, "ambiguous": 1, "already": 0, "unmatched": 0}

    csv_rows = _read_csv(csv_path)
    stamp_rows = [r for r in csv_rows if r["action"] == "stamp"]
    ambiguous_rows = [r for r in csv_rows if r["action"] == "ambiguous"]
    assert len(stamp_rows) == 1
    assert len(ambiguous_rows) == 1
    assert stamp_rows[0]["ideas_url"] == _paper_url("0008")
    assert ambiguous_rows[0]["ideas_url"] == _paper_url("0009")

    rows = {r["doc_id"]: r for r in _read_rows(cfg, "gb")}
    assert rows["j1"]["source_url"] == _paper_url("0008")   # FIRST entry wins
