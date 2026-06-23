"""Tests for the actual HTTP polling in run_poller — the network path that the
pure-logic tests skip. Uses httpx.MockTransport so the real request-building and
response-parsing code runs, no live shim required."""
import httpx
import pytest

from ucl_gpu_infra import run_poller


def _patch_post(monkeypatch, handler):
    """Route run_poller's httpx.post through a MockTransport handler."""
    transport = httpx.MockTransport(handler)

    def fake_post(url, **kwargs):
        with httpx.Client(transport=transport) as c:
            return c.post(url, **kwargs)

    monkeypatch.setattr(run_poller.httpx, "post", fake_post)


def test_list_runs_on_parses_runs(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["body"] = request.content
        return httpx.Response(200, json={"runs": [{"run_id": "r1", "status": "running"}]})

    _patch_post(monkeypatch, handler)
    runs = run_poller.list_runs_on("http://shim:8791", "sess-key")
    assert runs == [{"run_id": "r1", "status": "running"}]
    assert seen["url"].endswith("/api/list_runs")
    assert b"sess-key" in seen["body"], "session_key goes in the JSON body, not a header"


def test_list_runs_on_no_creds_skips_request(monkeypatch):
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return httpx.Response(200, json={"runs": []})

    _patch_post(monkeypatch, handler)
    assert run_poller.list_runs_on("", "key") == []
    assert run_poller.list_runs_on("http://x", "") == []
    assert called["n"] == 0, "no creds → no HTTP call at all"


def test_list_runs_on_http_error_returns_empty(monkeypatch):
    _patch_post(monkeypatch, lambda req: httpx.Response(500))
    assert run_poller.list_runs_on("http://x", "k") == []


def test_list_runs_unions_static_and_lease_shims(monkeypatch):
    # static shim returns r1; a broker lease shim returns r2 — list_runs unions them.
    monkeypatch.setenv("INFRA_SERVER_URL", "http://static")
    monkeypatch.setenv("INFRA_SESSION_KEY", "static-key")

    def handler(request: httpx.Request) -> httpx.Response:
        body = request.content.decode()
        if "static-key" in body:
            return httpx.Response(200, json={"runs": [{"run_id": "r1", "status": "running"}]})
        return httpx.Response(200, json={"runs": [{"run_id": "r2", "status": "succeeded"}]})

    _patch_post(monkeypatch, handler)
    # fake a broker lease shim
    monkeypatch.setattr(run_poller.gpu_broker, "active_lease_shims",
                        lambda: [{"run_id": "x", "INFRA_SERVER_URL": "http://lease",
                                  "INFRA_SESSION_KEY": "lease-key"}])
    ids = {r["run_id"] for r in run_poller.list_runs()}
    assert ids == {"r1", "r2"}, "static + lease-shim runs are unioned"


def test_list_runs_dedups_by_run_id(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://static")
    monkeypatch.setenv("INFRA_SESSION_KEY", "static-key")
    _patch_post(monkeypatch, lambda req: httpx.Response(
        200, json={"runs": [{"run_id": "dup", "status": "running"}]}))
    # both static and the lease shim return the SAME run_id → deduped to one
    monkeypatch.setattr(run_poller.gpu_broker, "active_lease_shims",
                        lambda: [{"run_id": "x", "INFRA_SERVER_URL": "http://lease",
                                  "INFRA_SESSION_KEY": "lease-key"}])
    runs = run_poller.list_runs()
    assert len([r for r in runs if r["run_id"] == "dup"]) == 1


def test_run_poller_end_to_end_terminal(monkeypatch):
    monkeypatch.setenv("INFRA_SERVER_URL", "http://static")
    monkeypatch.setenv("INFRA_SESSION_KEY", "static-key")
    _patch_post(monkeypatch, lambda req: httpx.Response(200, json={"runs": [
        {"run_id": "r1", "status": "succeeded", "workdir": "omc/proj/iter_001"},
    ]}))
    monkeypatch.setattr(run_poller.gpu_broker, "active_lease_shims", lambda: [])
    poller = run_poller.RunPoller(find_marker=lambda rid: f"omc/{rid}/iter_001")
    assert poller.poll("proj") == [
        {"run_id": "r1", "status": "succeeded", "workdir": "omc/proj/iter_001"}]
    assert poller.all_terminal("proj", ["r1"]) is True
