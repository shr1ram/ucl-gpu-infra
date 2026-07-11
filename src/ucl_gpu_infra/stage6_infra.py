"""Experiment submission to the GPU-infra shim.

Push a code dir and submit a run via the shim's scripts (fast_push_code.sh /
fast_submit.sh / fast_query_exp_status.sh), with credentials from
INFRA_SERVER_URL / INFRA_SESSION_KEY (env). A Receipt is a plain data carrier
(command, code dir, remote dest, plus strict-provenance fields); callers build
it directly. submit() and query_status() never raise — they return structured
results so a caller can HOLD when infra is unavailable.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

# The experiment-infra scripts ship as package data alongside this module (see
# pyproject [tool.setuptools.package-data]). Used as the final fallback by
# find_infra_scripts when EXPERIMENT_INFRA_SCRIPTS is not set.
_PACKAGED_INFRA_SCRIPTS = Path(__file__).resolve().parent / "scripts"


@dataclass
class Receipt:
    smoke_cmd: str = ""
    full_cmd: str = ""
    code_dir: str = ""       # local dir containing the experiment code
    remote_dest: str = ""    # remote workspace dest
    # strict_provenance fields the server requires; forwarded to fast_submit
    # when set (empty -> flag omitted -> server default / rejection surfaces)
    gpu: str = ""
    data_version: str = ""
    random_seed: str = ""    # str: "" means "unset", distinct from seed 0
    git_commit: str = ""
    estimated_hours: str = ""
    use_spot: str = ""       # "" | "true" | "false"
    retry_until_up: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.smoke_cmd or self.full_cmd)



@dataclass
class SubmitResult:
    ok: bool
    run_id: str = ""
    kind: str = ""           # "smoke" | "full"
    raw: dict = field(default_factory=dict)
    error: str = ""


def _creds(env: dict | None = None) -> tuple[str, str]:
    # Read from the passed env (the GPU broker points these at the per-run leased
    # shim) and fall back to the process environ (the static shim). Reading
    # os.environ unconditionally would ignore the lease and use the wrong shim.
    src = env if env is not None else os.environ
    return src.get("INFRA_SERVER_URL", ""), src.get("INFRA_SESSION_KEY", "")


def find_infra_scripts(skill_root: str | None = None) -> dict:
    """Locate the experiment-infra scripts. ``skill_root`` overrides; else use the
    env ``EXPERIMENT_INFRA_SCRIPTS``, then fall back to the scripts shipped as
    package data alongside this module, so submit works on any install with zero
    env wiring. Returns {} if not found (caller degrades)."""
    cands: list[Path] = []
    if skill_root:
        cands.append(Path(skill_root))
    env = os.environ.get("EXPERIMENT_INFRA_SCRIPTS")
    if env:
        cands.append(Path(env))
    # Final fallback: the scripts shipped as package data (see pyproject
    # package-data). Without this, resolution would depend on
    # EXPERIMENT_INFRA_SCRIPTS being set on every deploy.
    cands.append(_PACKAGED_INFRA_SCRIPTS)
    names = {"fast_push_code.sh", "fast_submit.sh", "fast_query_exp_status.sh"}
    for base in cands:
        if base.is_dir() and names.issubset({p.name for p in base.glob("*.sh")}):
            return {n: str(base / n) for n in names}
    return {}


def _run(args: list[str], env: dict, timeout: float = 320.0) -> tuple[int, str]:
    try:
        r = subprocess.run(args, capture_output=True, text=True, env=env, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, f"{type(exc).__name__}: {exc}"


def _extract_run_id(out: str) -> str:
    import json as _json
    try:
        d = _json.loads(out)
        if isinstance(d, dict) and d.get("run_id"):
            return str(d["run_id"])
    except Exception as exc:  # noqa: BLE001
        logger.debug("[infra-submit] run_id JSON parse failed ({}); trying regex", exc)
    m = re.search(r'"run_id"\s*:\s*"([^"]+)"', out) or re.search(r"\b(run_[0-9a-f]{8,})\b", out)
    return m.group(1) if m else ""


def submit(receipt: Receipt, scripts: dict, config_path: str, kind: str = "smoke",
           env: dict | None = None) -> SubmitResult:
    """Push the code and submit the (smoke|full) run via the skill's scripts.

    Parameterised: pushes ``receipt.code_dir`` to ``receipt.remote_dest`` and
    submits ``receipt.smoke_cmd`` / ``receipt.full_cmd``. Never raises.
    """
    url, key = _creds(env)
    if not (url and key):
        return SubmitResult(ok=False, kind=kind, error="missing INFRA_SERVER_URL / INFRA_SESSION_KEY")
    if not scripts:
        return SubmitResult(ok=False, kind=kind, error="experiment-infra scripts not found")
    cmd = receipt.full_cmd if kind == "full" else receipt.smoke_cmd
    if not cmd:
        return SubmitResult(ok=False, kind=kind, error=f"no {kind} command in receipt")
    run_env = dict(env or os.environ, INFRA_SERVER_URL=url, INFRA_SESSION_KEY=key)

    _skip_push = os.environ.get("INFRA_SKIP_PUSH") or os.environ.get("STAGE6_SKIP_PUSH") or ""
    if receipt.code_dir and receipt.remote_dest and "push" not in _skip_push:
        rc, out = _run(["bash", scripts["fast_push_code.sh"], receipt.code_dir, receipt.remote_dest], run_env)
        if rc != 0:
            return SubmitResult(ok=False, kind=kind, error=f"push failed: {out[:200]}")

    # Run in the dir the code was pushed to (receipt.remote_dest), so the
    # command's relative paths resolve against the pushed code, not the shim
    # workspace root. Without this the run executes one dir too high and can't
    # find its entrypoint.
    submit_args = ["bash", scripts["fast_submit.sh"], "--config", config_path, "--cmd", cmd]
    if receipt.remote_dest:
        submit_args += ["--workdir", receipt.remote_dest]
    # forward provenance/backend fields the strict server requires (P0):
    # the documented submit() path must supply them, not only the CLI
    for flag, val in (("--gpu", receipt.gpu),
                      ("--data-version", receipt.data_version),
                      ("--random-seed", receipt.random_seed),
                      ("--git-commit", receipt.git_commit),
                      ("--estimated-hours", receipt.estimated_hours),
                      ("--use-spot", receipt.use_spot)):
        if val:
            submit_args += [flag, str(val)]
    if receipt.retry_until_up:
        submit_args += ["--retry-until-up"]
    rc, out = _run(submit_args, run_env)
    rid = _extract_run_id(out)
    if rc != 0 or not rid:
        return SubmitResult(ok=False, kind=kind, error=f"submit failed (rc={rc}): {out[:200]}")
    return SubmitResult(ok=True, run_id=rid, kind=kind)


def query_status(run_id: str, scripts: dict, env: dict | None = None) -> dict:
    """Return {status, log_tail, ...} for a run, or {} on failure. Never raises."""
    url, key = _creds(env)
    if not (url and key and scripts):
        return {}
    run_env = dict(env or os.environ, INFRA_SERVER_URL=url, INFRA_SESSION_KEY=key)
    rc, out = _run(["bash", scripts["fast_query_exp_status.sh"], run_id], run_env, timeout=60)
    import json as _json
    try:
        d = _json.loads(out)
    except Exception:  # noqa: BLE001
        return {}
    if isinstance(d, dict) and d.get("run_id"):
        return d
    for r in (d.get("runs", []) if isinstance(d, dict) else []):
        if r.get("run_id") == run_id:
            return r
    return {}
