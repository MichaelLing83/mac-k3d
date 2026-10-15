#!/usr/bin/env python3
"""Can Harbor enforce network isolation on this Docker host?

Harbor 0.22 runs its egress-control kernel probe (a pinned alpine image) and
silently refuses the agent allowlist and `no-network` when that container
cannot run. This step runs the same probe visibly, before any trial:

  check --out FILE      Harbor's probe image first, then Harbor's own check of
                        it. Only when Docker cannot run it (pull or create
                        error, exit 125-127, timeout, or Harbor's 30 s probe
                        failing), the same script in Harbor's egress sidecar
                        image (every isolated trial starts it anyway), then in
                        FALLBACK_PROBE_IMAGE. A probe that runs and exits
                        non-zero is a kernel answer and never falls back.
                        Writes FILE (egress_probe.json); exit 1 on failure.
  override --probe FILE print the probe image to export as
                        MAC_K3D_EGRESS_PROBE_IMAGE, or nothing

Each probe container is named, has no network, and gets RUN_TIMEOUT_S, then
one retry with RETRY_TIMEOUT_S; a container that timed out is removed.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

LIB = Path(__file__).resolve().parent
FALLBACK_PROBE_IMAGE = "alpine:3.19@sha256:6baf43584bcb78f2e5847d1de515f23499913ac9f12bdf834811a3145eb11ca1"
OVERRIDE_ENV = "MAC_K3D_EGRESS_PROBE_IMAGE"
PULL_TIMEOUT_S = 600
RUN_TIMEOUT_S = 60
RETRY_TIMEOUT_S = 180
RM_TIMEOUT_S = 30
CHECK_TIMEOUT_S = 120
HARBOR_PROBE_TIMEOUT_S = 30
PROBE_NAME_PREFIX = "mac-k3d-egress-probe-"
DOCKER_RUN_ERRORS = (125, 126, 127)
TIMED_OUT = "timed out after"
ROLE_LABELS = {
    "harbor_default": "Harbor's probe image",
    "sidecar": "Harbor's egress sidecar",
    "fallback": "fallback",
}
NETWORK_ERRORS = (
    "i/o timeout",
    "tls handshake timeout",
    "connection reset",
    "connection refused",
    "unexpected eof",
    "client.timeout",
    "context deadline exceeded",
    "temporary failure in name resolution",
    "no such host",
)
RUNBOOK = "docs/testing/cloud-eval-runbook.md (Troubleshooting)"

QUERY = r"""
import asyncio, json
out = {}
try:
    from harbor.environments.docker.docker import DockerEnvironment as D
except Exception as exc:
    print(json.dumps({"error": f"cannot import Harbor's DockerEnvironment: {exc}"}))
    raise SystemExit(0)
out["image"] = getattr(D, "_EGRESS_CONTROL_KERNEL_PROBE_IMAGE", None)
out["script"] = getattr(D, "_EGRESS_CONTROL_KERNEL_PROBE_SCRIPT", None)
try:
    from harbor.environments.docker.utils import _compute_image_name, default_docker_platform
    from harbor.utils.container_cache import docker_build_context_hash
    key = docker_build_context_hash(
        context=D._EGRESS_CONTROL_SIDECAR_CONTEXT_PATH,
        dockerfile_path=D._egress_control_sidecar_dockerfile_path(),
        build_args={},
        platform=asyncio.run(default_docker_platform()),
    )
    out["sidecar"] = _compute_image_name(D._EGRESS_CONTROL_SIDECAR_DOCKER_NAME, key)
