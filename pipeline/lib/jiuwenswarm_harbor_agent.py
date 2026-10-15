"""Harbor adapter: the worker's JiuwenSwarm runtime bind-mounted at /opt/jiuwenswarm-host.

DeepSWE only. The runtime (tasks/harness, jiuwenswarm_build.sh) carries its own
CPython and packages, so the task image needs only bash, git and coreutils. A
trial runs JiuwenSwarm's headless runner once:

  jiuwenswarm-process --run-json /tmp/jiuwenswarm_run.json > jiuwenswarm.jsonl

stdout is JSONL: one `event` line per runtime event (tool calls included),
then one `result` line with the status, exit code and token usage
(jiuwenswarm_usage.py). The model key is handled as ICodeAgent handles it:
a 0600 file the run shell loads and deletes, then exported as API_KEY, the
name JiuwenSwarm's config reads.
"""

from __future__ import annotations

import harbor_probe_override  # noqa: F401  (must run before Harbor creates an environment)
import harbor_network_override  # noqa: F401  (must run before Harbor creates an environment)

import shlex
import tempfile
from pathlib import Path

try:
    from typing import override
except ImportError:  # Python 3.11
    def override(method):
        return method

from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

from canary_harbor_agent import CanaryAgent
from icode_harbor_agent import (
    CAPTURE,
    DEEPSEEK_BASE_URL,
    DEFAULT_MODEL,
    MODEL_KEY_LOADER,
    MODEL_KEYS,
    ICodeAgent,
    install_capture,
)

JIUWENSWARM_HOST = "/opt/jiuwenswarm-host"
RUNNER = f"{JIUWENSWARM_HOST}/bin/jiuwenswarm-run"
PYTHON = f"{JIUWENSWARM_HOST}/bin/jiuwenswarm-python"
TRIAL_SRC = Path(__file__).with_name("jiuwenswarm_trial.py")
TRIAL = "/installed-agent/jiuwenswarm_trial.py"
USAGE_SRC = Path(__file__).with_name("jiuwenswarm_usage.py")
USAGE_MODULE = "/installed-agent/jiuwenswarm_usage.py"
DATA_DIR = "/tmp/jiuwenswarm"
TASK_FILE = "/tmp/jiuwenswarm_task.md"
RUN_JSON = "/tmp/jiuwenswarm_run.json"
LOG = "/logs/agent/jiuwenswarm.jsonl"
STDERR = "/logs/agent/jiuwenswarm-stderr.txt"
EXIT_FILE = "/logs/agent/jiuwenswarm-exit.txt"
USAGE_FILE = "/logs/agent/jiuwenswarm-usage.json"
SEED_LOG = "/logs/agent/jiuwenswarm-seed.txt"
AGENT_MODE = "agent.code.normal"
MAX_TURNS = "500"
IMPORT_CHECK = "import jiuwenswarm.channels.process_cli.main"
# The run shell's key, as JiuwenSwarm's config.yaml names it (${API_KEY}).
KEY_EXPORT = 'export API_KEY="$DEEPSEEK_API_KEY"; unset DEEPSEEK_API_KEY; '


