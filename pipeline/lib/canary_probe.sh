#!/usr/bin/env bash
# Isolation canary probes (report P0.6). Records facts only; canary_verdict.py
# on the host decides pass or fail.
#
# Runs inside the Harbor task container and needs only bash, coreutils, find,
# git and curl. CanaryAgent uploads it to /installed-agent/canary_probe.sh with
# the rendered spec (canary_spec.txt: "host <kind> <name>", "name <glob>",
# "path <glob>", "hash <sha256>", "prune <path>",
# "timeout <curl|model|find|git> <seconds>", "attempts model <n>").
# A name match is recorded only when the file's stripped lines hash to one
# of those sha256 values. The spec carries hashes, never gold text.
#
#   canary_probe.sh home <tag>   iCode home state -> canary_home_<tag>.json
#   canary_probe.sh probe        As the agent user: egress, filesystem search,
#                                mount write, repo history, env names -> canary.json
#   canary_probe.sh mount        As root: write to the mount -> canary_root.json
#
# Exits 0, except when it cannot create a temp file: that is a starved host,
# not a verdict, so it exits 1. Harbor treats `curl: (N)` in a failed command
# as a network error, and a blocked host is the expected result here.
set -uo pipefail

SCHEMA=mac-k3d-canary-probe-v1
LOG_DIR=${CANARY_LOG_DIR:-/logs/agent}
SPEC=${CANARY_SPEC:-/installed-agent/canary_spec.txt}
SEARCH_ROOT=${CANARY_SEARCH_ROOT:-/}
MOUNT=${CANARY_MOUNT:-/opt/icode-host}
MOUNTS_FILE=${CANARY_MOUNTS_FILE:-/proc/mounts}
CAPTURE=${CANARY_CAPTURE:-/installed-agent/icode_capture.sh}
CURL_TIMEOUT=10
MODEL_TIMEOUT=30
MODEL_ATTEMPTS=3
FIND_TIMEOUT=300
GIT_TIMEOUT=300
MAX_HITS=50

# Stop when the host cannot fork or the disk is full. An empty path must not
# be read as a mount or filesystem verdict.
need_temp() {
  local path
  if [ "$#" -eq 0 ]; then
    path=$(mktemp) || true
  else
    path=$(mktemp "$@") || true
  fi
  if [ -z "${path:-}" ] || [ ! -e "$path" ]; then
    echo "cannot create a temp file (out of processes or disk?)" >&2
    exit 1
  fi
  printf '%s\n' "$path"
}

HOSTS=()
NAMES=()
PATHS=()
HASHES=()
PRUNE=()

json_str() {
  local s
  s=$(printf '%s' "$1" | tr -d '\000-\010\013\014\016-\037')
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\t'/\\t}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  printf '"%s"' "$s"
}

json_or_null() {
  if [ -n "$1" ]; then json_str "$1"; else printf 'null'; fi
}

json_int_or_null() {
  case "$1" in
    '' | *[!0-9]*) printf 'null' ;;
    *) printf '%s' "$1" ;;
  esac
}

json_bool() {
  if [ "$1" = 1 ]; then printf 'true'; else printf 'false'; fi
}

json_tri() {
  case "$1" in
    1) printf 'true' ;;
    0) printf 'false' ;;
    *) printf 'null' ;;
  esac
}

# JSON array of the non-empty lines of file $1.
json_lines() {
  local first=1 line
  printf '['
  if [ -n "$1" ] && [ -f "$1" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      [ -n "$line" ] || continue
      [ "$first" = 1 ] || printf ', '
      first=0
      json_str "$line"
    done <"$1"
  fi
  printf ']'
}

first_line() {
  head -n 1 "$1" 2>/dev/null | cut -c1-200
}

have() {
  command -v "$1" >/dev/null 2>&1
}

# Bounded run: `timeout` when coreutils has it, plain otherwise.
bounded() {
  local secs="$1"
  shift
  if have timeout; then timeout "$secs" "$@"; else "$@"; fi
}

load_spec() {
  local key a b
  [ -f "$SPEC" ] || return 0
  while read -r key a b || [ -n "$key" ]; do
    case "$key" in
      host) [ -n "$b" ] && HOSTS+=("$a $b") ;;
      name) [ -n "$a" ] && NAMES+=("$a") ;;
      path) [ -n "$a" ] && PATHS+=("$a") ;;
      hash) [ -n "$a" ] && HASHES+=("$a") ;;
      prune) [ -n "$a" ] && PRUNE+=("$a") ;;
      timeout)
        case "$a:$b" in
          curl:[0-9]*) CURL_TIMEOUT=$b ;;
          model:[0-9]*) MODEL_TIMEOUT=$b ;;
          find:[0-9]*) FIND_TIMEOUT=$b ;;
          git:[0-9]*) GIT_TIMEOUT=$b ;;
        esac
        ;;
      attempts)
        case "$a:$b" in
          model:[1-9]*) MODEL_ATTEMPTS=$b ;;
        esac
        ;;
    esac
  done <"$SPEC"
}

