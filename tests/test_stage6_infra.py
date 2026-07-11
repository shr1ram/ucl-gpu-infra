"""Tests for stage6_infra — receipt parsing, packaged-script resolution, and the
never-raise submit contract (no live infra required)."""
from ucl_gpu_infra import stage6_infra as s6


def test_find_infra_scripts_resolves_packaged_data():
    # With no env override, the scripts shipped as package data must resolve.
    scripts = s6.find_infra_scripts()
    assert set(scripts) == {"fast_push_code.sh", "fast_submit.sh", "fast_query_exp_status.sh"}
    for path in scripts.values():
        assert path.endswith(".sh")


def test_submit_without_creds_is_structured_failure():
    r = s6.submit(s6.Receipt(smoke_cmd="python x.py"), {"fast_submit.sh": "/x"},
                  "cfg", env={})  # empty env → no creds
    assert r.ok is False and "INFRA_SERVER_URL" in r.error


def test_submit_without_scripts_is_structured_failure():
    r = s6.submit(s6.Receipt(smoke_cmd="python x.py"), {},
                  "cfg", env={"INFRA_SERVER_URL": "http://x", "INFRA_SESSION_KEY": "k"})
    assert r.ok is False and "scripts" in r.error


def test_query_status_without_creds_returns_empty():
    assert s6.query_status("run_x", {}, env={}) == {}


def test_submit_forwards_provenance_to_fast_submit(monkeypatch):
    """The documented submit() path must send strict_provenance flags (PR#2 P0)."""
    import ucl_gpu_infra.stage6_infra as s6
    captured = {}

    def fake_run(args, env, timeout=320.0):
        captured["args"] = args
        return 0, '{"run_id": "run_abc123"}'

    monkeypatch.setattr(s6, "_run", fake_run)
    r = s6.Receipt(smoke_cmd="python x.py", code_dir="", remote_dest="dest",
                   gpu="H100:1", data_version="dv", random_seed="0",
                   git_commit="local", estimated_hours="0.05", use_spot="false",
                   retry_until_up=True)
    res = s6.submit(r, {"fast_submit.sh": "/x", "fast_push_code.sh": "/y"},
                    config_path="", kind="smoke",
                    env={"INFRA_SERVER_URL": "u", "INFRA_SESSION_KEY": "k"})
    assert res.ok and res.run_id == "run_abc123"
    a = captured["args"]
    for flag, val in (("--gpu", "H100:1"), ("--data-version", "dv"),
                      ("--random-seed", "0"), ("--git-commit", "local"),
                      ("--estimated-hours", "0.05"), ("--use-spot", "false")):
        assert flag in a and a[a.index(flag) + 1] == val, flag
    assert "--retry-until-up" in a
