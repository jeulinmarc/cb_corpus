"""The suite's network guard actually blocks a real HTTP fetch.

`pytest.ini`'s `--disable-socket` blocks sockets for every test so none can
silently crawl a live central-bank site. A guard nobody exercises rots: these
tests build the REAL `cb_corpus.http.Fetcher` (its `.session` untouched — no fake,
no monkeypatch of `requests`) and prove the guard stops it, so a future
refactor that drops the option or the `pytest-socket` dependency fails here
instead of quietly going online.

The URL used is `http://127.0.0.1:9/...` (the discard port, nothing listening)
so that even with the guard removed the check fails fast and locally, without
sending a packet anywhere near a real bank. pytest-socket warns as well as
raising on a blocked call; the warning is filtered per test so the suite's
output stays clean.
"""
from __future__ import annotations

import socket

import pytest
from pytest_socket import SocketBlockedError

from cb_corpus.config import Config
from cb_corpus.http import Fetcher

BLOCKED_URL = "http://127.0.0.1:9/central-bank-corpus-network-guard"


def _causes(exc: BaseException) -> list[BaseException]:
    """The exception plus its full `__cause__`/`__context__` chain."""
    chain = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_real_fetcher_get_is_blocked_by_the_socket_guard(monkeypatch):
    """A real Fetcher.get() cannot reach the network: the socket guard raises,
    and `Fetcher`'s catch-all retry loop re-raises it as the RuntimeError whose
    cause chain still carries the SocketBlockedError — so the failure is
    attributable to the guard, not mistaken for a flaky site."""
    # Retry knobs to the minimum + no backoff sleeps: a blocked socket must not
    # turn one assertion into a multi-second retry storm.
    monkeypatch.setattr("cb_corpus.http.time.sleep", lambda _seconds: None)
    fetcher = Fetcher(Config(max_retries=1, min_delay_seconds=0.0, timeout=1.0))

    with pytest.raises(RuntimeError) as excinfo:
        fetcher.get(BLOCKED_URL)

    assert BLOCKED_URL in str(excinfo.value)
    assert any(isinstance(e, SocketBlockedError) for e in _causes(excinfo.value)), (
        "the network guard is not in force: Fetcher.get() failed for some other "
        "reason than a blocked socket"
    )


@pytest.mark.filterwarnings("ignore:A test tried to use socket")
def test_opening_a_bare_socket_is_blocked():
    """The guard sits at the `socket` layer, not on a requests-only stub:
    anything that opens a socket (a hand-rolled urllib call, a library phoning
    home) is stopped too."""
    with pytest.raises(SocketBlockedError):
        socket.socket(socket.AF_INET, socket.SOCK_STREAM)
