"""Shared secret loading — one box-local file, referenced by every consumer.

The real secrets (LLM API key, infra session key, …) live in exactly ONE
git-ignored file on the box; both ``memento-research`` and
``continual-auto-research`` load it through this resolver instead of each keeping
its own copy. The committed ``secrets.env.template`` documents the shape with no
values.

Resolution order for the secret file path:
  1. an explicit ``path`` argument
  2. ``$UCL_GPU_INFRA_SECRETS``
  3. ``~/.config/ucl-gpu-infra/secrets.env`` (the canonical default)

:func:`load_secrets` parses simple ``KEY=value`` lines (``#`` comments, blank
lines, optional ``export``, and surrounding quotes are handled) and sets them in
``os.environ`` — by default WITHOUT overwriting vars already present, so an
explicit env still wins. Returns a report of what was set / skipped / missing so
callers can surface readiness without printing secret values.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from loguru import logger

DEFAULT_PATH = "~/.config/ucl-gpu-infra/secrets.env"


def secret_path(path: Optional[str] = None) -> Path:
    """The resolved secret-file path (may not exist)."""
    raw = path or os.environ.get("UCL_GPU_INFRA_SECRETS") or DEFAULT_PATH
    return Path(raw).expanduser()


def parse_env_file(text: str) -> dict[str, str]:
    """Parse ``KEY=value`` lines. Ignores comments/blanks; strips an optional
    leading ``export`` and surrounding single/double quotes on the value."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("export "):
            s = s[len("export "):].lstrip()
        if "=" not in s:
            continue
        k, v = s.split("=", 1)
        k = k.strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
            v = v[1:-1]
        if k:
            out[k] = v
    return out


def load_secrets(path: Optional[str] = None, *, override: bool = False) -> dict:
    """Load the shared secret file into ``os.environ``.

    ``override=False`` (default): only set vars not already in the environment,
    so an explicitly-set env (e.g. a profile sourced by a launcher) wins. Returns
    ``{"path", "exists", "set": [...names...], "skipped": [...], "empty": [...]}``
    — names only, never values. Never raises; a missing file is reported, not an
    error (a consumer may legitimately run with env set another way).
    """
    p = secret_path(path)
    report = {"path": str(p), "exists": p.is_file(), "set": [], "skipped": [], "empty": []}
    if not p.is_file():
        logger.debug("[secrets] no secret file at {} (env may be set another way)", p)
        return report
    try:
        kv = parse_env_file(p.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 — never crash on a malformed/locked file
        logger.warning("[secrets] could not read {}: {}", p, exc)
        return report
    for k, v in kv.items():
        if v == "":
            report["empty"].append(k)  # present in template but unfilled
            continue
        if not override and k in os.environ and os.environ[k] != "":
            report["skipped"].append(k)
            continue
        os.environ[k] = v
        report["set"].append(k)
    logger.info("[secrets] loaded {} ({} set, {} pre-set, {} empty)",
                p, len(report["set"]), len(report["skipped"]), len(report["empty"]))
    return report