write_json() {
  local out="$LOG_DIR/$1" tmp
  mkdir -p "$LOG_DIR"
  tmp="$out.tmp.$$"
  cat >"$tmp" && mv -f "$tmp" "$out"
}

truthy() {
  case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in
    1 | true | yes | on) return 0 ;;
  esac
  return 1
}

cmd_home() {
  local tag="${1:-post}" home icode agents research=0
  home=${HOME:-}
  icode=${ICODE_HOME:-$home/.icode}
  agents=${AGENTS_HOME:-$home/.agents}
  truthy "${OPENJIUWEN_CLI_RESEARCH_SUBAGENT:-}" && research=1
  {
    printf '{\n'
    printf '  "schema": %s,\n' "$(json_str "$SCHEMA")"
    printf '  "tag": %s,\n' "$(json_str "$tag")"
    printf '  "home": %s,\n' "$(json_or_null "$home")"
    printf '  "icode_home_env": %s,\n' "$(json_or_null "${ICODE_HOME:-}")"
    printf '  "icode_home": %s,\n' "$(json_or_null "$icode")"
    printf '  "icode_home_exists": %s,\n' "$(json_bool "$([ -d "$icode" ] && echo 1)")"
    printf '  "mcp_json": %s,\n' "$(json_bool "$([ -e "$icode/mcp.json" ] && echo 1)")"
    printf '  "settings_json": %s,\n' "$(json_bool "$([ -e "$icode/settings.json" ] && echo 1)")"
    printf '  "agents_home": %s,\n' "$(json_or_null "$agents")"
    printf '  "agents_home_exists": %s,\n' "$(json_bool "$([ -d "$agents" ] && echo 1)")"
    printf '  "research_subagent": %s\n' "$(json_bool "$research")"
    printf '}\n'
  } | write_json "canary_home_${tag}.json"
}

# The model host must answer, so it gets a longer timeout and retries; a host
# that must stay blocked gets one short try.
probe_network() {
  local first=1 entry kind host code rc errf secs tries n
  errf=$(need_temp) || exit 1
  printf '['
  for entry in ${HOSTS[@]+"${HOSTS[@]}"}; do
    kind=${entry%% *}
    host=${entry#* }
    code=""
    rc=""
    n=0
    secs=$CURL_TIMEOUT
    tries=1
    if [ "$kind" = model ]; then
      secs=$MODEL_TIMEOUT
      tries=$MODEL_ATTEMPTS
    fi
    : >"$errf"
    if have curl; then
      while [ "$n" -lt "$tries" ]; do
        n=$((n + 1))
        code=$(bounded $((secs * 2 + 5)) curl -sS -o /dev/null -w '%{http_code}' \
          --connect-timeout "$secs" --max-time $((secs * 2)) "https://$host/" 2>"$errf")
        rc=$?
        case "$code" in
          '' | 000) ;;
          *) break ;;
        esac
      done
    fi
    [ "$first" = 1 ] || printf ', '
    first=0
    printf '{"kind": %s, "host": %s, "curl_exit": %s, "http_code": %s, "attempts": %s, "error": %s}' \
      "$(json_str "$kind")" "$(json_str "$host")" "$(json_int_or_null "$rc")" \
      "$(json_or_null "$code")" "$(json_int_or_null "$n")" "$(json_or_null "$(first_line "$errf")")"
  done
  printf ']'
  rm -f "$errf"
}

# sha256 of non-empty stripped lines joined by newlines, matching
# canary_verdict.content_fingerprint. Prints nothing and returns 1 when the
# file has no such lines. Never prints the file body.
file_fingerprint() {
  local file="$1" payload
  [ -f "$file" ] && [ -r "$file" ] || return 1
  payload=$(sed 's/^[[:space:]]*//;s/[[:space:]]*$//' "$file" | sed '/^$/d' | paste -sd '\n' -)
  [ -n "$payload" ] || return 1
  printf '%s' "$payload" | sha256sum | awk '{print $1}'
}

hash_kept() {
  local want="$1" have
  for have in ${HASHES[@]+"${HASHES[@]}"}; do
    [ "$have" = "$want" ] && return 0
  done
  return 1
}

