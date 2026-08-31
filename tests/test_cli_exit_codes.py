import json

import cb_corpus.cli as cli


def _read_report(data_dir):
    lines = (data_dir / "runs.jsonl").read_text().strip().split("\n")
    return json.loads(lines[-1])


def test_discover_clean_run_exits_zero_and_reports(tmp_path, monkeypatch):
    def fake_run(**kwargs):
        report = kwargs.get("report")
        report.source("us").record_saved_counts({"saved": 2})
        return {"us": {"saved": 2}}

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    rc = cli.main(["discover", "--banks", "us"])
    assert rc == 0
    rep = _read_report(tmp_path)
    assert rep["outcome"] == "ok" and rep["command"] == "discover"


def test_discover_truncated_source_exits_three(tmp_path, monkeypatch):
    def fake_run(**kwargs):
        s = kwargs["report"].source("us")
        s.record_fetch_error("listing page unreachable", truncated=True)
        return {"us": {}}

    monkeypatch.setattr(cli, "run", fake_run)
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    assert cli.main(["discover", "--banks", "us"]) == 3
    assert _read_report(tmp_path)["outcome"] == "degraded"


def test_crash_writes_failed_report_and_exits_one(tmp_path, monkeypatch):
    def boom(**kwargs):
        raise RuntimeError("adapter exploded")

    monkeypatch.setattr(cli, "run", boom)
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    assert cli.main(["discover", "--banks", "us"]) == 1
    rep = _read_report(tmp_path)
    assert rep["outcome"] == "failed" and "adapter exploded" in rep["fatal"]


def test_list_banks_stays_reportless_and_zero(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("CB_DATA_DIR", str(tmp_path))
    assert cli.main(["list-banks"]) == 0
    assert not (tmp_path / "runs.jsonl").exists()