except Exception as exc:
    out["sidecar_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(out))
"""

CHECK = r"""
import harbor_probe_override
from harbor.environments.docker.docker import DockerEnvironment as D
print("true" if D._egress_control_kernel_support() else "false")
"""


class EgressError(Exception):
    pass


def harbor_python() -> str:
    """Harbor's own interpreter: the shebang of the `harbor` launcher."""
    explicit = os.environ.get("MAC_K3D_HARBOR_PYTHON", "").strip()
    if explicit:
        return explicit
    launcher = shutil.which("harbor")
    if not launcher:
        raise EgressError("harbor is not on PATH (env/harbor installs it)")
    try:
        first = Path(launcher).resolve().read_text(encoding="utf-8", errors="replace").splitlines()[0]
    except (OSError, IndexError) as exc:
        raise EgressError(f"cannot read {launcher}: {exc}") from exc
    interp = first[2:].strip().split()[0] if first.startswith("#!") and first[2:].strip() else ""
    if not interp or not Path(interp).is_file():
        raise EgressError(f"cannot find Harbor's interpreter from {launcher} (first line {first!r})")
    return interp


def run(cmd: list[str], timeout: float, env: dict[str, str] | None = None) -> tuple[int | None, str, str]:
    """(returncode or None on timeout / missing binary, stdout, stderr)."""
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    except subprocess.TimeoutExpired:
        return None, "", f"{TIMED_OUT} {int(timeout)}s: {' '.join(cmd[:4])}"
    except OSError as exc:
        return None, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def first_error(text: str) -> str:
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    for line in lines:
        if "error" in line.lower():
            return line[:300]
    return (lines[-1] if lines else "")[:300]


def hints(reason: str) -> list[str]:
    low = reason.lower()
    out = []
    if "unable to prepare extraction snapshot" in low or "alreadyexists" in low or (
        "blob" in low and "not found" in low
    ):
        out.append(
            "this host's Docker image store is damaged (a stale snapshot or missing blobs); "
            f"a root admin can repair it, see {RUNBOOK}"
        )
    if "toomanyrequests" in low:
        out.append("Docker Hub rate limit: run `docker login` on this worker")
    if "docker api" in low or "docker daemon" in low or "docker.sock" in low:
        out.append("the Docker daemon is not reachable for this user (env/host checks the same)")
    if TIMED_OUT in low:
        out.append(
            f"Docker did not finish a probe container within {RUN_TIMEOUT_S} s, then {RETRY_TIMEOUT_S} s; "
            "the daemon may be overloaded (check `docker ps` and `docker info` on this worker)"
        )
    return out


def harbor_facts(python: str) -> dict:
    rc, out, err = run([python, "-c", QUERY], CHECK_TIMEOUT_S)
    if rc != 0:
        raise EgressError(f"Harbor query failed: {first_error(err) or f'exit {rc}'}")
    try:
        facts = json.loads(out.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError) as exc:
        raise EgressError(f"Harbor query printed no JSON: {out[-200:]!r}") from exc
    if facts.get("error"):
        raise EgressError(facts["error"])
    if not facts.get("image") or not facts.get("script"):
        raise EgressError(
            "this Harbor has no DockerEnvironment._EGRESS_CONTROL_KERNEL_PROBE_IMAGE/_SCRIPT; "
            "harbor_egress.py was written for Harbor 0.22"
        )
    return facts


def ensure_image(ref: str, pull: bool = True) -> str:
    """'' when the image is present (pulled if needed and allowed), else the error."""
    rc, _, _ = run(["docker", "image", "inspect", ref], RUN_TIMEOUT_S)
    if rc == 0:
        return ""
    if not pull:
        return f"{ref} is not on this host"
    err = ""
    for attempt in (1, 2):
        rc, _, err = run(["docker", "pull", ref], PULL_TIMEOUT_S)
        if rc == 0:
            return ""
        if attempt == 1 and (rc is None or any(m in err.lower() for m in NETWORK_ERRORS)):
            print(f"docker pull {ref} failed ({first_error(err)}); retrying once", file=sys.stderr)
            time.sleep(5)
            continue
        break
    return first_error(err) or "docker pull failed"


def probe_cmd(name: str, ref: str, script: str) -> list[str]:
    """Harbor's script needs no network; `--entrypoint sh` also runs it in images with their own entrypoint."""
    return [
        "docker", "container", "run", "--rm", "--name", name, "--network", "none",
        "--entrypoint", "sh", ref, "-c", script,
    ]


def remove_container(name: str) -> None:
    run(["docker", "container", "rm", "-f", name], RM_TIMEOUT_S)


def probe(ref: str, script: str, pull: bool = True) -> tuple[str, str]:
    """('ok' | 'kernel' | 'docker', detail)."""
    image_error = ensure_image(ref, pull)
    if image_error:
        return "docker", image_error
    err = ""
    for timeout in (RUN_TIMEOUT_S, RETRY_TIMEOUT_S):
        name = f"{PROBE_NAME_PREFIX}{os.getpid()}-{secrets.token_hex(4)}"
        rc, _, err = run(probe_cmd(name, ref, script), timeout)
        if rc == 0:
            return "ok", ""
        if rc is None and err.startswith(TIMED_OUT):
            remove_container(name)
            if timeout == RUN_TIMEOUT_S:
                print(f"egress probe in {ref} {err}; removed {name}, retrying with {RETRY_TIMEOUT_S}s")
                continue
        if rc is None or rc in DOCKER_RUN_ERRORS:
            return "docker", first_error(err) or f"docker run exited {rc}"
        return "kernel", f"probe exited {rc}" + (f": {first_error(err)}" if err.strip() else "")
    return "docker", first_error(err)


def harbor_check(python: str, override: str) -> bool:
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (str(LIB), env.get("PYTHONPATH", "")) if p)
    if override:
        env[OVERRIDE_ENV] = override
    else:
        env.pop(OVERRIDE_ENV, None)
    rc, out, err = run([python, "-c", CHECK], CHECK_TIMEOUT_S, env=env)
    if rc != 0:
        raise EgressError(f"Harbor's own probe check crashed: {first_error(err) or f'exit {rc}'}")
    return out.strip().splitlines()[-1:] == ["true"]


