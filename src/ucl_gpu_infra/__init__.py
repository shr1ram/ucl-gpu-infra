"""ucl-gpu-infra — shared UCL GPU broker + experiment submission infrastructure.

Lifted out of the Memento-Research fork so the auto-scientist fork and the
continual-auto-research repo depend on ONE copy instead of diverging. The public
surface is exactly what both consumers already call:

    from ucl_gpu_infra import gpu_broker, stage6_infra, run_poller

    lease, status = gpu_broker.claim("my-run", holder="hc_run")
    env = gpu_broker.env_for(lease)
    receipt = stage6_infra.Receipt(smoke_cmd=cmd, code_dir=ws, remote_dest=dest)
    scripts = stage6_infra.find_infra_scripts()
    res = stage6_infra.submit(receipt, scripts, config_path, env=env)
    ...
    runs = run_poller.list_runs()
"""
from __future__ import annotations

from . import gpu_broker, run_poller, stage6_infra

__all__ = ["gpu_broker", "stage6_infra", "run_poller"]
__version__ = "0.1.0"
