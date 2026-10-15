#!/usr/bin/env bash
# Fail on Linux-only shell in pipeline/ and scripts/: every worker script must
# also run on macOS (BSD userland). A line passes when it carries its fallback,
# sits under an OS guard (`uname -s` within the 14 lines above it), or ends
# with `# portable: <why>`. Scripts that say "Runs inside the Harbor task
# container" in their header only ever run on Linux and are skipped.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

fail=0
while IFS= read -r file; do
  [ "$file" != "scripts/check_shell_portability.sh" ] || continue
  if head -n 12 "$file" | grep -q 'inside the Harbor task container'; then
    continue
  fi
  has_shasum=0
  grep -q 'shasum' "$file" && has_shasum=1
  awk -v file="$file" -v has_shasum="$has_shasum" '
    function report(why) { printf "%s:%d: %s: %s\n", file, FNR, why, $0; bad = 1 }
    {
      hist[FNR] = $0
      if ($0 ~ /^[[:space:]]*#/) next
      code = $0
      sub(/[[:space:]]#[^"'\'']*$/, "", code)
      if ($0 ~ /# portable:/) next
      guarded = 0
      for (i = 1; i <= 14 && FNR - i > 0; i++)
        if (hist[FNR - i] ~ /uname -s|OSTYPE/) { guarded = 1; break }
      checked = 0
      for (i = 0; i <= 3 && FNR - i > 0; i++)
        if (hist[FNR - i] ~ /have timeout|command -v timeout/) { checked = 1; break }

      if (code ~ /\/proc\// && !guarded) report("/proc is Linux-only (macOS: sysctl)")
      if (code ~ /(^|[^a-z_-])ip (route|-o link|link|addr)/ && !guarded) report("ip is Linux-only (macOS: route -n get, ifconfig)")
      if (code ~ /stat -c/ && code !~ /stat -f/ && !guarded) report("stat -c needs a stat -f fallback")
      if (code ~ /sha256sum/ && !has_shasum && !guarded) report("sha256sum needs a shasum -a 256 fallback")
      if (code ~ /(^|[[:space:];&|(])timeout[[:space:]]+[0-9"$]/ && !checked) report("timeout is not on macOS; check have timeout first")
      if (code ~ /readlink -f/ && !guarded) report("readlink -f is missing on older macOS")
      if (code ~ /sed[[:space:]]+(-[A-Za-z]+[[:space:]]+)*-i([[:space:]]|$|\047|")/) report("sed -i needs a suffix on BSD sed (use -i.bak)")
      if (code ~ /mktemp[[:space:]][^|;]*XXX+[^X[:space:]"\047)]/) report("BSD mktemp only fills trailing Xs")
      if (code ~ /date[[:space:]]+-d[[:space:]]/ && !guarded) report("date -d is GNU-only")
      if (code ~ /(^|[^a-z_-])free[[:space:]]+-/ && !guarded) report("free is Linux-only")
      if (code ~ /-printf/) report("find -printf is GNU-only")
      if (code ~ /grep[[:space:]]+-[A-Za-z]*P/) report("grep -P is GNU-only")
      if (code ~ /base64[[:space:]]+-w/) report("base64 -w is GNU-only")
      if (code ~ /(^|[^a-z_-])nproc([^a-z_-]|$)/ && code !~ /hw\.ncpu/ && !guarded) report("nproc is missing on macOS (sysctl -n hw.ncpu)")
    }
    END { exit bad }
  ' "$file" || fail=1
done < <(find pipeline scripts -name '*.sh' -type f | sort)

if [ "$fail" -ne 0 ]; then
  echo "ERROR: Linux-only shell above; add a Darwin branch or fallback, or '# portable: <why>'" >&2
  exit 1
fi
echo "OK shell portability (pipeline/, scripts/)"
