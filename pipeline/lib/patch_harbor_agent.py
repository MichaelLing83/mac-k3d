"""Harbor agent that applies a pre-written patch and exits.

The LLM call already happened on the host (or the patch is the task's gold).
The patch then goes through the same icode_capture.sh as an iCode attempt, so
Harbor's verifier grades it the same way.
"""

from __future__ import annotations

try:
    from typing import override
except ImportError:  # Python 3.11
    def override(method):
        return method

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from icode_harbor_agent import CAPTURE, install_capture

PATCH_HOST = "/opt/baseline-patch/agent.patch"


class PatchAgent(BaseInstalledAgent):
    """Copy a mounted unified diff into the task repo."""

    @staticmethod
    @override
    def name() -> str:
        return "patch"

    @override
    def get_version_command(self) -> str | None:
        return "git --version"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("git",))
        await install_capture(self, environment)

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = dict(self.extra_env)
        await self.exec_as_agent(
            environment,
            env=env,
            command=(
                "set -eu; "
                "mkdir -p /logs/agent /logs/artifacts; "
                f"repo=$(bash {CAPTURE} base) || "
                '{ echo "capture: repo check failed (see /logs/agent/capture.json)" >&2; exit 3; }; '
                'cd "$repo"; '
                f"if [ -s {PATCH_HOST} ]; then "
                f"  cp -f {PATCH_HOST} /logs/agent/agent.patch; "
                f"  git apply --verbose {PATCH_HOST} || echo 'git apply failed' | tee /logs/agent/notes.txt; "
                'else echo "empty patch" | tee /logs/agent/notes.txt; fi; '
                f"bash {CAPTURE} capture || true"
            ),
        )
