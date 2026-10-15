"""Give each Harbor trial network its own subnet from trial_network.py's pool.

Harbor 0.22 lets Docker take every trial's compose `default` network from the
daemon's address pools. On a shared worker those run out, and the trial fails
with `all predefined address pools have been fully subnetted` before it starts.
This wraps `DockerEnvironment._docker_compose_paths`: the first time an
environment builds a compose command, `trial_network.allocate()` reserves a
free subnet for it, and one more compose file sets that subnet on `default`.
`stop()` releases it after Harbor's own `docker compose down`, so a retry (a new
environment) gets a fresh one. With no subnet free, Harbor keeps Docker's
default; `MAC_K3D_TRIAL_SUBNETS=off` leaves Harbor unchanged.

Imported by `icode_harbor_agent.py` before anything else. Harbor's `Trial`
loads the agent before it creates the environment, so this runs before the
first `docker compose up`.
"""

from __future__ import annotations

import functools
import sys
import tempfile
from pathlib import Path

import trial_network

PATHS = "_docker_compose_paths"
STOP = "stop"
STATE = "_mac_k3d_trial_network"
MARK = "_mac_k3d_trial_network_applied"
COMPOSE_NAME = "trial-network.yaml"


def _reserve(env) -> dict:
    state = getattr(env, STATE, None)
    if state is not None:
        return state
    state = {}
    setattr(env, STATE, state)
    trial = trial_network.project_name(str(getattr(env, "session_id", "") or "trial"))
    try:
        subnet = trial_network.allocate(trial)
    except (trial_network.TrialNetworkError, OSError) as exc:
        print(f"WARNING: {trial}: no trial subnet ({exc}); Docker's default address pools apply", file=sys.stderr)
        return state
    if subnet is None:
        print(f"WARNING: {trial}: every trial subnet is taken; Docker's default address pools apply", file=sys.stderr)
        return state
    tmp = tempfile.TemporaryDirectory(prefix="mac-k3d-trial-network-")
    path = Path(tmp.name) / COMPOSE_NAME
    path.write_text(
        f"networks:\n  default:\n    ipam:\n      config:\n        - subnet: {subnet}\n", encoding="utf-8"
    )
    state.update(subnet=subnet, tmp=tmp, path=path)
    return state


def _release(env) -> None:
    state = getattr(env, STATE, None) or {}
    setattr(env, STATE, {})
    if state.get("subnet"):
        try:
            trial_network.release(state["subnet"])
        except (trial_network.TrialNetworkError, OSError) as exc:
            print(f"WARNING: could not release trial subnet {state['subnet']} ({exc})", file=sys.stderr)
    if state.get("tmp"):
        state["tmp"].cleanup()


def _with_subnet(paths: property) -> property:
    @functools.wraps(paths.fget)
    def fget(self) -> list[Path]:
        out = list(paths.fget(self))
        state = _reserve(self)
        if state.get("path"):
            out.append(state["path"])
        return out

    return property(fget, doc=paths.__doc__)


def _releasing(stop):
    @functools.wraps(stop)
    async def wrapper(self, *args, **kwargs):
        try:
            return await stop(self, *args, **kwargs)
        finally:
            _release(self)

    return wrapper


def apply() -> bool:
    """Patch Harbor's DockerEnvironment once; False when off or this Harbor has no such hook."""
    if trial_network.disabled():
        return False
    try:
        from harbor.environments.docker.docker import DockerEnvironment
    except ImportError as exc:
        print(f"WARNING: Harbor's DockerEnvironment is not importable ({exc}); trial networks left to Docker", file=sys.stderr)
        return False
    if getattr(DockerEnvironment, MARK, False):
        return True
    paths = DockerEnvironment.__dict__.get(PATHS)
    if not isinstance(paths, property) or paths.fget is None or not callable(getattr(DockerEnvironment, STOP, None)):
        print(
            f"WARNING: this Harbor has no DockerEnvironment.{PATHS} property / {STOP}(); "
            "trial networks left to Docker",
            file=sys.stderr,
        )
        return False
    setattr(DockerEnvironment, PATHS, _with_subnet(paths))
    setattr(DockerEnvironment, STOP, _releasing(DockerEnvironment.stop))
    setattr(DockerEnvironment, MARK, True)
    return True


apply()