class JiuwenSwarmAgent(ICodeAgent):
    """Run JiuwenSwarm's headless runner inside a Harbor task.

    Shares ICodeAgent's key handling: MODEL_KEYS never ride on --ae, and
    upload_model_key copies the key in as a private file.
    """

    # The canary records a runtime that does not start instead of failing here.
    STRICT_INSTALL = True

    @staticmethod
    @override
    def name() -> str:
        return "jiuwenswarm"

    @override
    def get_version_command(self) -> str | None:
        return f"{RUNNER} --help >/dev/null 2>&1 && echo jiuwenswarm-installed"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("git", "curl"))
        await install_capture(self, environment)
        await environment.upload_file(TRIAL_SRC, TRIAL)
        await environment.upload_file(USAGE_SRC, USAGE_MODULE)
        await self.exec_as_root(environment, command=f"chmod 644 {TRIAL} {USAGE_MODULE}")
        if not self.STRICT_INSTALL:
            return
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"ls -la {JIUWENSWARM_HOST} || true; "
                f"[ -x {RUNNER} ] || {{ echo missing {RUNNER} in the bind mount; exit 1; }}; "
                f"cat {JIUWENSWARM_HOST}/runtime.json; "
                f"{PYTHON} -c {shlex.quote(IMPORT_CHECK)}"
            ),
            env=self.extra_env,
        )

    @override
    def run_env(self) -> dict[str, str]:
        """Environment of the JiuwenSwarm run; the canary probes with the same one."""
        env = {key: value for key, value in self.extra_env.items() if key not in MODEL_KEYS}
        base = str(env.get("ICODE_API_BASE") or DEEPSEEK_BASE_URL)
        env.setdefault("API_BASE", base)
        env.setdefault("MODEL_NAME", str(env.get("ICODE_MODEL") or DEFAULT_MODEL))
        env.setdefault("MODEL_PROVIDER", "OpenAI")
        env.setdefault("ENDPOINT_PROFILE", "deepseek" if "deepseek.com" in base.lower() else "openai")
        env.setdefault("JIUWENSWARM_DATA_DIR", DATA_DIR)
        env.setdefault("JIUWENSWARM_AGENT_MODE", AGENT_MODE)
        env.setdefault("JIUWENSWARM_MAX_TURNS", MAX_TURNS)
        env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
        return env

    async def upload_task(self, environment: BaseEnvironment, instruction: str) -> None:
        with tempfile.TemporaryDirectory(prefix="mac-k3d-jiuwenswarm-") as tmp:
            local = Path(tmp) / "task.md"
            local.write_text(instruction + "\n", encoding="utf-8")
            await environment.upload_file(local, TASK_FILE)
        await self.exec_as_root(environment, command=f"chmod 644 {TASK_FILE}")

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = self.run_env()
        await self.exec_as_agent(environment, command="mkdir -p /logs/agent")
        await self.upload_task(environment, instruction)
        await self.upload_model_key(environment)
        # Same contract as ICodeAgent.run: a failed turn keeps its exit code in
        # jiuwenswarm-exit.txt, the shell exits 0, and the verifier grades
        # whatever patch was written. A seed or run-document failure stops the
        # trial before the model is called.
        command = MODEL_KEY_LOADER + KEY_EXPORT + (
            f"repo=$(bash {CAPTURE} base) || "
            '{ echo "capture: repo check failed (see /logs/agent/capture.json)" >&2; exit 3; }; '
            'cd "$repo"; '
            f"{PYTHON} {TRIAL} seed >{SEED_LOG} 2>&1 || {{ cat {SEED_LOG} >&2; exit 3; }}; "
            f'{PYTHON} {TRIAL} rundoc {TASK_FILE} "$repo" {RUN_JSON} || exit 3; '
            "set +o pipefail; "
            f"{RUNNER} --run-json {RUN_JSON} >{LOG} 2>{STDERR}; "
            f'printf "%s\\n" "$?" > {EXIT_FILE}; '
            f"{PYTHON} {TRIAL} usage {LOG} {USAGE_FILE} && cat {USAGE_FILE}; "
            f"bash {CAPTURE} capture; "
            'printf "%s\\n" "$?" > /logs/agent/capture-exit.txt'
        )
        await self.exec_as_agent(environment, env=env, command=command)


class JiuwenSwarmCanaryAgent(CanaryAgent, JiuwenSwarmAgent):
    """The isolation canary with JiuwenSwarm's install, mount and run environment.

    Never calls the model. Before the shared probes it records whether the
    bundle's Python starts in the task image, imports the headless runner,
    and seeds the data dir with the permission prompts off.
    """

    HARNESS = "jiuwenswarm"
    MOUNT = JIUWENSWARM_HOST
    STRICT_INSTALL = False
    TOOL_LIST_REASON = (
        "JiuwenSwarm's headless runner has no command that prints its tool set; "
        "the trial network allows the model host only, and the P0.5 transcript scan covers web tools"
    )

    @staticmethod
    @override
    def name() -> str:
        return "jiuwenswarm-canary"

    @override
    def key_loader(self) -> str:
        return MODEL_KEY_LOADER + KEY_EXPORT

    @override
    def runtime_probes(self) -> list[tuple[str, str]]:
        return [
            ("python", f"{PYTHON} -c {shlex.quote(IMPORT_CHECK)}"),
            ("runner", f"{RUNNER} --help"),
            ("seed", f"{PYTHON} {TRIAL} seed"),
        ]
