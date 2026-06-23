"""Client for the dynamic GPU broker (ucl-infra/claim-gpu.sh / release-gpu.sh).

Phase 2 of the GPU-broker work: give each Stage-6 experiment run its OWN GPU so
concurrent runs (and the two app worktrees) never share. The engine calls
``claim()`` just before submitting an experiment and ``release()`` when the run
reaches a terminal state.

This is a thin, FAIL-OPEN shell wrapper:
- If the broker is disabled (``GPU_BROKER`` unset/0) or its scripts aren't found,
  ``claim()`` returns ``None`` and the caller falls back to the static
  ``INFRA_SERVER_URL`` — i.e. today's shared-shim behaviour. Nothing breaks.
- ``release()`` never raises and is safe to call for an unknown run_id.

The broker scripts (``claim-gpu.sh`` / ``release-gpu.sh`` / ``gpu-leases.sh``)
live on the deployment box; point at them with the ``UCL_INFRA_DIR`` env var
(default ``./ucl-infra`` relative to the working directory). When the dir or its
scripts are absent, claim() returns "fallback" and the caller uses the static
``INFRA_SERVER_URL`` shim — so an unset/wrong path degrades gracefully, never
crashes.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from loguru import logger

# Default is relative + generic; the real broker location is supplied via
# UCL_INFRA_DIR on the deployment box. A missing dir degrades to "fallback".
_DEFAULT_DIR = "./ucl-infra"


def _enabled() -> bool:
    # On-demand GPU broker is now the DEFAULT (each experiment claims its own GPU
    # and spins up a per-run shim, instead of an always-on static shim holding a
    # GPU for the whole deployment). Unset/empty -> ON. Only an explicit falsey
    # value disables it, falling back to the static INFRA_SERVER_URL shim.
    # When the broker is on but its scripts aren't installed, claim() returns
    # "fallback" and the caller still uses the static shim — so this is safe even
    # off-box. Normalize case so FALSE/False/No/Off all disable it.
    return os.environ.get("GPU_BROKER", "").strip().lower() not in ("0", "false", "no", "off")


def _dir() -> Path:
    return Path(os.environ.get("UCL_INFRA_DIR", _DEFAULT_DIR))


def _script(name: str) -> Optional[str]:
    p = _dir() / name
    return str(p) if p.exists() else None


# The broker scripts interpolate the run_id into shell command strings and lease
# paths, so claim-gpu.sh rejects anything outside [A-Za-z0-9._:-] (notably "/")
# as `invalid run_id`. Callers legitimately pass composite ids like
# "<project>/iter_001" (HC / Stage 6 use project_id which can carry the
# iteration), which would otherwise be rejected and silently fall back to the
# (now torn-down) static shim. Map to the broker's charset here so claim/release
# agree on the SAME key — sanitising in only one would leak leases.
_RUN_ID_SAFE = re.compile(r"[^A-Za-z0-9._:-]+")


def _safe_run_id(run_id: str) -> str:
    """Map an arbitrary run_id to the broker's charset, INJECTIVELY.

    A plain character-substitution is not collision-safe — "a/b" and "a_b" would
    both map to "a_b" and could then share or release each other's lease. So the
    key is ALWAYS ``<sanitised-prefix>.<hash-of-original>``: the hash is taken over
    the ORIGINAL id, so equal outputs imply equal originals (collision-resistant),
    while the same id always maps to the same key (claim and release stay in sync).

    The prefix is only for human-readable logs/paths; the hash carries identity.
    A single early-return for already-safe ids was NOT enough — a safe id that
    happened to equal another id's remapped output would cross-collide — so there
    is no passthrough branch: every id is hashed.

    Injectivity is preserved by the trailing hash, not by the prefix, so we can
    freely tidy the prefix: strip trailing "." (and collapse any "..") so the "."
    separator can't recreate a broker-rejected ".." when the prefix ends in a dot
    (e.g. "a." -> "a..<hash>"). The 12-hex digest never contains ".", so the only
    place ".." could form is that boundary, which this removes.
    """
    rid = run_id or "run"
    prefix = _RUN_ID_SAFE.sub("_", rid).replace("..", "_").rstrip(".") or "run"
    digest = hashlib.sha1(rid.encode("utf-8", "surrogatepass")).hexdigest()[:12]
    return f"{prefix}.{digest}"


# claim() outcome statuses (the second element of its return tuple):
#   "ok"          -> a lease dict is returned; use it.
#   "unavailable" -> broker is ON but NO GPU is free; the caller should PAUSE the
#                    run and notify the user (do NOT silently share a GPU).
#   "fallback"    -> broker is OFF / not installed / errored; the caller should
#                    proceed on the static shared shim (today's behaviour).
def claim(run_id: str, holder: str = "stage6"):
    """Claim a dedicated GPU for ``run_id``.

    Returns ``(lease_or_None, status)`` where status is one of "ok",
    "unavailable", "fallback" (see above). Idempotent: re-claiming a run returns
    its existing lease with status "ok".
    """
    if not _enabled():
        return None, "fallback"
    script = _script("claim-gpu.sh")
    if not script:
        logger.debug("[gpu-broker] claim-gpu.sh not found under {}; using static shim", _dir())
        return None, "fallback"
    run_id = _safe_run_id(run_id)  # broker rejects "/" etc.; keep claim/release in sync
    try:
        r = subprocess.run(
            ["bash", script, run_id, holder],
            capture_output=True, text=True, timeout=120,
        )
        out = (r.stdout or "").strip()
        data = json.loads(out.splitlines()[-1]) if out else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("[gpu-broker] claim errored for {} ({}); using static shim", run_id, exc)
        return None, "fallback"
    # "no free GPU available" is the broker working correctly but finding nothing
    # free — that's UNAVAILABLE (pause + notify), distinct from a real error.
    err = (data.get("error") or "").lower()
    if "no free gpu" in err:
        logger.warning("[gpu-broker] run {} has NO free GPU available — pausing for user", run_id)
        return None, "unavailable"
    # A usable lease needs BOTH the URL and the key. Validating only the URL let a
    # malformed/partial lease through, and env_for() would then KeyError on the
    # missing key — treat such a lease as a fallback, not a success.
    if data.get("error") or not data.get("INFRA_SERVER_URL") or not data.get("INFRA_SESSION_KEY"):
        logger.warning(
            "[gpu-broker] claim for {} returned an incomplete lease ({}); using static shim",
            run_id, data.get("error", "?"),
        )
        return None, "fallback"
    logger.info(
        "[gpu-broker] run {} claimed {} -> {}",
        run_id, data.get("box"), data["INFRA_SERVER_URL"],
    )
    return data, "ok"


def release(run_id: str) -> None:
    """Release ``run_id``'s GPU lease. Never raises; no-op if disabled/unknown."""
    if not _enabled():
        return
    script = _script("release-gpu.sh")
    if not script:
        return
    run_id = _safe_run_id(run_id)  # MUST match the sanitisation claim() applied
    try:
        r = subprocess.run(["bash", script, run_id], capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            logger.info("[gpu-broker] released lease for run {}", run_id)
        else:
            # Don't claim success when the script failed — that hides a stuck
            # lease (cubic). Log it loudly; gpu-leases.sh --prune is the backstop.
            logger.warning(
                "[gpu-broker] release script FAILED for {} (rc={}): {} — lease may be "
                "stuck until prune reaps it",
                run_id, r.returncode, (r.stderr or r.stdout or "").strip()[:200],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("[gpu-broker] release of {} errored ({}) — prune will reap it", run_id, exc)


def env_for(lease: Optional[dict], base: Optional[dict] = None) -> dict:
    """Build a subprocess env that points stage6_infra at the leased shim.
    With no lease, returns ``base`` (or os.environ) unchanged."""
    env = dict(base if base is not None else os.environ)
    # Only override when BOTH fields are present — use .get so a partial lease
    # can't KeyError here (claim() already filters those out, but be defensive).
    if lease and lease.get("INFRA_SERVER_URL") and lease.get("INFRA_SESSION_KEY"):
        env["INFRA_SERVER_URL"] = lease["INFRA_SERVER_URL"]
        env["INFRA_SESSION_KEY"] = lease["INFRA_SESSION_KEY"]
    return env


def lease_for(holder_run_id: str) -> Optional[dict]:
    """The shim coords for the lease claimed under ``holder_run_id`` (the value
    passed to :func:`claim`, e.g. an HC/Stage-6 ``project_id``), so a caller can
    read a run's terminal output from the SAME per-run shim it was submitted to.
    Returns ``{"INFRA_SERVER_URL","INFRA_SESSION_KEY",...}`` or ``None``."""
    key = _safe_run_id(holder_run_id)
    for shim in active_lease_shims():
        if shim.get("run_id") == key:
            return shim
    return None


def active_lease_shims() -> list[dict]:
    """Every currently-held lease's shim coordinates, for pollers that must
    reach the PER-RUN shim a run was submitted to (the on-demand broker gives
    each run its own shim/session — runs are NOT visible on the static shim).

    Returns a list of ``{"run_id", "INFRA_SERVER_URL", "INFRA_SESSION_KEY"}``
    (run_id is the broker lease key, i.e. the sanitised holder run_id). Empty
    on any failure — pollers degrade to the static shim. Never raises."""
    if not _enabled():
        return []
    script = _script("gpu-leases.sh")
    if not script:
        return []
    try:
        r = subprocess.run(["bash", script, "--json"], capture_output=True, text=True, timeout=30)
        data = json.loads((r.stdout or "").strip() or "{}")
    except Exception as exc:  # noqa: BLE001
        logger.debug("[gpu-broker] active_lease_shims listing failed ({})", exc)
        return []
    out: list[dict] = []
    for rid, lease in (data.items() if isinstance(data, dict) else []):
        if not isinstance(lease, dict):
            continue  # one malformed record must not break lease lookup/finalization
        port = lease.get("tunnel_port")
        key = lease.get("session_key")
        if not port or not key:
            continue
        out.append({
            "run_id": rid,
            "INFRA_SERVER_URL": f"http://127.0.0.1:{port}",
            "INFRA_SESSION_KEY": key,
        })
    return out