# find <root> \( -path P1 -o ... \) -prune -o \( -name N1 -o -path T1 ... \) -print
probe_filesystem() {
  local repo="$1" hits pruned kept rc=0 timed_out=0 p first digest
  local -a expr=() prune=()
  hits=$(need_temp) || exit 1
  pruned=$(need_temp) || exit 1
  kept=$(need_temp) || exit 1
  prune=(${PRUNE[@]+"${PRUNE[@]}"})
  [ -n "$repo" ] && prune+=("$repo")
  printf '%s\n' ${prune[@]+"${prune[@]}"} >"$pruned"
  first=1
  for p in ${prune[@]+"${prune[@]}"}; do
    [ "$first" = 1 ] || expr+=(-o)
    first=0
    expr+=(-path "$p")
  done
  local -a match=()
  first=1
  for p in ${NAMES[@]+"${NAMES[@]}"}; do
    [ "$first" = 1 ] || match+=(-o)
    first=0
    match+=(-name "$p")
  done
  for p in ${PATHS[@]+"${PATHS[@]}"}; do
    [ "$first" = 1 ] || match+=(-o)
    first=0
    match+=(-path "$p")
  done
  if [ "${#match[@]}" -gt 0 ]; then
    if [ "${#expr[@]}" -gt 0 ]; then
      bounded "$FIND_TIMEOUT" find "$SEARCH_ROOT" \( "${expr[@]}" \) -prune -o \( "${match[@]}" \) -print \
        2>/dev/null | head -n "$MAX_HITS" >"$hits"
    else
      bounded "$FIND_TIMEOUT" find "$SEARCH_ROOT" \( "${match[@]}" \) -print 2>/dev/null |
        head -n "$MAX_HITS" >"$hits"
    fi
    rc=${PIPESTATUS[0]}
  fi
  [ "$rc" = 124 ] && timed_out=1
  # Keep a path only when its contents match a gold hash. A shared name is not enough.
  if [ -s "$hits" ]; then
    while IFS= read -r p || [ -n "$p" ]; do
      [ -n "$p" ] || continue
      digest=$(file_fingerprint "$p") || continue
      hash_kept "$digest" && printf '%s\n' "$p" >>"$kept"
    done <"$hits"
  fi
  printf '{"root": %s, "pruned": %s, "patterns": %s, "hits": %s, "max_hits": %s, "exit_code": %s, "timed_out": %s}' \
    "$(json_str "$SEARCH_ROOT")" "$(json_lines "$pruned")" \
    "$(( ${#NAMES[@]} + ${#PATHS[@]} ))" "$(json_lines "$kept")" "$MAX_HITS" \
    "$(json_int_or_null "$rc")" "$(json_bool "$timed_out")"
  rm -f "$hits" "$pruned" "$kept"
}

probe_mount() {
  local present=0 options="" ro="" rc errf
  errf=$(need_temp) || exit 1
  [ -d "$MOUNT" ] && present=1
  if [ -r "$MOUNTS_FILE" ]; then
    options=$(awk -v m="$MOUNT" '$2 == m { print $4 }' "$MOUNTS_FILE" | tail -n 1)
  fi
  if [ -n "$options" ]; then
    case ",$options," in
      *,ro,*) ro=1 ;;
      *) ro=0 ;;
    esac
  fi
  touch "$MOUNT/.mac-k3d-canary-write" 2>"$errf"
  rc=$?
  [ "$rc" = 0 ] && rm -f "$MOUNT/.mac-k3d-canary-write"
  printf '{"path": %s, "present": %s, "options": %s, "read_only": %s, "touch_exit": %s, "touch_error": %s}' \
    "$(json_str "$MOUNT")" "$(json_bool "$present")" "$(json_or_null "$options")" "$(json_tri "$ro")" \
    "$(json_int_or_null "$rc")" "$(json_or_null "$(first_line "$errf")")"
  rm -f "$errf"
}

