"""Per-URL download quarantine (recover-quarantine design §3).

State file `data/download_quarantine.jsonl` (append-only, latest line per url
wins) tracks per-URL consecutive DISTINCT failing nights. A URL is
quarantined once it accumulates `QUARANTINE_AFTER_NIGHTS` (env, default 5)
distinct nights of failure; the nightly bounded sync then skips it (one
summary log line) and only the Sunday full sweep retries it
(`QUARANTINE_RETRY=1` bypass — the decision is skipped, but the state file
keeps recording normally). Any success tombstones the url (clears state);
a later failure restarts the night count from zero.

Same-night repeats (a URL retried more than once within one nightly run)
must count as ONE failing night, not N — otherwise a single bad night with
retries could quarantine a URL early.
"""
from __future__ import annotations

import json

from cb_corpus.config import Config
from cb_corpus.quarantine import Quarantine


def _mk(tmp_path) -> Quarantine:
    return Quarantine(Config(data_dir=tmp_path))


# --- distinct-night counting ------------------------------------------------

def test_five_distinct_nights_quarantines_at_default_threshold(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True


def test_four_distinct_nights_not_yet_quarantined(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is False


def test_five_entries_same_night_not_quarantined(tmp_path):
    """Same-night repeat failures (retries within one nightly run) count once."""
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for _ in range(5):
        q.record_failure(url, "2026-08-01")
    assert q.is_quarantined(url) is False


def test_unknown_url_is_not_quarantined(tmp_path):
    q = _mk(tmp_path)
    assert q.is_quarantined("https://x.test/never-seen.pdf") is False


# --- resurrection ------------------------------------------------------------

def test_record_success_tombstones_and_clears_quarantine(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True

    q.record_success(url)

    assert q.is_quarantined(url) is False


def test_resurrection_then_refailure_restarts_the_night_count(tmp_path):
    """After a release, the URL must climb all the way back to the threshold
    again — no memory of the prior failing streak."""
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)
    q.record_success(url)

    # A single new failure after release must NOT immediately re-quarantine.
    q.record_failure(url, "2026-08-10")
    assert q.is_quarantined(url) is False

    for night in ["2026-08-11", "2026-08-12", "2026-08-13", "2026-08-14"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True


def test_record_success_is_noop_on_unknown_url(tmp_path):
    """If record_success is called on a URL never seen before (no active state),
    it returns without writing — a guard against unbounded growth."""
    q = _mk(tmp_path)
    unknown_url = "https://x.test/never-seen.pdf"
    q.record_success(unknown_url)

    path = tmp_path / "download_quarantine.jsonl"
    assert not path.exists() or path.read_text() == ""


# --- bypass env ---------------------------------------------------------------

def test_quarantine_retry_bypasses_skip_decision_but_keeps_state(tmp_path, monkeypatch):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True

    monkeypatch.setenv("QUARANTINE_RETRY", "1")
    assert q.is_quarantined(url) is False

    # State survives the bypass: a fresh instance (env still bypassed) still
    # sees the full night history once the bypass is lifted.
    monkeypatch.delenv("QUARANTINE_RETRY", raising=False)
    q2 = _mk(tmp_path)
    assert q2.is_quarantined(url) is True


def test_bypass_does_not_count_toward_skipped_count(tmp_path, monkeypatch):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)

    monkeypatch.setenv("QUARANTINE_RETRY", "1")
    q.is_quarantined(url)
    assert q.skipped_count() == 0
    assert q.summary_line() is None


def test_decision_locking_recomputes_threshold_on_fresh_instance(tmp_path, monkeypatch):
    """Quarantine decision is not cached — it recomputes against the CURRENT
    threshold at call time (both is_quarantined and fresh instance reload).
    Write 5 failures with threshold=5 (quarantined), then a fresh instance
    with threshold=10 must see is_quarantined()=False, then back to 5 must
    see True again."""
    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "5")
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True

    # Fresh instance with higher threshold: same 5 nights now insufficient.
    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "10")
    q_higher = _mk(tmp_path)
    assert q_higher.is_quarantined(url) is False

    # Back to threshold=5: URL is quarantined again.
    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "5")
    q_restored = _mk(tmp_path)
    assert q_restored.is_quarantined(url) is True


