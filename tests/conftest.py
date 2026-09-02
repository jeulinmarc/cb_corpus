"""Shared test fixtures.

The suite-wide network guard lives in `pytest.ini` (`--disable-socket`), not
here: pytest-socket re-enables sockets in its per-test teardown hook, so a
session-scoped fixture calling `disable_socket()` would only protect the very
first test. See `tests/test_network_guard.py` for the proof that it holds.

This module carries one shared duck-typed fetcher so the newer discovery tests
(Bundesbank / BoE / BoJ / reindex) don't each grow their own copy. The older
test modules keep their own local fakes on purpose — they are not touched.
"""
from __future__ import annotations

import pytest


class RecordingFetcher:
    """Stand-in for `cb_corpus.http.Fetcher`, serving canned pages off disk.

    `pages` maps a URL *suffix* to the body to return (same matching rule as
    the local fakes in `tests/test_bdf_wp.py`); a body that is an `Exception`
    is raised instead, which is how a dead page/listing is simulated. An
    unmapped URL raises, exactly as a live 404 does. Every requested URL is
    appended to `calls`, so tests can assert on fetch ORDER and on fetches
    that must NOT happen (early stops, dedup, pagination cut-offs).
    """

    def __init__(self, pages: dict[str, object]):
        self.pages = pages
        self.calls: list[str] = []

    def get_text(self, url: str) -> str:
        self.calls.append(url)
        for suffix, body in self.pages.items():
            if url.endswith(suffix):
                if isinstance(body, Exception):
                    raise body
                return body  # type: ignore[return-value]
        raise RuntimeError(f"404: {url}")


@pytest.fixture
def fetcher_factory():
    """Build a `RecordingFetcher` from a {url_suffix: body_or_exception} map."""
    return RecordingFetcher
