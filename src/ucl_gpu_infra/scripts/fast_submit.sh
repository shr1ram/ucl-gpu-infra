#!/usr/bin/env bash
# fast_submit.sh --config <config_path> --cmd <run_command> — submit an
# experiment run to the experiment-infra shim (/api/submit) and print its JSON
# (incl. run_id, which the engine extracts).
#
# Reads INFRA_SERVER_URL / INFRA_SESSION_KEY from the env (the GPU broker points
# these at the leased shim). --config is optional context passed through to the
# shim's metadata; the shim runs --cmd in its workspace on its own GPU box.
set -euo pipefail
CONFIG=""; CMD=""; WORKDIR=""
while [ $# -gt 0 ]; do
  case "$1" in
    --config)  CONFIG="${2:-}"; shift 2 ;;
    --cmd)     CMD="${2:-}"; shift 2 ;;
    --workdir) WORKDIR="${2:-}"; shift 2 ;;
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
SHIM_KEY="$KEY" SHIM_CMD="$CMD" SHIM_CFG="$cfg_json" SHIM_WD="$WORKDIR" python3 -c '
import json, os
cfg = os.environ.get("SHIM_CFG", "{}")
try:
    cfg = json.loads(cfg)
except Exception:
    cfg = {"raw": cfg}
print(json.dumps({
    "session_key": os.environ["SHIM_KEY"],
    "run_command": os.environ["SHIM_CMD"],
    "workdir": os.environ.get("SHIM_WD", ""),
    "config": cfg,
}))
' | curl -fsS -m 60 -X POST "${URL%/}/api/submit" \
  -H 'Content-Type: application/json' --data @-
