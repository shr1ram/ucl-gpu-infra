"""Tests for run_poller — the OMC-free poll/filter/terminal logic."""
from ucl_gpu_infra import run_poller as rp


def test_is_terminal():
    assert rp.is_terminal({"status": "succeeded"})
    assert rp.is_terminal({"status": "FAILED"})       # case-insensitive
    assert not rp.is_terminal({"status": "running"})
    assert not rp.is_terminal({})                      # missing → not terminal


def test_filter_by_workdir_matches_run_command_or_workdir():
    runs = [
        {"run_id": "a", "run_command": "cd omc/p1/iter_001 && python x.py", "workdir": ""},
        {"run_id": "b", "run_command": "python y.py", "workdir": "omc/p1/iter_001"},
        {"run_id": "c", "run_command": "cd omc/p2/iter_001 && python z.py", "workdir": ""},
    ]
    got = {r["run_id"] for r in rp.filter_runs_by_workdir(runs, "omc/p1/iter_001")}
    assert got == {"a", "b"}, "matches either run_command OR workdir, scoped to the marker"


def test_filter_empty_marker_returns_nothing():
    assert rp.filter_runs_by_workdir([{"run_id": "a", "workdir": "x"}], "") == []


def test_all_terminal_requires_present_and_terminal(monkeypatch):
    poller = rp.RunPoller(find_marker=lambda rid: f"omc/{rid}")
    # b is still running → not all terminal
    monkeypatch.setattr(rp, "list_runs", lambda limit=100: [
        {"run_id": "r1", "status": "succeeded", "workdir": "omc/p1"},
        {"run_id": "r2", "status": "running", "workdir": "omc/p1"},
    ])
    assert poller.all_terminal("p1", ["r1", "r2"]) is False
    assert poller.all_terminal("p1", ["r1"]) is True


def test_all_terminal_missing_run_is_not_terminal(monkeypatch):
    poller = rp.RunPoller(find_marker=lambda rid: f"omc/{rid}")
    # r2 aged out of the listing → must NOT count as terminal
    monkeypatch.setattr(rp, "list_runs", lambda limit=100: [
        {"run_id": "r1", "status": "succeeded", "workdir": "omc/p1"},
    ])
    assert poller.all_terminal("p1", ["r1", "r2"]) is False
    assert poller.all_terminal("p1", []) is False  # nothing expected → not "done"
