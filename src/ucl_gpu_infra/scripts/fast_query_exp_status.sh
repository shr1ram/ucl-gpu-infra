#!/usr/bin/env bash
# fast_query_exp_status.sh <run_id> — query a run's status on the experiment-infra
# shim (/api/status) and print its JSON (status, log_tail, exit_code, ...).
#
# Reads INFRA_SERVER_URL / INFRA_SESSION_KEY from the env (the GPU broker points
# these at the leased shim that owns the run).
set -euo pipefail
RUN_ID="${1:?usage: fast_query_exp_status.sh <run_id>}"
URL="${INFRA_SERVER_URL:?INFRA_SERVER_URL not set}"
KEY="${INFRA_SESSION_KEY:?INFRA_SESSION_KEY not set}"

# Build the JSON body with python (safe quoting) and feed it via STDIN, not argv —
# a session_key in the curl command line leaks via the process list (cubic).
SHIM_KEY="$KEY" SHIM_RID="$RUN_ID" python3 -c '
import json, os
print(json.dumps({"session_key": os.environ["SHIM_KEY"], "run_id": os.environ["SHIM_RID"]}))
' | curl -fsS -m 30 -X POST "${URL%/}/api/status" \
  -H 'Content-Type: application/json' --data @-
