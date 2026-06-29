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


def test_parse_receipt_inline_env_prefix_whole_line():
    # A hill-climbing proposer sets the candidate hyperparameter via an inline
    # env assignment. The env prefix must NOT drop the command (it used to: the
    # interpreter token got pushed past the match, so every iteration silently
    # re-ran the default and the loop could never climb).
    text = "cd exp && LR=0.05 python train.py\n"
    r = s6.parse_receipt(text)
    assert r.smoke_cmd == "cd exp && LR=0.05 python train.py"


def test_parse_receipt_inline_env_prefix_backtick_bullet():
    text = "- **Smoke run**: `cd exp && LR=0.05 python train.py`\n"
    r = s6.parse_receipt(text, project_id="p1")
    assert r.smoke_cmd == "cd exp && LR=0.05 python train.py"


def test_parse_receipt_multiple_env_assignments():
    text = "BATCH=32 LR=0.1 python3 train.py\n"
    r = s6.parse_receipt(text)
    assert r.smoke_cmd == "BATCH=32 LR=0.1 python3 train.py"


def test_parse_receipt_no_env_still_matches():
    # Regression guard: the env-prefix addition must not break the plain case.
    text = "cd exp && python train.py\n"
    r = s6.parse_receipt(text)
    assert r.smoke_cmd == "cd exp && python train.py"


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
