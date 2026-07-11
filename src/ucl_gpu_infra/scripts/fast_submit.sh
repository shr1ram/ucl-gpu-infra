#!/usr/bin/env bash
# fast_submit.sh --cmd <run_command> [--config <p>] [--workdir <d>] [--gpu <g>]
#   [--data-version <v>] [--random-seed <n>] [--git-commit <sha>]
#   [--estimated-hours <h>] — submit an
# experiment run to the experiment-infra shim (/api/submit) and print its JSON
# (incl. run_id, which the engine extracts).
#
# Reads INFRA_SERVER_URL / INFRA_SESSION_KEY from the env (the GPU broker points
# these at the leased shim). --config is optional context passed through to the
# shim's metadata; the shim runs --cmd in its workspace on its own GPU box.
set -euo pipefail
CONFIG=""; CMD=""; WORKDIR=""
GPU=""; DATA_VERSION=""; RANDOM_SEED=""; GIT_COMMIT=""; ESTIMATED_HOURS=""; USE_SPOT=""; RETRY_UNTIL_UP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config)          CONFIG="${2:-}"; shift 2 ;;
    --cmd)             CMD="${2:-}"; shift 2 ;;
    --workdir)         WORKDIR="${2:-}"; shift 2 ;;
    --gpu)             GPU="${2:-}"; shift 2 ;;
    --data-version)    DATA_VERSION="${2:-}"; shift 2 ;;
    --random-seed)     RANDOM_SEED="${2:-}"; shift 2 ;;
    --git-commit)      GIT_COMMIT="${2:-}"; shift 2 ;;
    --estimated-hours) ESTIMATED_HOURS="${2:-}"; shift 2 ;;
    --use-spot)        USE_SPOT="${2:-}"; shift 2 ;;
    --retry-until-up)  RETRY_UNTIL_UP="${2:-}"; shift 2 ;;
    *) shift ;;
  esac
done
URL="${INFRA_SERVER_URL:?INFRA_SERVER_URL not set}"
KEY="${INFRA_SESSION_KEY:?INFRA_SESSION_KEY not set}"
[ -n "$CMD" ] || { echo '{"error":"--cmd is required"}' >&2; exit 1; }

# Read an optional config file's contents into the metadata (best-effort).
cfg_json='{}'
if [ -n "$CONFIG" ] && [ -f "$CONFIG" ]; then
  cfg_json=$(cat "$CONFIG")
fi

# Build the JSON body safely with python (handles quoting in the command).
# Build the JSON body with python (safe quoting) and feed it to curl via STDIN,
# not argv — a session_key on the curl command line leaks via the process list
# (cubic).
SHIM_KEY="$KEY" SHIM_CMD="$CMD" SHIM_CFG="$cfg_json" SHIM_WD="$WORKDIR" \
SHIM_GPU="$GPU" SHIM_DV="$DATA_VERSION" SHIM_SEED="$RANDOM_SEED" \
SHIM_GIT="$GIT_COMMIT" SHIM_HOURS="$ESTIMATED_HOURS" SHIM_SPOT="$USE_SPOT" SHIM_RETRY="$RETRY_UNTIL_UP" python3 -c '
import json, os
cfg = os.environ.get("SHIM_CFG", "{}")
try:
    cfg = json.loads(cfg)
except Exception:
    cfg = {"raw": cfg}
body = {
    "session_key": os.environ["SHIM_KEY"],
    "run_command": os.environ["SHIM_CMD"],
    "workdir": os.environ.get("SHIM_WD", ""),
    "config": cfg,
}
if os.environ.get("SHIM_GPU"):   body["gpu"] = os.environ["SHIM_GPU"]
if os.environ.get("SHIM_DV"):    body["data_version"] = os.environ["SHIM_DV"]
if os.environ.get("SHIM_SEED"):  body["random_seed"] = int(os.environ["SHIM_SEED"])
if os.environ.get("SHIM_GIT"):   body["git_commit"] = os.environ["SHIM_GIT"]
if os.environ.get("SHIM_HOURS"): body["estimated_hours"] = float(os.environ["SHIM_HOURS"])
if os.environ.get("SHIM_SPOT"):  body["use_spot"] = os.environ["SHIM_SPOT"].lower() in ("1","true","yes")
if os.environ.get("SHIM_RETRY"): body["config"] = {**body.get("config", {}), "retry_until_up": os.environ["SHIM_RETRY"].lower() in ("1","true","yes")}
print(json.dumps(body))
' | curl -fsS -m 60 -X POST "${URL%/}/api/submit" \
  -H 'Content-Type: application/json' --data @-
