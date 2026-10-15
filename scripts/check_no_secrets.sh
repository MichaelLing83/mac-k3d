#!/usr/bin/env bash
# Fail if git would track secret files or key-shaped values. Prints paths only.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
note() { echo "ERROR: $*" >&2; fail=1; }

# Names that must never be tracked (runtime env with API keys, eval dumps).
while IFS= read -r path; do
  [ -n "$path" ] || continue
  case "$path" in
    .env.example | */.env.example) continue ;;
  esac
  base="$(basename "$path")"
  case "$base" in
    .env | .env.local | .pier-env | .harbor-env)
      note "tracked secret env file: $path"
      ;;
  esac
  case "$path" in
    eval-runs/* | eval-runs-*/* | .cursor/debug-*.log | */icode-bin/*)
      note "tracked eval/runtime dump: $path"
      ;;
  esac
done < <(git ls-files)

# Staged + tracked text: KEY=value with a real secret (empty / placeholders OK).
scan_blob() {
  local path="$1"
  [ -f "$path" ] || return 0
  case "$(basename "$path")" in
    .env.example) return 0 ;;
  esac
  case "$path" in
    *.png | *.jpg | *.jpeg | *.gif | *.webp | *.ico | *.pdf | *.tar | *.gz | *.tgz | *.zip | *.bin)
      return 0
      ;;
  esac
  # icode binary / large blobs
  if [ -f "$path" ] && [ "$(wc -c <"$path" | tr -d ' ')" -gt 1000000 ]; then
    case "$(basename "$path")" in
      icode | icode-*)
        note "tracked iCode binary: $path"
        ;;
    esac
    return 0
  fi
  python3 - "$path" <<'PY'
import re, sys
path = sys.argv[1]
try:
    text = open(path, encoding="utf-8", errors="replace").read()
except OSError:
    raise SystemExit(0)
# Do not print values. Flag assigned API-key style material.
keys = (
    "DEEPSEEK_API_KEY",
    "MAC_K3D_DEEPSEEK_API_KEY",
    "OPENROUTER_API_KEY",
    "MAC_K3D_OPENROUTER_API_KEY",
    "GITCODE_TOKEN",
    "GITCODE_PAT",
    "GITHUB_TOKEN",
    "GITHUB_PAT",
    "JENKINS_API_TOKEN",
)
placeholder = re.compile(
    r"^(sk-\u2026|sk-\.\.\.|<.*>|changeme|your-.*|TODO|xxx+)$",
    re.I,
)
fail = 0
for key in keys:
    for m in re.finditer(rf"(?m)^{re.escape(key)}\s*=\s*(.+)$", text):
        raw = m.group(1).strip().strip('"').strip("'")
        if not raw or placeholder.match(raw) or raw.endswith("\u2026") or raw.endswith("..."):
            continue
        if len(raw) >= 8:
            print(path, file=sys.stderr)
            fail = 1
            break
if re.search(r"(?m)(^|[^A-Za-z0-9])sk-[A-Za-z0-9]{16,}", text):
    # docs use sk-… (ellipsis) which does not match [A-Za-z0-9]
    print(path, file=sys.stderr)
    fail = 1
raise SystemExit(fail)
PY
}

content_hit=0
while IFS= read -r path; do
  [ -n "$path" ] || continue
  if ! scan_blob "$path"; then
    note "key-shaped value in tracked file: $path"
    content_hit=1
  fi
done < <(git ls-files)

if [ "$fail" -ne 0 ]; then
  echo "Secret check failed. Unstage those paths; keys live in gitignored .env or Jenkins credentials." >&2
  exit 1
fi
echo "OK no tracked secret files or key-shaped values"
exit 0
