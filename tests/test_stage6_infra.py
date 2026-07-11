"""Tests for stage6_infra — receipt parsing, packaged-script resolution, and the
never-raise submit contract (no live infra required)."""
from ucl_gpu_infra import stage6_infra as s6


def test_parse_receipt_inline_backtick_cmd():
    text = "- **Smoke run**: `cd exp && python run.py --smoke`\n"
    r = s6.parse_receipt(text, project_id="p1", iteration="iter_001")
    assert r.smoke_cmd == "cd exp && python run.py --smoke"
    assert r.ok
    assert r.remote_dest == "omc/p1/iter_001"  # falls back when receipt names none


def test_parse_receipt_whole_line_python3():
    # python3 must match (the old \b-after-python silently dropped it).
    text = "## Full\npython3 train.py --epochs 10\n"
    r = s6.parse_receipt(text)
    assert r.full_cmd == "python3 train.py --epochs 10"


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


def test_skip_push_honored_from_submission_env(monkeypatch):
    """INFRA_SKIP_PUSH passed via env= must control the subprocess, not just
    the parent os.environ (cubic PR#3 P2)."""
    import ucl_gpu_infra.stage6_infra as s6
    calls = []

    def fake_run(args, env, timeout=320.0):
        calls.append(args)
        return 0, '{"run_id": "run_x"}'

    monkeypatch.setattr(s6, "_run", fake_run)
    monkeypatch.delenv("INFRA_SKIP_PUSH", raising=False)
    monkeypatch.delenv("STAGE6_SKIP_PUSH", raising=False)
    r = s6.Receipt(smoke_cmd="python x.py", code_dir="/tmp/c", remote_dest="d")
    s6.submit(r, {"fast_push_code.sh": "/y", "fast_submit.sh": "/x"},
              config_path="", env={"INFRA_SERVER_URL": "u", "INFRA_SESSION_KEY": "k",
                                    "INFRA_SKIP_PUSH": "push"})
    # push was skipped -> only the submit call ran, no push call
    assert not any("fast_push_code.sh" in a for a in calls), "push not skipped via env="