# --- custom threshold (read at call time) -------------------------------------

def test_custom_threshold_env_read_at_call_time(tmp_path, monkeypatch):
    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "3")
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    q.record_failure(url, "2026-08-01")
    q.record_failure(url, "2026-08-02")
    assert q.is_quarantined(url) is False
    q.record_failure(url, "2026-08-03")
    assert q.is_quarantined(url) is True


# --- nights list capped at threshold (no unbounded growth) -------------------

def test_nights_list_capped_at_threshold(tmp_path, monkeypatch):
    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "3")
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    nights = ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04",
              "2026-08-05", "2026-08-06", "2026-08-07"]
    for night in nights:
        q.record_failure(url, night)
    assert q.is_quarantined(url) is True

    lines = (tmp_path / "download_quarantine.jsonl").read_text().splitlines()
    last_row = json.loads(lines[-1])
    assert len(last_row["nights"]) == 3
    # Most recent nights kept, not the oldest.
    assert last_row["nights"] == ["2026-08-05", "2026-08-06", "2026-08-07"]


# --- skipped_count / summary_line ---------------------------------------------

def test_skipped_count_and_summary_line_count_true_hits_this_run(tmp_path):
    q = _mk(tmp_path)
    quarantined_url = "https://x.test/dead.pdf"
    active_url = "https://x.test/alive.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04", "2026-08-05"]:
        q.record_failure(quarantined_url, night)
    q.record_failure(active_url, "2026-08-01")

    assert q.skipped_count() == 0
    assert q.summary_line() is None

    q.is_quarantined(quarantined_url)
    q.is_quarantined(active_url)     # below threshold -> not a skip
    q.is_quarantined(quarantined_url)

    assert q.skipped_count() == 2
    assert q.summary_line() == "quarantine: skipped 2 url(s)"


def test_summary_line_is_none_when_nothing_skipped(tmp_path):
    q = _mk(tmp_path)
    assert q.summary_line() is None


# --- state-file format / round-trip -------------------------------------------

def test_record_failure_appends_expected_row_shape(tmp_path):
    q = _mk(tmp_path)
    q.record_failure("https://x.test/dead.pdf", "2026-08-01")
    path = tmp_path / "download_quarantine.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    assert row == {
        "url": "https://x.test/dead.pdf",
        "nights": ["2026-08-01"],
        "quarantined": False,
    }


def test_record_success_appends_released_tombstone(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    q.record_failure(url, "2026-08-01")
    q.record_success(url)
    path = tmp_path / "download_quarantine.jsonl"
    rows = [json.loads(line) for line in path.read_text().splitlines()]
    assert rows[-1] == {"url": url, "released": True}


def test_latest_line_per_url_wins_on_reload(tmp_path):
    """Two fresh Quarantine instances against the same data_dir simulate two
    separate process runs (e.g. two nightly syncs) — the second must see the
    first's appended state, not a stale in-memory snapshot."""
    q1 = _mk(tmp_path)
    url = "https://x.test/dead.pdf"
    for night in ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]:
        q1.record_failure(url, night)
    assert q1.is_quarantined(url) is False

    q2 = _mk(tmp_path)
    q2.record_failure(url, "2026-08-05")
    assert q2.is_quarantined(url) is True

    q3 = _mk(tmp_path)
    assert q3.is_quarantined(url) is True


# --- seed() (recover-quarantine design §4: seed final unrecoverables) --------

