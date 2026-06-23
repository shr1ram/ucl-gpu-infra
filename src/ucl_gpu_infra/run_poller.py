"""RunPoller — the OMC-free half of the old ``run_tracker``.

The original ``run_tracker`` tangled two responsibilities: (1) a generic poller
that pulls ``/api/list_runs`` off the experiment shims, dedups, filters runs by a
workspace marker, and classifies terminal status; and (2) OMC-specific glue that
persisted results onto ``pipeline_state.yaml`` and routed finalize back into the
``pipeline_engine``. Only (1) is reusable across codebases — it has no knowledge
of projects, iterations, or any app's state model. This module is that half.

Consumers supply their own glue: poll with :func:`list_runs`, filter the runs
they own with :func:`filter_runs_by_workdir`, and decide terminal-ness with
:func:`is_terminal` — then do whatever their app needs (score, persist, advance).

Runs are unioned across the static shim (``INFRA_SERVER_URL``) AND every
currently-held GPU-broker lease's per-run shim, because the on-demand broker
gives each run its own shim/session — a broker-submitted run is NOT visible on
the static shim. Never raises; degrades to ``[]`` on failure.
"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

import httpx
from loguru import logger

from . import gpu_broker

# Statuses that mean a run is over. Anything else (running / queued / unknown /
# missing-from-listing) is treated as still alive.
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "cancelled", "rejected"})


def is_terminal(run: dict[str, Any]) -> bool:
    """True if a run record's status is terminal. A missing status is NOT
    terminal (the run may simply have aged out of the listing while alive)."""
    return (run.get("status") or "").strip().lower() in TERMINAL_STATUSES


def list_runs_on(url: str, key: str, limit: int = 100) -> list[dict[str, Any]]:
    """``/api/list_runs`` on ONE shim (url+key). ``[]`` on any failure.

    Auth is via ``session_key`` in the JSON body, not an ``Authorization``
    header — the infra silently treats a Bearer-authed request as anonymous and
    returns an empty ``runs`` array.
    """
    if not url or not key:
        return []
    try:
        resp = httpx.post(
            f"{url.rstrip('/')}/api/list_runs",
            headers={"Content-Type": "application/json"},
            json={"session_key": key, "limit": limit},
            timeout=20.0,
        )
        resp.raise_for_status()
        data = resp.json()
        runs = data.get("runs", [])
        return runs if isinstance(runs, list) else []
    except (httpx.HTTPError, ValueError) as exc:
        logger.debug("[run-poller] /api/list_runs failed on {}: {}", url, exc)
        return []


def list_runs(limit: int = 100) -> list[dict[str, Any]]:
    """Every run visible to this client, unioned + deduped by ``run_id`` across:

    1. the static shim (``INFRA_SERVER_URL`` / ``INFRA_SESSION_KEY`` env), and
    2. every currently-held GPU-broker lease's per-run shim.

    Returns ``[]`` on total failure. Never raises.
    """
    seen: dict[str, dict[str, Any]] = {}

    def _absorb(runs: list[dict[str, Any]]) -> None:
        for r in runs:
            if not isinstance(r, dict):
                continue  # one malformed entry must not abort the tick
            rid = r.get("run_id")
            if rid and rid not in seen:
                seen[rid] = r

    _absorb(list_runs_on(os.environ.get("INFRA_SERVER_URL", ""),
                         os.environ.get("INFRA_SESSION_KEY", ""), limit))
    try:
        for shim in gpu_broker.active_lease_shims():
            _absorb(list_runs_on(shim["INFRA_SERVER_URL"], shim["INFRA_SESSION_KEY"], limit))
    except Exception as exc:  # noqa: BLE001 — never poison a polling loop
        logger.debug("[run-poller] lease-shim poll skipped: {}", exc)
    return list(seen.values())


def filter_runs_by_workdir(runs: list[dict[str, Any]], marker: str) -> list[dict[str, Any]]:
    """Runs whose ``run_command`` OR ``workdir`` contains ``marker``.

    The agent runbook prefixes the command with ``cd <marker>/...`` so the marker
    lives in ``run_command``; the engine-driven submit passes ``--workdir
    <marker>`` and a clean ``cd <subdir>`` command, so the marker is in
    ``workdir`` and absent from ``run_command``. Checking both catches either
    submission style.
    """
    if not marker:
        return []
    return [
        r for r in runs
        if marker in (r.get("run_command") or "") or marker in (r.get("workdir") or "")
    ]


class RunPoller:
    """Thin convenience wrapper for the poll → filter → terminal loop.

    ``find_marker`` maps a caller's run id to the workdir marker that identifies
    its runs (e.g. ``lambda run_id: f"omc/{run_id}"``). One tick returns the
    runs owned by that id, annotated only with what the infra reported — the
    caller decides what terminal means for its app.
    """

    def __init__(self, find_marker: Optional[Callable[[str], str]] = None):
        self._find_marker = find_marker or (lambda rid: rid)

    def poll(self, owner_id: str, limit: int = 100) -> list[dict[str, Any]]:
        """Runs owned by ``owner_id`` this tick (may be empty)."""
        return filter_runs_by_workdir(list_runs(limit), self._find_marker(owner_id))

    def all_terminal(self, owner_id: str, expected_run_ids: list[str], limit: int = 100) -> bool:
        """True iff every id in ``expected_run_ids`` is present AND terminal.

        A run that has aged out of the listing counts as NOT terminal (it may
        still be alive), so a partial listing never falsely reports "all done".
        """
        if not expected_run_ids:
            return False
        by_id = {r.get("run_id"): r for r in self.poll(owner_id, limit)}
        return all(rid in by_id and is_terminal(by_id[rid]) for rid in expected_run_ids)