def tried_line(entry: dict) -> str:
    label = ROLE_LABELS.get(entry["role"], entry["role"])
    result = entry["detail"] if entry["result"] != "ok" else "ok"
    return f"{label} {entry['image']}: {result}"


def answer_key(record: dict) -> tuple:
    """The part of a probe record the console explains. `checked_at` is not part of it."""
    return (
        record.get("harbor_default"),
        record.get("image"),
        bool(record.get("substituted")),
        record.get("reason") or "",
        record.get("sidecar") or "",
        record.get("sidecar_present"),
    )


def previous_answer(out_path: Path) -> dict | None:
    try:
        doc = json.loads(out_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def check(out_path: Path, fallback: str = FALLBACK_PROBE_IMAGE) -> dict:
    python = harbor_python()
    facts = harbor_facts(python)
    default, script = facts["image"], facts["script"]
    sidecar = facts.get("sidecar") or ""
    sidecar_present = None
    if sidecar:
        rc, _, _ = run(["docker", "image", "inspect", sidecar], RUN_TIMEOUT_S)
        sidecar_present = rc == 0

    tried: list[dict] = []
    record = {
        "harbor_default": default,
        "image": default,
        "substituted": False,
        "reason": "",
        "sidecar": sidecar,
        "sidecar_present": sidecar_present,
        "tried": tried,
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }

    def attempt(image: str, role: str, pull: bool) -> tuple[str, str]:
        status, detail = probe(image, script, pull)
        tried.append({"image": image, "role": role, "result": status, "detail": detail})
        if status == "kernel":
            raise EgressError(
                f"the egress probe ran in {image} but failed ({detail}): this Docker host's kernel lacks "
                "CONFIG_NFT_FIB_INET, so Harbor cannot enforce the agent allowlist here"
            )
        return status, detail

    status, detail = attempt(default, "harbor_default", True)
    if status == "ok" and not harbor_check(python, ""):
        status = "docker"
        detail = (
            f"Harbor's own probe failed within its {HARBOR_PROBE_TIMEOUT_S} s timeout "
            "although the same script passed here (a slow Docker daemon)"
        )
        tried[-1].update(result=status, detail=detail)

    if status == "docker":
        candidates = [(fallback, "fallback", True)]
        if sidecar_present:
            candidates.insert(0, (sidecar, "sidecar", False))
        chosen = ""
        for image, role, pull in candidates:
            if attempt(image, role, pull)[0] == "ok":
                chosen = image
                break
        if not chosen:
            lines = ["Harbor cannot check egress control on this host, so it would refuse network isolation."]
            lines += [f"  {tried_line(t)}" for t in tried]
            lines += [f"  hint: {h}" for h in hints(" ".join(t["detail"] for t in tried))]
            raise EgressError("\n".join(lines))
        record.update(image=chosen, substituted=True, reason=detail)
        if not harbor_check(python, chosen):
            raise EgressError(
                f"the probe passed in {chosen} but Harbor's own _egress_control_kernel_support() "
                "still returns False with the substitution"
            )

    if sidecar and not sidecar_present and record["substituted"]:
        raise EgressError(
            f"Harbor's egress sidecar {sidecar} is not on this host and Harbor would build it "
            f"from gogost/gost, which needs the same image layers that {default} could not unpack"
        )
    previous = previous_answer(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")

    if previous is not None and answer_key(previous) == answer_key(record):
        print(f"egress: same probe as the last check ({record['image']})")
        return record

    if record["substituted"]:
        print(
            f"WARNING: Harbor's probe image {default} cannot run on this host ({detail}); "
            f"using {record['image']} for the kernel check. Network isolation is unchanged."
        )
        for entry in tried:
            print(f"  tried {tried_line(entry)}")
        for h in hints(detail):
            print(f"  hint: {h}")
    else:
        print(f"OK egress control: Harbor default probe {default}")
    if not sidecar:
        print(f"note: could not name Harbor's egress sidecar image ({facts.get('sidecar_error', 'unknown')})")
    elif record["sidecar_present"]:
        print(f"OK egress sidecar {sidecar} present")
    else:
        print(f"note: Harbor builds its egress sidecar {sidecar} on the first trial")
    return record


def override_image(probe_file: Path) -> str:
    try:
        doc = json.loads(probe_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(doc.get("image") or "") if doc.get("substituted") else ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check")
    p_check.add_argument("--out", type=Path, required=True)
    p_override = sub.add_parser("override")
    p_override.add_argument("--probe", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.cmd == "override":
        image = override_image(args.probe)
        if image:
            print(image)
        return 0
    try:
        check(args.out)
    except EgressError as exc:
        args.out.unlink(missing_ok=True)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