def test_seed_immediately_quarantines_without_night_history(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/confirmed-dead.pdf"
    q.seed(url, "hunt exhausted: wayback + bank site + mirrors all checked")
    assert q.is_quarantined(url) is True

    path = tmp_path / "download_quarantine.jsonl"
    row = json.loads(path.read_text().splitlines()[0])
    assert row == {
        "url": url,
        "seeded": "hunt exhausted: wayback + bank site + mirrors all checked",
        "quarantined": True,
    }


def test_seed_survives_reload_and_raising_threshold(tmp_path, monkeypatch):
    """A seeded URL stays quarantined on a fresh instance, and raising
    QUARANTINE_AFTER_NIGHTS afterwards must NOT un-quarantine it (it has no
    night-count history to compare against a threshold)."""
    q = _mk(tmp_path)
    url = "https://x.test/confirmed-dead.pdf"
    q.seed(url, "unrecoverable")

    monkeypatch.setenv("QUARANTINE_AFTER_NIGHTS", "50")
    q2 = _mk(tmp_path)
    assert q2.is_quarantined(url) is True


def test_seed_then_record_success_releases_it(tmp_path):
    q = _mk(tmp_path)
    url = "https://x.test/confirmed-dead.pdf"
    q.seed(url, "unrecoverable")
    assert q.is_quarantined(url) is True

    q.record_success(url)
    assert q.is_quarantined(url) is False


def test_record_failure_is_noop_on_seeded_row(tmp_path):
    """A seeded row must not decay into a night-count row. Scenario: the
    Sunday full sweep (QUARANTINE_RETRY=1) retries a seeded dead URL and the
    retry fails -- record_failure must leave the seed standing (not
    overwrite it with a fresh 1-night count row, which would let the
    bounded Mon-Sat sync start re-hammering the URL again before it
    re-accumulates QUARANTINE_AFTER_NIGHTS worth of failures)."""
    q = _mk(tmp_path)
    url = "https://x.test/confirmed-dead.pdf"
    q.seed(url, "hunt exhausted")

    q.record_failure(url, "2026-08-01")

    q2 = _mk(tmp_path)
    assert q2.is_quarantined(url) is True

    lines = (tmp_path / "download_quarantine.jsonl").read_text().splitlines()
    last_row = json.loads(lines[-1])
    assert "seeded" in last_row
    assert last_row["seeded"] == "hunt exhausted"


# --- corrupt/torn line tolerance ----------------------------------------------

def test_corrupt_line_is_skipped_with_a_single_warning(tmp_path, capsys):
    path = tmp_path / "download_quarantine.jsonl"
    tmp_path.mkdir(parents=True, exist_ok=True)
    good1 = json.dumps({"url": "https://x.test/a.pdf", "nights": ["2026-08-01"],
                        "quarantined": False})
    bad = '{"url": "https://x.test/b.pdf", "nights": [\''  # malformed JSON
    good2 = json.dumps({"url": "https://x.test/c.pdf",
                        "nights": ["2026-08-01", "2026-08-02", "2026-08-03",
                                   "2026-08-04", "2026-08-05"],
                        "quarantined": True})
    path.write_text(good1 + "\n" + bad + "\n" + good2 + "\n")

    q = Quarantine(Config(data_dir=tmp_path))

    # Corrupt line skipped, valid lines around it still loaded.
    assert q.is_quarantined("https://x.test/a.pdf") is False
    assert q.is_quarantined("https://x.test/c.pdf") is True
    assert q.is_quarantined("https://x.test/b.pdf") is False  # never loaded

    err = capsys.readouterr().err
    assert err.count("WARNING") == 1
    assert str(path) in err


def test_torn_final_line_does_not_crash_load(tmp_path):
    """A SIGKILL/ENOSPC mid-append leaves a malformed FINAL line (no closing
    brace) — the brief's contract for this file is simpler than the manifest's
    repair machinery: skip and warn, never crash. No repair/truncation is
    required of this module."""
    path = tmp_path / "download_quarantine.jsonl"
    good = json.dumps({"url": "https://x.test/a.pdf", "nights": ["2026-08-01"],
                       "quarantined": False})
    torn = '{"url": "https://x.test/b.pdf", "nights": ["2026-08-01'  # no closing
    path.write_text(good + "\n" + torn)

    q = Quarantine(Config(data_dir=tmp_path))

    assert q.is_quarantined("https://x.test/a.pdf") is False
    assert q.is_quarantined("https://x.test/b.pdf") is False


def test_empty_and_missing_state_file_load_as_empty(tmp_path):
    # missing file
    q = Quarantine(Config(data_dir=tmp_path))
    assert q.is_quarantined("https://x.test/a.pdf") is False

    # empty file
    path = tmp_path / "download_quarantine.jsonl"
    path.write_text("")
    q2 = Quarantine(Config(data_dir=tmp_path))
    assert q2.is_quarantined("https://x.test/a.pdf") is False
