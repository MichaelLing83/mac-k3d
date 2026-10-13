"""Harbor's oracle and nop agents for tasks/images' environment check.

The same agents as `harbor run --agent oracle` / `--agent nop`, loaded by import
path as iCode's and the canary's are, so the check's trials take their subnet
and egress probe from mac-k3d's overrides as the rollouts do. env_check.py
judges what the verifier wrote.
"""

from __future__ import annotations

import harbor_probe_override  # noqa: F401  (must run before Harbor creates an environment)
import harbor_network_override  # noqa: F401  (must run before Harbor creates an environment)

from pathlib import Path

from harbor.agents.nop import NopAgent as _HarborNop
from harbor.agents.oracle import OracleAgent as _HarborOracle
from harbor.models.trial.paths import TrialPaths


class OracleAgent(_HarborOracle):
    """Applies the task's reference solution (solution/solve.sh); every F2P and P2P test must pass."""

    def __init__(
        self,
        logs_dir: Path,
        task_dir: str | Path | None = None,
        trial_paths: TrialPaths | None = None,
        **kwargs,
    ):
        # Harbor passes these only to the agent it names `oracle`. By import
        # path the task comes from `--ak task_dir=`, and logs_dir is <trial>/agent.
        if task_dir is None:
            raise ValueError("env_check_agents:OracleAgent needs --ak task_dir=<task folder>")
        super().__init__(
            logs_dir=logs_dir,
            task_dir=Path(task_dir),
            trial_paths=trial_paths or TrialPaths(trial_dir=Path(logs_dir).parent),
            **kwargs,
        )


class NopAgent(_HarborNop):
    """Changes nothing; the verifier grades the base state, so some F2P test must fail."""
