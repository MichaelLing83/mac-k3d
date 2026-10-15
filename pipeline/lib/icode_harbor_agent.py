"""Harbor adapter: official worker iCode drop bind-mounted at /opt/icode-host.

Used for DeepSWE, LoLBench, and SWE-bench Pro. LoLBench's in-repo agent clones
iCode from gitcode inside the sandbox. That repo is not anonymously cloneable,
and this lab does not store a GitCode PAT on Jenkins.

The model key never travels in an exec's environment or command line: Harbor
turns both into `docker compose exec -e KEY=VALUE … bash -c …`, which any user
on the host can read with `ps`. Harbor's own process has the key (loaded from
--env-file), so the agent copies it into the trial as a 0600 file owned by the
agent user, and the run command loads it and deletes the file before iCode
starts.
"""

from __future__ import annotations

import harbor_probe_override  # noqa: F401  (must run before Harbor creates an environment)
import harbor_network_override  # noqa: F401  (must run before Harbor creates an environment)

import os
import shlex
import tempfile
from pathlib import Path

try:
    from typing import override
except ImportError:  # Python 3.11
    def override(method):
        return method

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-flash"
MAX_TOKENS = "65536"
MAX_ITERATIONS = "500"
ICODE_HOST = "/opt/icode-host"
CAPTURE_SRC = Path(__file__).with_name("icode_capture.sh")
CAPTURE = "/installed-agent/icode_capture.sh"
MODEL_KEYS = ("DEEPSEEK_API_KEY",)
MODEL_KEY_FILE = "/tmp/.mac-k3d-model.env"
MODEL_KEY_LOADER = (
    f"set -a; . {MODEL_KEY_FILE} || "
    '{ echo "model key file missing" >&2; exit 3; }; '
    f"set +a; rm -f {MODEL_KEY_FILE}; "
)


async def install_capture(agent: BaseInstalledAgent, environment: BaseEnvironment) -> None:
    """Upload the capture script every agent shares: `base` before work, `capture` after."""
    await environment.upload_file(CAPTURE_SRC, CAPTURE)
    await agent.exec_as_root(environment, command=f"chmod 755 {CAPTURE}")


def model_key_text() -> str:
    """The MODEL_KEY_FILE body, from Harbor's own environment."""
    missing = [key for key in MODEL_KEYS if not os.environ.get(key)]
    if missing:
        raise RuntimeError(
            f"{', '.join(missing)} is not in Harbor's environment; "
            "evaluate passes it with --env-file, never with --ae"
        )
    return "".join(f"{key}={shlex.quote(os.environ[key])}\n" for key in MODEL_KEYS)


class ICodeAgent(BaseInstalledAgent):
    """Run the official iCode binary inside a Harbor task."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Trial applies extra_env to every exec of the agent: a key passed with
        # --ae would ride along on each `docker compose exec -e`.
        for key in MODEL_KEYS:
            self._extra_env.pop(key, None)

    @staticmethod
    @override
    def name() -> str:
        return "icode"

    @override
    def get_version_command(self) -> str | None:
        return "icode --help >/dev/null 2>&1 && echo icode-installed"

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        await self.ensure_system_dependencies(environment, ("git", "curl"))
        await install_capture(self, environment)
        await self.exec_as_agent(
            environment,
            command=(
                "set -euo pipefail; "
                f"ls -la {ICODE_HOST} || true; "
                "bin=''; "
                f"if [ -x {ICODE_HOST}/icode ]; then bin={ICODE_HOST}/icode; "
                f"else bin=$(find {ICODE_HOST} -type f -name icode -print -quit); fi; "
                '[ -n "$bin" ] || { echo missing icode binary in /opt/icode-host bind mount; exit 1; }; '
                'mkdir -p "$HOME/.local/bin"; '
                'cp -f "$bin" "$HOME/.local/bin/icode"; '
                'chmod +x "$HOME/.local/bin/icode"; '
                'export PATH="$HOME/.local/bin:$PATH"; '
                "icode --help >/dev/null"
            ),
            env=self.extra_env,
        )

    async def upload_model_key(self, environment: BaseEnvironment) -> None:
        """Copy the model key into the trial as MODEL_KEY_FILE (0600, the agent user's)."""
        text = model_key_text()
        with tempfile.TemporaryDirectory(prefix="mac-k3d-model-") as tmp:
            local = Path(tmp) / "model.env"
            fd = os.open(local, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(text)
            await environment.upload_file(local, MODEL_KEY_FILE)
        target = shlex.quote(MODEL_KEY_FILE)
        if environment.default_user is not None:
            owner = shlex.quote(str(environment.default_user))
            await self.exec_as_root(environment, command=f"chown {owner} {target} && chmod 600 {target}")
        else:
            await self.exec_as_root(environment, command=f"chmod 600 {target}")

    def run_env(self) -> dict[str, str]:
        """Environment of the iCode run; CanaryAgent probes with the same one."""
        env = {key: value for key, value in self.extra_env.items() if key not in MODEL_KEYS}
        env.setdefault("ICODE_API_BASE", DEEPSEEK_BASE_URL)
        base = str(env.get("ICODE_API_BASE") or "").lower()
        env.setdefault(
            "ICODE_PROVIDER",
            "DeepSeek" if "deepseek.com" in base else "OpenAI",
        )
        env.setdefault("ICODE_REASONING_EFFORT", "high")
        env.setdefault("ICODE_MODEL", DEFAULT_MODEL)
        env.setdefault("ICODE_MAX_TOKENS", MAX_TOKENS)
        env.setdefault("ICODE_MAX_ITERATIONS", MAX_ITERATIONS)
        return env

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        env = self.run_env()

        await self.exec_as_agent(
            environment,
            command=(
                "set -eu; mkdir -p /logs/agent/icode /logs/agent/icode-project; "
                f"cat > /tmp/icode_task.md <<'ICODE_TASK_EOF'\n{instruction}\nICODE_TASK_EOF"
            ),
        )

        # Harbor prefixes this script with `set -o pipefail`. iCode exits 1 when
        # the turn is not ok (including a tool path that is too long). That exit
        # must not become NonZeroAgentExitCodeError; the verifier still grades
        # whatever patch was written. Turn pipefail off, keep iCode's status,
        # and let the shell itself exit 0. Same command for every benchmark;
        # icode_capture.sh hands the patch to each suite's grader. A declared
        # repo that is not a git top-level stops the trial before iCode starts.
        await self.upload_model_key(environment)
        command = MODEL_KEY_LOADER + (
            'export PATH="$HOME/.local/bin:$PATH"; '
            f"repo=$(bash {CAPTURE} base) || "
            '{ echo "capture: repo check failed (see /logs/agent/capture.json)" >&2; exit 3; }; '
            'cd "$repo"; '
            "set +o pipefail; "
            "icode -p /logs/agent/icode-project "
            "run -t /tmp/icode_task.md "
            '-C "$repo" -a code --json 2>&1 | tee /logs/agent/icode.txt; '
            'printf "%s\\n" "${PIPESTATUS[0]}" > /logs/agent/icode-exit.txt; '
            f"bash {CAPTURE} capture; "
            'printf "%s\\n" "$?" > /logs/agent/capture-exit.txt'
        )
        await self.exec_as_agent(environment, env=env, command=command)
