"""Per-URL download quarantine — dead-letter state for the nightly sync path.

One month of production left exactly 76 unique documents failing every
single night (dead RePEc-sourced PDF URLs, `download_errors.jsonl` audit).
This module stops that re-hammering: it tracks, per URL, the number of
CONSECUTIVE DISTINCT nights a download has failed, and once that reaches
`QUARANTINE_AFTER_NIGHTS` (env, default 5) the caller is told to skip the URL
in the nightly bounded (Mon-Sat) sync — only the Sunday full sweep (`sync
full`, via `QUARANTINE_RETRY=1`) still tries it. Any success releases the
URL immediately (tombstone); a later failure restarts the count from zero.

State: `data/download_quarantine.jsonl` beside `download_errors.jsonl`,
append-only, one JSON row per event:
    {"url": ..., "nights": ["YYYY-MM-DD", ...], "quarantined": bool}
    {"url": ..., "released": true}                          # tombstone

Latest line per url wins on replay (same discipline as the manifest files in
storage.py) — a `released` tombstone clears all prior state for that url.
Corrupt/torn lines are skipped with ONE warning for the whole load (not one
per line: a bad night can produce many failing URLs, and this file is
operational churn, not an audit record worth a hard stop) — deliberately
simpler than storage.py's torn-tail repair machinery: this file is never
read back for anything but quarantine decisions, so there is nothing to
repair-in-place, only something to never let crash the caller.

Same-night repeats (a URL retried more than once within one nightly run,
before Quarantine.record_failure is even in the picture) must count as ONE
failing night — otherwise a single bad night with internal retries could
quarantine a URL that has actually only failed once.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Optional

from .config import Config

_DEFAULT_THRESHOLD = 5


def _threshold() -> int:
    """Distinct-failing-nights threshold. Re-read from the environment on
    EVERY call (never cached on the instance) so `QUARANTINE_AFTER_NIGHTS`
    can be tuned between runs (or even between calls, in tests) without
    reconstructing a Quarantine. Malformed/non-positive values fall back to
    the default rather than raising — a typo in an env var must never crash
    the nightly sync."""
    raw = os.environ.get("QUARANTINE_AFTER_NIGHTS", str(_DEFAULT_THRESHOLD))
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_THRESHOLD
    return n if n > 0 else _DEFAULT_THRESHOLD


class Quarantine:
    """Per-URL dead-letter state, backed by an append-only JSONL file."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self._path = cfg.data_dir / "download_quarantine.jsonl"
        # url -> {"nights": [...]}; "quarantined" is NOT trusted from disk as
        # the live truth (see is_quarantined) — it's written for operator
        # readability only, recomputed fresh against the CURRENT threshold.
        self._state: dict[str, dict] = {}
        self._skipped = 0
        self._load()

    # -- state file IO ---------------------------------------------------
    def _load(self) -> None:
        if not self._path.exists():
            return
        raw = self._path.read_bytes()
        warned = False
        for ln in raw.splitlines():
            if not ln.strip():
                continue
            try:
                row = json.loads(ln.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                if not warned:
                    print(f"[quarantine] WARNING: corrupt line(s) in {self._path}, "
                          f"skipped", file=sys.stderr, flush=True)
                    warned = True
                continue
            url = row.get("url") if isinstance(row, dict) else None
            if not url:
                if not warned:
                    print(f"[quarantine] WARNING: corrupt line(s) in {self._path}, "
                          f"skipped", file=sys.stderr, flush=True)
                    warned = True
                continue
            if row.get("released"):
                self._state.pop(url, None)
                continue
            if "seeded" in row:
                # A seeded row is unconditionally quarantined (see seed())
                # independent of the night-count threshold -- no synthetic
                # nights list to replay.
                self._state[url] = {"nights": [], "seeded": True}
                continue
            self._state[url] = {"nights": list(row.get("nights") or [])}

    def _append(self, row: dict) -> None:
        """Append a single JSON row. Each row is a single small write() call on
        an O_APPEND handle (effectively atomic on POSIX). Unlike storage.py's
        _append, no flock is taken because quarantine writes happen from the
        serialized sync loop, not parallel workers — callers must keep it that
        way."""
        self.cfg.data_dir.mkdir(parents=True, exist_ok=True)
        with self._path.open("a") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # -- public API --------------------------------------------------------
    def is_quarantined(self, url: str) -> bool:
        """True only when `url` is ACTIVELY quarantined for this call.

        Recomputed from the night count against the CURRENT threshold (not
        the cached on-disk `quarantined` flag from whenever it was last
        written) — "read at call time" per the design contract. Every True
        return here represents one URL the caller will actually skip this
        run, which is what skipped_count()/summary_line() report; a
        QUARANTINE_RETRY=1 bypass returns False (so the Sunday full sweep
        retries everything) WITHOUT mutating state and WITHOUT counting as
        a skip.
        """
        state = self._state.get(url)
        if not state:
            return False
        if not state.get("seeded") and len(state["nights"]) < _threshold():
            return False
        if os.environ.get("QUARANTINE_RETRY") == "1":
            return False
        self._skipped += 1
        return True

    def record_failure(self, url: str, night: str) -> None:
        """Record one failed download attempt for `url` on `night`
        ("YYYY-MM-DD"). A `night` equal to the LAST recorded night for this
        url is a no-op on the count (same-night idempotence) but still
        appends a state line (cheap, and keeps the on-disk trail showing the
        retry happened). The nights list is capped at the current threshold
        so a URL that fails for months doesn't grow this file's per-url
        state without bound — only the most recent nights are kept.
        """
        state = self._state.setdefault(url, {"nights": []})
        nights = state["nights"]
        if not nights or nights[-1] != night:
            nights.append(night)
        threshold = _threshold()
        if len(nights) > threshold:
            del nights[: len(nights) - threshold]
        quarantined = len(nights) >= threshold
        self._append({"url": url, "nights": list(nights), "quarantined": quarantined})

    def record_success(self, url: str) -> None:
        """Release `url`: append a `released` tombstone and clear in-memory
        state. A subsequent failure starts a fresh night count from zero —
        no memory of the prior failing streak survives a release.

        No-op if `url` has no active state (not in the in-memory dict) — a
        guard against unbounded growth if callers fire this on every successful
        download without checking quarantine status first."""
        if url not in self._state:
            return
        self._state.pop(url, None)
        self._append({"url": url, "released": True})

    def seed(self, url: str, reason: str = "") -> None:
        """Seed `url` as ALREADY quarantined, for a document confirmed
        unrecoverable after a manual hunt (recover-quarantine design §4) --
        the nightly sync must stop re-hammering it immediately, without
        waiting to accumulate `QUARANTINE_AFTER_NIGHTS` worth of synthetic
        failures first (that would misrepresent the actual failure history:
        it never really failed that many DISTINCT nights, a human just
        confirmed it dead). Writes one line
        `{"url": url, "seeded": reason, "quarantined": True}` -- honest
        history, distinct from a real night-count row. The "seeded" marker
        is unconditionally quarantined (independent of the night-count
        threshold, so raising QUARANTINE_AFTER_NIGHTS later never
        un-quarantines it) until a `record_success` release."""
        self._state[url] = {"nights": [], "seeded": True}
        self._append({"url": url, "seeded": reason, "quarantined": True})

    def skipped_count(self) -> int:
        """Number of is_quarantined() calls THIS RUN that returned True —
        i.e. URLs the calling sync loop actually skipped (bypassed calls
        never count, since nothing was skipped)."""
        return self._skipped

    def summary_line(self) -> Optional[str]:
        """One-line nightly log summary, or None when nothing was skipped
        (keeps a clean run's log free of a zero-noise line)."""
        if self._skipped == 0:
            return None
        return f"quarantine: skipped {self._skipped} url(s)"