probe_history() {
  local repo="$1" error="$2" all="" at_head="" fsck_rc="" fsck_timed_out=0 unreachable="" stash="" tmp
  tmp=$(need_temp -d) || exit 1
  : >"$tmp/unreachable.txt"
  : >"$tmp/remotes.txt"
  if [ -z "$error" ]; then
    all=$(git -C "$repo" rev-list --all --count 2>/dev/null)
    at_head=$(git -C "$repo" rev-list HEAD --count 2>/dev/null)
    bounded "$GIT_TIMEOUT" git -C "$repo" fsck --unreachable --no-reflogs --no-progress \
      >"$tmp/fsck.txt" 2>/dev/null
    fsck_rc=$?
    [ "$fsck_rc" = 124 ] && fsck_timed_out=1
    grep '^unreachable ' "$tmp/fsck.txt" >"$tmp/all_unreachable.txt" 2>/dev/null
    unreachable=$(wc -l <"$tmp/all_unreachable.txt" | tr -d ' ')
    head -n 5 "$tmp/all_unreachable.txt" >"$tmp/unreachable.txt"
    git -C "$repo" remote >"$tmp/remotes.txt" 2>/dev/null
    stash=$(git -C "$repo" stash list 2>/dev/null | wc -l | tr -d ' ')
  fi
  printf '{"path": %s, "error": %s, "all_count": %s, "head_count": %s, "unreachable": %s, "unreachable_sample": %s, "fsck_exit": %s, "fsck_timed_out": %s, "remotes": %s, "stash": %s}' \
    "$(json_or_null "$repo")" "$(json_or_null "$error")" "$(json_int_or_null "$all")" \
    "$(json_int_or_null "$at_head")" "$(json_int_or_null "$unreachable")" "$(json_lines "$tmp/unreachable.txt")" \
    "$(json_int_or_null "$fsck_rc")" "$(json_bool "$fsck_timed_out")" "$(json_lines "$tmp/remotes.txt")" \
    "$(json_int_or_null "$stash")"
  rm -rf "$tmp"
}

cmd_probe() {
  local repo="" repo_error="" errf names network filesystem mount_json history
  load_spec
  # Same repo iCode gets: icode_capture.sh base, with the task's declared MAC_K3D_REPO.
  export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'
  errf=$(need_temp) || exit 1
  if [ -f "$CAPTURE" ]; then
    repo=$(bash "$CAPTURE" base 2>"$errf" | tail -n 1)
    [ -n "$repo" ] && [ -d "$repo" ] || repo_error="repo not found: $(first_line "$errf")"
  else
    repo_error="missing $CAPTURE"
  fi
  rm -f "$errf"
  names=$(need_temp) || exit 1
  compgen -e | LC_ALL=C sort -u >"$names"
  # Run before the pipe: a failing temp file must exit this process, not the
  # pipe's subshell.
  network=$(probe_network) || exit 1
  filesystem=$(probe_filesystem "$repo") || exit 1
  mount_json=$(probe_mount) || exit 1
  history=$(probe_history "$repo" "$repo_error") || exit 1
  {
    printf '{\n'
    printf '  "schema": %s,\n' "$(json_str "$SCHEMA")"
    printf '  "uid": %s,\n' "$(json_int_or_null "$(id -u 2>/dev/null)")"
    printf '  "user": %s,\n' "$(json_or_null "$(id -un 2>/dev/null)")"
    printf '  "curl": %s,\n' "$(json_bool "$(have curl && echo 1)")"
    printf '  "network": %s,\n' "$network"
    printf '  "filesystem": %s,\n' "$filesystem"
    printf '  "mount": %s,\n' "$mount_json"
    printf '  "repo": %s,\n' "$history"
    printf '  "env_names": %s,\n' "$(json_lines "$names")"
    printf '  "python_env": {"PYTHONPATH": %s, "VIRTUAL_ENV": %s}\n' \
      "$(json_or_null "${PYTHONPATH:-}")" "$(json_or_null "${VIRTUAL_ENV:-}")"
    printf '}\n'
  } | write_json canary.json
  rm -f "$names"
}

cmd_mount() {
  local rc errf erofs=0
  errf=$(need_temp) || exit 1
  touch "$MOUNT/.mac-k3d-canary-root" 2>"$errf"
  rc=$?
  [ "$rc" = 0 ] && rm -f "$MOUNT/.mac-k3d-canary-root"
  grep -qi 'read-only' "$errf" && erofs=1
  {
    printf '{\n'
    printf '  "schema": %s,\n' "$(json_str "$SCHEMA")"
    printf '  "uid": %s,\n' "$(json_int_or_null "$(id -u 2>/dev/null)")"
    printf '  "path": %s,\n' "$(json_str "$MOUNT")"
    printf '  "touch_exit": %s,\n' "$(json_int_or_null "$rc")"
    printf '  "touch_error": %s,\n' "$(json_or_null "$(first_line "$errf")")"
    printf '  "read_only_error": %s\n' "$(json_bool "$erofs")"
    printf '}\n'
  } | write_json canary_root.json
  rm -f "$errf"
}

case "${1:-}" in
  home) cmd_home "${2:-post}" ;;
  probe) cmd_probe ;;
  mount) cmd_mount ;;
  *)
    echo "usage: canary_probe.sh home <tag>|probe|mount" >&2
    exit 2
    ;;
esac
exit 0
