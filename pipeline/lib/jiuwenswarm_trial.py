"""Inside a Harbor trial, with the bundle's Python: JiuwenSwarm's data dir and run document.

JiuwenSwarmAgent uploads this file to /installed-agent/jiuwenswarm_trial.py and
runs it with /opt/jiuwenswarm-host/bin/jiuwenswarm-python, so the task image
needs no Python of its own.

  jiuwenswarm_trial.py seed
      Seed $JIUWENSWARM_DATA_DIR from the packaged config: English, no
      permission prompts (a --run-json run auto-rejects every ask), no file
      guard, and config/.env holding the endpoint and model only. The key
      stays in the run shell's API_KEY: JiuwenSwarm reloads .env with
      override=True, so a key line there would win.
  jiuwenswarm_trial.py rundoc TASK REPO OUT
      The run document for `jiuwenswarm-process --run-json OUT`: the
      instruction from TASK, REPO as the workspace, and the mode, turn limit
      and timeout from JIUWENSWARM_AGENT_MODE, JIUWENSWARM_MAX_TURNS and
      JIUWENSWARM_TIMEOUT_SECONDS. It carries no `permissions`: run-level
      tool levels turn the installed permission policy back on.
  jiuwenswarm_trial.py usage LOG OUT
      The result line of LOG without its answer text (status, exit code,
      error, usage) as OUT, jiuwenswarm-usage.json: a shard gzips the
      transcript, and the merged report still reads this file.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ENV_KEYS = ("API_BASE", "MODEL_NAME", "MODEL_PROVIDER", "ENDPOINT_PROFILE")
ENV_FIXED = {
    "BROWSER_RUNTIME_MCP_ENABLED": "0",
    "EVOLUTION_AUTO_SCAN": "false",
    "FREE_SEARCH_DDG_ENABLED": "false",
    "FREE_SEARCH_BING_ENABLED": "false",
}
MODES = ("agent.code.normal", "agent.code.plan")


def _env_line(key: str, value: str) -> str:
    if any(c in value for c in "\n\r\"\\"):
        raise ValueError(f"{key} holds a character .env cannot carry")
    return f'{key}="{value}"\n'


def seed() -> int:
    if not os.environ.get("JIUWENSWARM_DATA_DIR"):
        print("jiuwenswarm_trial: JIUWENSWARM_DATA_DIR is not set", file=sys.stderr)
        return 2
    missing = [key for key in ENV_KEYS if not os.environ.get(key)]
    if missing:
        print(f"jiuwenswarm_trial: {', '.join(missing)} not set", file=sys.stderr)
        return 2
    # JiuwenSwarm fixes its config dir on first use: the data dir's config/ when
    # it exists, else the package's own resources, read-only in the mount.
    Path(os.environ["JIUWENSWARM_DATA_DIR"], "config").mkdir(parents=True, exist_ok=True)
    from jiuwenswarm.common.utils import get_config_dir, get_env_file, prepare_workspace

    config_dir = Path(get_config_dir()).resolve()
    if config_dir != Path(os.environ["JIUWENSWARM_DATA_DIR"], "config").resolve():
        print(f"jiuwenswarm_trial: JiuwenSwarm reads its config from {config_dir}, not the data dir", file=sys.stderr)
        return 1
    prepare_workspace(overwrite=True, preferred_language="en")
    # config.CONFIG_YAML_PATH is fixed at import: import it after the seed.
    from jiuwenswarm.common import config

    config.update_permissions_profile_in_config("full_access")

    def _mutate(data: dict) -> dict:
        data["preferred_language"] = "en"
        permissions = data.get("permissions")
        if not isinstance(permissions, dict):
            permissions = data["permissions"] = {}
        guard = permissions.get("file_guard")
        if not isinstance(guard, dict):
            guard = permissions["file_guard"] = {}
        guard["enabled"] = False
        return data

    config.update_config(_mutate)
    env_file = Path(get_env_file())
    env_file.parent.mkdir(parents=True, exist_ok=True)
    body = "".join(_env_line(key, os.environ[key]) for key in ENV_KEYS)
    body += "".join(_env_line(key, value) for key, value in ENV_FIXED.items())
    tmp = env_file.with_name(env_file.name + ".tmp")
    tmp.write_text(body, encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(env_file)
    data = config.get_config_raw() if hasattr(config, "get_config_raw") else {}
    perms = data.get("permissions") if isinstance(data, dict) else None
    guard = perms.get("file_guard") if isinstance(perms, dict) else None
    if not isinstance(perms, dict) or perms.get("enabled") is not False:
        print("jiuwenswarm_trial: permissions are still on after the seed", file=sys.stderr)
        return 1
    if isinstance(guard, dict) and guard.get("enabled") is not False:
        print("jiuwenswarm_trial: file_guard is still on after the seed", file=sys.stderr)
        return 1
    print(f"seeded {os.environ['JIUWENSWARM_DATA_DIR']}: full_access, file_guard off, model {os.environ['MODEL_NAME']}")
    return 0


def _positive_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    value = int(raw) if raw.isdigit() else 0
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer (got {raw})")
    return value


def rundoc(task: str, repo: str, out: str) -> int:
    mode = os.environ.get("JIUWENSWARM_AGENT_MODE", "").strip() or MODES[0]
    if mode not in MODES:
        print(f"jiuwenswarm_trial: JIUWENSWARM_AGENT_MODE={mode} (use {' or '.join(MODES)})", file=sys.stderr)
        return 2
    try:
        turns = _positive_int("JIUWENSWARM_MAX_TURNS", 500)
        timeout = _positive_int("JIUWENSWARM_TIMEOUT_SECONDS", None)
    except ValueError as exc:
        print(f"jiuwenswarm_trial: {exc}", file=sys.stderr)
        return 2
    if turns > 1000:
        print(f"jiuwenswarm_trial: JIUWENSWARM_MAX_TURNS={turns} is over JiuwenSwarm's 1000", file=sys.stderr)
        return 2
    doc = {
        "schema_version": "0.1",
        "type": "run",
        "input": Path(task).read_text(encoding="utf-8"),
        "mode": mode,
        "workspace": {"cwd": repo, "project_dir": repo, "trusted_dirs": [repo]},
        "max_turns": turns,
    }
    if timeout is not None:
        doc["timeout_seconds"] = timeout
    Path(out).write_text(json.dumps(doc, ensure_ascii=False) + "\n", encoding="utf-8")
    return 0


def usage(log: str, out: str) -> int:
    import jiuwenswarm_usage

    result = jiuwenswarm_usage.read_result(Path(log))
    if result is None:
        print(f"jiuwenswarm_trial: no result line in {log}", file=sys.stderr)
        return 1
    doc = {key: result[key] for key in ("type", "status", "exit_code", "error", "usage") if key in result}
    Path(out).write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


def main(argv: list[str]) -> int:
    if argv[:1] == ["seed"] and len(argv) == 1:
        return seed()
    if argv[:1] == ["rundoc"] and len(argv) == 4:
        return rundoc(*argv[1:])
    if argv[:1] == ["usage"] and len(argv) == 3:
        return usage(*argv[1:])
    print("usage: jiuwenswarm_trial.py seed | rundoc TASK REPO OUT | usage LOG OUT", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
