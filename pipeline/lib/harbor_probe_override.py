"""Give Harbor the egress-control kernel answer env/egress measured.

Harbor 0.22 enforces the agent allowlist and `no-network` only when
`DockerEnvironment._egress_control_kernel_support()` returns True. That probe
runs a pinned alpine image with a 30 s timeout; on a host whose Docker store
cannot unpack that image, or whose daemon is slow, it fails and Harbor refuses
isolation. `harbor_egress.py check` runs Harbor's own probe script first and
exports `MAC_K3D_EGRESS_PROBE_IMAGE` only when Docker could not run Harbor's
image but the same script passed in another image on this host, in this step.
Then Harbor reuses that answer and starts no probe container of its own. The
sidecar, the allowlist and `no-network` stay Harbor's own.

Imported by `icode_harbor_agent.py` before anything else. Harbor's `Trial`
loads the agent before it creates the environment, so this runs before the
first probe.
"""

from __future__ import annotations

import os
import sys

ENV = "MAC_K3D_EGRESS_PROBE_IMAGE"
ATTR = "_EGRESS_CONTROL_KERNEL_PROBE_IMAGE"
PROBE = "_egress_control_kernel_support"


def _measured() -> bool:
    return True


def apply(image: str | None = None) -> bool:
    """Record `image` (default: $MAC_K3D_EGRESS_PROBE_IMAGE) and answer Harbor's probe with the check's result."""
    image = (image if image is not None else os.environ.get(ENV, "")).strip()
    if not image:
        return False
    try:
        from harbor.environments.docker.docker import DockerEnvironment
    except ImportError as exc:
        print(f"WARNING: {ENV} set but Harbor's DockerEnvironment is not importable ({exc})", file=sys.stderr)
        return False
    if not hasattr(DockerEnvironment, ATTR) or getattr(DockerEnvironment, PROBE, None) is None:
        print(
            f"WARNING: {ENV} set but this Harbor has no DockerEnvironment.{ATTR} / {PROBE}(); "
            "probe left unchanged",
            file=sys.stderr,
        )
        return False
    setattr(DockerEnvironment, ATTR, image)
    setattr(DockerEnvironment, PROBE, staticmethod(_measured))
    return True


apply()
