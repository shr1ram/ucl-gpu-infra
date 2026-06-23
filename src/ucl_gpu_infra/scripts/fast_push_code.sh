#!/usr/bin/env bash
# fast_push_code.sh <code_dir> <remote_dest> — push a code directory to the
# experiment-infra shim's workspace (gzip tar over /api/push_codebase).
#
# Reads INFRA_SERVER_URL / INFRA_SESSION_KEY from the environment (the engine,
# via the GPU broker, points these at the leased shim). Prints the shim's JSON.
set -euo pipefail
CODE_DIR="${1:?usage: fast_push_code.sh <code_dir> <remote_dest>}"
REMOTE_DEST="${2:?usage: fast_push_code.sh <code_dir> <remote_dest>}"
URL="${INFRA_SERVER_URL:?INFRA_SERVER_URL not set}"
KEY="${INFRA_SESSION_KEY:?INFRA_SESSION_KEY not set}"

[ -d "$CODE_DIR" ] || { echo "{\"error\":\"code_dir not found: $CODE_DIR\"}" >&2; exit 1; }

# URL-encode the query params so special chars in the key/dest can't corrupt the
# request. The session_key is a secret, so the full URL must NOT appear in curl's
# argv (it would leak via the process list, cubic) — pass it through a curl config
# file via process substitution instead. The body is a binary tar on stdin.
urlenc() { python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1], safe=""))' "$1"; }
q_key=$(urlenc "$KEY")
q_dest=$(urlenc "$REMOTE_DEST")
full_url="${URL%/}/api/push_codebase?session_key=${q_key}&remote_dest=${q_dest}"

# tar+gzip the code dir contents and POST as the raw body. `url`/options come from
# the --config file (process substitution), keeping the secret URL out of argv.
tar -C "$CODE_DIR" -czf - . \
  | curl -fsS -m 120 --config <(printf 'url = "%s"\nrequest = "POST"\nheader = "Content-Type: application/octet-stream"\n' "$full_url") \
      --data-binary @-
