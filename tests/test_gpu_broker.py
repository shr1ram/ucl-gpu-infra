"""Tests for gpu_broker — the fail-open shell wrapper + run_id safety.

No live broker is required: with the scripts absent (or GPU_BROKER off) claim()
returns the documented fallback, and the run_id sanitiser is pure.
"""
import pytest

from ucl_gpu_infra import gpu_broker as b


def test_disabled_broker_falls_back(monkeypatch):
    monkeypatch.setenv("GPU_BROKER", "0")
    lease, status = b.claim("run-1")
    assert lease is None and status == "fallback"


def test_enabled_but_no_scripts_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv("GPU_BROKER", "1")
    monkeypatch.setenv("UCL_INFRA_DIR", str(tmp_path))  # empty dir, no claim-gpu.sh
    lease, status = b.claim("run-1")
    assert lease is None and status == "fallback"


def test_release_never_raises(monkeypatch, tmp_path):
    monkeypatch.setenv("GPU_BROKER", "1")
    monkeypatch.setenv("UCL_INFRA_DIR", str(tmp_path))
    b.release("anything")  # no script → silent no-op, must not raise


def test_safe_run_id_is_injective_and_charset_clean():
    # "/" and other rejected chars are mapped out, and the hash makes collisions
    # between distinct ids astronomically unlikely (and equal ids map equally).
    a = b._safe_run_id("proj/iter_001")
    c = b._safe_run_id("proj_iter_001")
    assert "/" not in a
    assert a != c, "distinct ids must not collide (the trailing hash guarantees it)"
    assert b._safe_run_id("proj/iter_001") == a, "same id → same key (claim/release sync)"


def test_safe_run_id_no_double_dot():
    # ".." is broker-rejected; the sanitiser must never emit it.
    assert ".." not in b._safe_run_id("a..b")
    assert ".." not in b._safe_run_id("a.")


def test_env_for_with_no_lease_is_passthrough():
    base = {"FOO": "bar"}
    assert b.env_for(None, base=base) == base


def test_env_for_overrides_only_with_complete_lease():
    base = {"FOO": "bar"}
    out = b.env_for({"INFRA_SERVER_URL": "http://x", "INFRA_SESSION_KEY": "k"}, base=base)
    assert out["INFRA_SERVER_URL"] == "http://x" and out["INFRA_SESSION_KEY"] == "k"
    # a partial lease must NOT override (defensive against KeyError downstream)
    assert "INFRA_SERVER_URL" not in b.env_for({"INFRA_SERVER_URL": "http://x"}, base={})
