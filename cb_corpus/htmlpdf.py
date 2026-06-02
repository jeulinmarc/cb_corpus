"""HTML -> PDF via headless Chrome.

Used by Storage to render text/html responses (e.g. ECB monetary policy
accounts) into PDFs so the corpus stays uniform for downstream ingestion
(eigenmind handles PDF, not HTML).

Why Chrome and not the cached HTML file:
  ECB pages reference CSS by relative URL; rendering the local cached .html
  with file:// loses the styling. We point Chrome at the live URL so external
  resources resolve properly.
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional


# Try the standard macOS path first, then common Linux names.
_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome",
    "chromium",
    "chromium-browser",
    "chrome",
)


def find_chrome() -> Optional[str]:
    for c in _CHROME_CANDIDATES:
        if "/" in c:
            if Path(c).exists():
                return c
        else:
            path = shutil.which(c)
            if path:
                return path
    return None


def render_url_to_pdf(url: str, output: Path,
                      chrome: Optional[str] = None,
                      timeout: float = 60.0,
                      user_data_dir: Optional[str] = None) -> None:
    """Render the page at `url` to `output` via headless Chrome.

    If `user_data_dir` is given, the directory is reused across calls —
    avoids cold-start cost (per-call temp profile is ~10x slower because
    Chrome rebuilds the profile every invocation). The caller owns cleanup.
    When omitted, a fresh tempdir is created per call (safe but slow).

    Raises RuntimeError if Chrome is not found or the conversion fails.
    """
    chrome = chrome or find_chrome()
    if chrome is None:
        raise RuntimeError("no Chrome / Chromium binary found")
    output.parent.mkdir(parents=True, exist_ok=True)
    if user_data_dir is not None:
        _run_chrome(chrome, url, output, user_data_dir, timeout)
    else:
        with tempfile.TemporaryDirectory(prefix="cbc_chrome_") as tmpdir:
            _run_chrome(chrome, url, output, tmpdir, timeout)
    if not output.exists() or output.stat().st_size == 0:
        raise RuntimeError(f"Chrome produced no PDF for {url}")


def _run_chrome(chrome: str, url: str, output: Path,
                user_data_dir: str, timeout: float) -> None:
    # `--headless=new` is ~25x slower than legacy `--headless` for print-to-pdf
    # (cold-startup overhead per invocation). Use legacy.
    cmd = [
        chrome,
        "--headless",
        "--disable-gpu",
        "--no-sandbox",
        f"--user-data-dir={user_data_dir}",
        f"--print-to-pdf={output}",
        url,
    ]
    # IMPORTANT: do NOT use capture_output / PIPE here.
    # Chrome streams verbose output to stderr; if we capture it via a pipe
    # without draining, the pipe buffer (~64KB) fills and Chrome blocks for
    # the entire timeout. Discarding to DEVNULL keeps it fast (~4s/render).
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Chrome failed (exit {e.returncode}) on {url}") from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"Chrome timed out after {timeout}s on {url}") from e
