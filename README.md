# ucl-gpu-infra

Shared UCL GPU **broker** + **experiment submission** infrastructure, extracted
from the Memento-Research fork so two codebases depend on **one** copy instead of
drifting apart:

- the auto-scientist fork (Memento-Research) — Stage 6 experiment runs
- [`continual-auto-research`](../continual-auto-research) — hill-climbing runs

Both call the same small surface; pin the same tag in both repos and bump
deliberately. That is what keeps them from diverging.

## Install

```bash
pip install "git+ssh://git@github.com/<you>/ucl-gpu-infra@v0.1.0"
```

(Private repo + pinned tag is enough — no PyPI needed.)

## What's in it

| Module | Role |
|--------|------|
| `gpu_broker` | Claim/release a dedicated GPU per run via the on-demand broker. Fail-open: no broker installed → `"fallback"`, caller uses the static shim. |
| `stage6_infra` | Build a `Receipt`, locate the experiment-infra scripts (shipped as package data), push code + submit a run, query status. Never raises. |
| `run_poller` | OMC-free polling: `list_runs()` (unioned across the static shim + every broker lease shim), `filter_runs_by_workdir()`, `is_terminal()`, and a `RunPoller` convenience wrapper. |
| `secrets` | Shared-secret loader: `load_secrets()` reads ONE git-ignored box-local file (LLM key, infra key, …) into `os.environ`, referenced by every consumer. |

## Shared secrets (one file, no secrets in git)

The real secrets live in exactly **one** git-ignored file on the box; both
consumer repos load it through this package — no copies, no keys in any repo.

```bash
# one-time, on the box:
mkdir -p ~/.config/ucl-gpu-infra
cp secrets.env.template ~/.config/ucl-gpu-infra/secrets.env   # then fill in real values
```

```python
from ucl_gpu_infra import load_secrets
report = load_secrets()   # → os.environ; report lists set/skipped/empty (names only)
```

- Path resolves from `$UCL_GPU_INFRA_SECRETS`, else `~/.config/ucl-gpu-infra/secrets.env`.
- `override=False` (default) means an already-set env wins — safe to call at startup.
- `secrets.env.template` (committed, no values) documents the shape. The filled-in
  `secrets.env` is git-ignored and must never be committed.

## Usage

```python
from ucl_gpu_infra import gpu_broker, stage6_infra, run_poller

lease, status = gpu_broker.claim("my-run", holder="hc_run")   # "ok" | "unavailable" | "fallback"
env = gpu_broker.env_for(lease)

receipt = stage6_infra.Receipt(smoke_cmd="cd exp && python run.py",
                               code_dir="/path/to/exp", remote_dest="omc/my-run/iter_001")
scripts = stage6_infra.find_infra_scripts()
res = stage6_infra.submit(receipt, scripts, config_path="cfg.yaml", env=env)
run_id = res.run_id

# later, off a polling loop:
poller = run_poller.RunPoller(find_marker=lambda rid: f"omc/{rid}/iter_001")
if poller.all_terminal("my-run", [run_id]):
    gpu_broker.release("my-run")
```

## The run_tracker split

The fork's old `run_tracker` was two things tangled together. Only the generic
half lives here:

- **In this package** (`run_poller`): poll `/api/list_runs`, dedup, filter by the
  `omc/<id>/<iter>` workdir marker, classify terminal. No app state, no project
  model.
- **Stays in each consumer** (app glue): persisting results onto the app's own
  state, routing finalize back into its pipeline. ~50 lines per repo.

## Config (env)

| Var | Meaning |
|-----|---------|
| `GPU_BROKER` | On unless set to `0/false/no/off`. |
| `UCL_INFRA_DIR` | Where the broker scripts live (`claim-gpu.sh`, `release-gpu.sh`, `gpu-leases.sh`). |
| `INFRA_SERVER_URL` / `INFRA_SESSION_KEY` | Static shim creds (fallback when no broker lease). |
| `EXPERIMENT_INFRA_SCRIPTS` | Override the experiment-infra script dir (else the packaged copy is used). |

## Tests

```bash
pip install -e ".[test]"
pytest
```

No live broker or infra required — the fail-open paths and pure logic are covered.
