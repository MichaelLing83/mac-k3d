#!/usr/bin/env bash
# Capture the agent's work for the grader and leave a receipt.
#
# Runs inside the Harbor task container as the agent user, so it needs only
# bash, git and coreutils. ICodeAgent and PatchAgent upload it to
# /installed-agent/icode_capture.sh.
#
#   icode_capture.sh base      Before the agent. Print the repo, write base_sha.txt
#                              and base_untracked.txt. Exit 3 when the declared repo
#                              is not a git top-level.
#   icode_capture.sh capture   After the agent. Remove runtime artifacts, build the
#                              standard patch (git diff --binary <base>), hand it to
#                              the suite's grader, commit, write capture.json.
#
# Every suite runs the same steps. Only delivery differs, because each grader
# reads a different file:
#   lolbench              lolbench-submit writes /logs/artifacts/solution.patch. It
#                         diffs the tree against the index, so it runs before the
#                         commit, and the standard patch replaces its output when
#                         the two differ (for example when the agent committed).
#   deepswe, swebenchpro  The task's verifier.collect hook writes model.patch from
#                         the repo after the agent exits. DeepSWE diffs <base>..HEAD,
#                         so the work must be committed.
set -uo pipefail

SCHEMA=mac-k3d-capture-v1
LOG_DIR=${CAPTURE_LOG_DIR:-/logs/agent}
ART_DIR=${CAPTURE_ARTIFACTS_DIR:-/logs/artifacts}
SEARCH_ROOTS=${CAPTURE_SEARCH_ROOTS:-/workspace /app}
LB_META=${LOLBENCH_METADATA:-/opt/lolbench/metadata.json}
LB_WORKSPACE=${LOLBENCH_WORKSPACE:-/workspace}
LB_UNTRACKED=${LOLBENCH_BASE_UNTRACKED:-/opt/lolbench/base_untracked.txt}
BENCH=$(printf '%s' "${MAC_K3D_BENCHMARK:-deepswe}" | tr '[:upper:]' '[:lower:]')
OVERSIZE_BYTES=1048576
# Untracked directories with these names are runtime output, never part of a patch.
CLEAN_NAMES="target build node_modules __pycache__ .pytest_cache .agent_history"

STAGE=""
REPO=""
REPO_SOURCE=""
REPO_DECLARED=${MAC_K3D_REPO:-}
# One Harbor job covers many tasks, so evaluate/harbor_run declares every selected task's repo and
# the trial picks the one its own image actually has.
REPO_CANDIDATES=${MAC_K3D_REPO_CANDIDATES:-}
REPO_SCAN=""
REPO_ERROR=""
SAFE_COMPLAINT=0
BASE=""
BASE_SOURCE=none
DECLARED_BASE=${MAC_K3D_BASE_COMMIT:-}
DECLARED_BASE_SHA=""
BASE_MATCHES=""
HEAD_BEFORE=""
FINAL_HEAD=""
STATUS_BEFORE=""
STATUS_AFTER=""
NEW_FILES=0
STAT_FILES=0
STAT_INS=0
STAT_DEL=0
PATCH_PATH=""
PATCH_BYTES=0
PATCH_SHA=""
PATCH_SOURCE=""
KEPT=0
COMMITTED_SHA=""
DELIVERY_METHOD=""
DELIVERY_PATH=""
SUBMIT_RAN=0
SUBMIT_RC=""
SUBMIT_SHA=""
REPLACED=0
COMMIT_RAN=0
COMMIT_RC=""
WORK=""
ERRORS=()
FLAGS=()

err() {
  ERRORS+=("$1")
  echo "capture: $1" >&2
}

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

json_array() {
  local first=1 item
  printf '['
  for item in "$@"; do
    [ "$first" = 1 ] || printf ', '
    first=0
    json_str "$item"
  done
  printf ']'
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

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  elif command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

bytes_of() {
  wc -c <"$1" | tr -d ' '
}

g() {
  git -C "$REPO" -c core.quotepath=off "$@"
}

scan_repo() {
  local root hit
  for root in $SEARCH_ROOTS; do
    [ -d "$root" ] || continue
    hit=$(find "$root" -maxdepth 3 -type d -name .git 2>/dev/null | head -1 || true)
    if [ -n "$hit" ]; then
      dirname "$hit"
      return 0
    fi
  done
}

metadata_repo() {
  local project
  [ -f "$LB_META" ] || return 0
  project=$(sed -n 's/.*"project"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$LB_META" | head -1)
  if [ -n "$project" ]; then
    printf '%s/%s\n' "$LB_WORKSPACE" "$project"
  fi
}

check_repo() {
  local errf top want
  if [ ! -d "$REPO" ]; then
    REPO_ERROR="repo $REPO ($REPO_SOURCE) does not exist"
    return 1
  fi
  errf=$(mktemp)
  top=$(git -C "$REPO" rev-parse --show-toplevel 2>"$errf")
  if grep -Eqi 'dubious ownership|safe\.directory' "$errf"; then
    # Recorded in the receipt. The override lasts only for this script and
    # the lolbench-submit it starts.
    SAFE_COMPLAINT=1
    export GIT_CONFIG_COUNT=1 GIT_CONFIG_KEY_0=safe.directory GIT_CONFIG_VALUE_0='*'
    top=$(git -C "$REPO" rev-parse --show-toplevel 2>"$errf")
  fi
  rm -f "$errf"
  if [ -z "$top" ]; then
    REPO_ERROR="repo $REPO ($REPO_SOURCE) is not a git work tree"
    return 1
  fi
  want=$(cd "$REPO" && pwd -P)
  if [ "$top" != "$want" ]; then
    REPO_ERROR="repo $REPO ($REPO_SOURCE) is not a git top-level (top-level is $top)"
    return 1
  fi
}

# Declared repo first (the evaluate phase passes MAC_K3D_REPO, or MAC_K3D_REPO_CANDIDATES when one
# Harbor job covers several tasks), then the LoLBench image metadata, then the
# first .git under the search roots.
resolve_repo() {
  local meta cand
  REPO_SCAN=$(scan_repo)
  if [ -z "$REPO_DECLARED" ] && [ -n "$REPO_CANDIDATES" ]; then
    local IFS=:
    for cand in $REPO_CANDIDATES; do
      [ -n "$cand" ] || continue
      if [ -d "$cand/.git" ]; then
        REPO_DECLARED=$cand
        break
      fi
    done
  fi
  if [ -n "$REPO_DECLARED" ]; then
    REPO=$REPO_DECLARED
    REPO_SOURCE=declared
  else
    meta=$(metadata_repo)
    if [ -n "$meta" ] && [ -d "$meta" ]; then
      REPO=$meta
      REPO_SOURCE=metadata
    elif [ -n "$REPO_SCAN" ]; then
      REPO=$REPO_SCAN
      REPO_SOURCE=scan
    else
      REPO_SOURCE=fallback
      if [ -d /app ]; then REPO=/app; else REPO=/workspace; fi
    fi
  fi
  check_repo
}

pick_base() {
  if [ -s "$LOG_DIR/base_sha.txt" ]; then
    BASE=$(head -n 1 "$LOG_DIR/base_sha.txt" | tr -d '[:space:]')
    if g cat-file -e "${BASE}^{commit}" 2>/dev/null; then
      BASE_SOURCE=file
    else
      err "base_sha.txt holds '$BASE', which is not a commit in $REPO"
      BASE=""
    fi
  else
    err "no base_sha.txt in $LOG_DIR (base step did not run)"
  fi
  if [ -n "$DECLARED_BASE" ]; then
    DECLARED_BASE_SHA=$(g rev-parse --verify -q "${DECLARED_BASE}^{commit}" 2>/dev/null)
    [ -n "$DECLARED_BASE_SHA" ] || err "declared base '$DECLARED_BASE' is not a commit in $REPO"
  fi
  if [ -z "$BASE" ]; then
    if [ -n "$DECLARED_BASE_SHA" ]; then
      BASE=$DECLARED_BASE_SHA
      BASE_SOURCE=declared
    else
      BASE=$(g rev-parse --verify -q HEAD 2>/dev/null)
      [ -n "$BASE" ] && BASE_SOURCE=head
    fi
  fi
  if [ -n "$DECLARED_BASE_SHA" ] && [ -n "$BASE" ]; then
    if [ "$BASE" = "$DECLARED_BASE_SHA" ]; then BASE_MATCHES=1; else BASE_MATCHES=0; fi
  fi
}

untracked_now() {
  g ls-files --others --exclude-standard 2>/dev/null | LC_ALL=C sort
}

# Remove untracked runtime directories the agent created. Never touches a
# directory that holds a tracked file or a file that was untracked at base.
cleanup() {
  local dir
  : >"$WORK/removed.txt"
  untracked_now >"$WORK/untracked.txt"
  LC_ALL=C comm -23 "$WORK/untracked.txt" "$WORK/pre.txt" |
    awk -v names="$CLEAN_NAMES" '
      BEGIN { n = split(names, a, " "); for (i = 1; i <= n; i++) want[a[i]] = 1 }
      {
        k = split($0, p, "/"); d = ""
        for (i = 1; i < k; i++) { d = d p[i] "/"; if (p[i] in want) { print d; next } }
        if (p[k] == ".agent_history") print $0
      }' |
    LC_ALL=C sort -u >"$WORK/candidates.txt"
  while IFS= read -r dir; do
    [ -n "$dir" ] || continue
    if [ -n "$(g --literal-pathspecs ls-files -- "$dir" 2>/dev/null | head -n 1)" ]; then
      continue
    fi
    if awk -v d="$dir" 'index($0, d) == 1 { found = 1; exit } END { exit !found }' "$WORK/pre.txt"; then
      continue
    fi
    if rm -rf -- "${REPO:?}/$dir"; then
      printf '%s\n' "$dir" >>"$WORK/removed.txt"
      echo "capture: removed untracked runtime output $dir" >&2
    else
      err "could not remove $dir"
    fi
  done <"$WORK/candidates.txt"
}

# Patch of record: everything since base, with files that were untracked at
# base left out (the same rule lolbench-submit applies).
standard_patch() {
  untracked_now >"$WORK/untracked.txt"
  LC_ALL=C comm -23 "$WORK/untracked.txt" "$WORK/pre.txt" >"$WORK/new.txt"
  NEW_FILES=$(wc -l <"$WORK/new.txt" | tr -d ' ')
  if [ -s "$WORK/new.txt" ]; then
    tr '\n' '\0' <"$WORK/new.txt" | (cd "$REPO" && xargs -0 -r git --literal-pathspecs add -N --) ||
      err "git add -N failed for new files"
  fi
  : >"$WORK/std.patch"
  if [ -z "$BASE" ]; then
    err "no base commit; the standard patch is empty"
    return 0
  fi
  if ! g diff --binary --no-ext-diff --no-color "$BASE" >"$WORK/std.patch" 2>"$WORK/diff.err"; then
    err "git diff --binary $BASE failed: $(head -c 300 "$WORK/diff.err")"
  fi
}

# Never replace a non-empty patch with an empty one.
keep_existing() {
  local prev=""
  RECORD="$WORK/std.patch"
  PATCH_SOURCE=base_diff
  if [ "$BENCH" = lolbench ] && [ -s "$ART_DIR/solution.patch" ]; then
    prev="$ART_DIR/solution.patch"
  elif [ -s "$LOG_DIR/capture.patch" ]; then
    prev="$LOG_DIR/capture.patch"
  fi
  if [ ! -s "$WORK/std.patch" ] && [ -n "$prev" ]; then
    cp -f "$prev" "$WORK/prev.patch"
    RECORD="$WORK/prev.patch"
    PATCH_SOURCE=kept_existing
    KEPT=1
    echo "capture: the new diff is empty; keeping the existing non-empty patch $prev" >&2
  fi
}

deliver() {
  local want
  DELIVERY_METHOD=verifier.collect
  DELIVERY_PATH="$ART_DIR/model.patch"
  [ "$BENCH" = lolbench ] || return 0
  DELIVERY_METHOD=lolbench-submit
  DELIVERY_PATH="$ART_DIR/solution.patch"
  if command -v lolbench-submit >/dev/null 2>&1; then
    SUBMIT_RAN=1
    LOLBENCH_PATCH_OUT="$DELIVERY_PATH" lolbench-submit "$REPO" >&2
    SUBMIT_RC=$?
    [ "$SUBMIT_RC" = 0 ] || err "lolbench-submit exited $SUBMIT_RC"
  else
    err "lolbench-submit not found on PATH; writing the standard patch directly"
  fi
  [ -f "$DELIVERY_PATH" ] && SUBMIT_SHA=$(sha256_of "$DELIVERY_PATH")
  want=$(sha256_of "$RECORD")
  if [ ! -f "$DELIVERY_PATH" ] || [ "$SUBMIT_SHA" != "$want" ]; then
    if cp -f "$RECORD" "$DELIVERY_PATH"; then
      REPLACED=1
      echo "capture: replaced lolbench-submit output with the standard patch" >&2
    else
      err "could not write $DELIVERY_PATH"
    fi
  fi
}

# Commit tracked changes and new files, never files that were untracked at base.
commit_work() {
  local errf="$WORK/commit.err"
  : >"$errf"
  # lolbench-submit marks every untracked file intent-to-add when its own list
  # is missing; take files that were untracked at base back out of the index.
  g ls-files 2>/dev/null | LC_ALL=C sort | LC_ALL=C comm -12 - "$WORK/pre.txt" >"$WORK/pre_indexed.txt"
  if [ -s "$WORK/pre_indexed.txt" ]; then
    tr '\n' '\0' <"$WORK/pre_indexed.txt" |
      (cd "$REPO" && xargs -0 -r git --literal-pathspecs rm -q --cached --) 2>>"$errf"
  fi
  g add -u 2>>"$errf"
  if [ -s "$WORK/new.txt" ]; then
    tr '\n' '\0' <"$WORK/new.txt" | (cd "$REPO" && xargs -0 -r git --literal-pathspecs add --) 2>>"$errf"
  fi
  if g diff --cached --quiet 2>/dev/null; then
    return 0
  fi
  COMMIT_RAN=1
  g -c user.name=icode -c user.email=icode@local -c commit.gpgsign=false \
    commit -q --no-verify -m "icode solution" 2>>"$errf"
  COMMIT_RC=$?
  [ "$COMMIT_RC" = 0 ] || err "git commit exited $COMMIT_RC: $(head -c 300 "$errf")"
}

finalize() {
  local numstat
  FINAL_HEAD=$(g rev-parse --verify -q HEAD 2>/dev/null)
  STATUS_AFTER=$(g status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  if [ -n "$BASE" ]; then
    numstat=$(g diff --numstat "$BASE" 2>/dev/null |
      awk -F'\t' '{ f++; if ($1 != "-") i += $1; if ($2 != "-") d += $2 } END { printf "%d %d %d", f, i, d }')
    read -r STAT_FILES STAT_INS STAT_DEL <<<"$numstat"
    if g diff --binary --no-ext-diff --no-color "$BASE" HEAD >"$WORK/committed.patch" 2>/dev/null; then
      COMMITTED_SHA=$(sha256_of "$WORK/committed.patch")
    fi
  fi
  PATCH_PATH="$LOG_DIR/capture.patch"
  cp -f "$RECORD" "$PATCH_PATH" || err "could not write $PATCH_PATH"
  PATCH_BYTES=$(bytes_of "$PATCH_PATH")
  PATCH_SHA=$(sha256_of "$PATCH_PATH")
  : >"$WORK/binary.txt"
  if [ -s "$PATCH_PATH" ]; then
    g apply --numstat "$PATCH_PATH" 2>/dev/null |
      awk -F'\t' '$1 == "-" && $2 == "-" { print $3 }' >"$WORK/binary.txt"
  fi
  if [ "${PATCH_BYTES:-0}" -gt "$OVERSIZE_BYTES" ]; then
    FLAGS+=(patch_oversize)
  fi
  if [ "$BASE_MATCHES" = 0 ]; then
    FLAGS+=(base_mismatch)
  fi
  if [ "$KEPT" = 0 ] && [ -n "$COMMITTED_SHA" ] && [ "$COMMITTED_SHA" != "$PATCH_SHA" ]; then
    FLAGS+=(commit_mismatch)
  fi
}

write_receipt() {
  local out="$LOG_DIR/capture.json" tmp oversize=0
  [ "${PATCH_BYTES:-0}" -gt "$OVERSIZE_BYTES" ] && oversize=1
  [ "${#ERRORS[@]}" -gt 0 ] && FLAGS+=(capture_error)
  mkdir -p "$LOG_DIR"
  tmp="$out.tmp.$$"
  {
    printf '{\n'
    printf '  "schema": %s,\n' "$(json_str "$SCHEMA")"
    printf '  "stage": %s,\n' "$(json_str "$STAGE")"
    printf '  "benchmark": %s,\n' "$(json_str "$BENCH")"
    printf '  "repo": {"path": %s, "source": %s, "declared": %s, "scan_hit": %s},\n' \
      "$(json_or_null "$REPO")" "$(json_or_null "$REPO_SOURCE")" \
      "$(json_or_null "$REPO_DECLARED")" "$(json_or_null "$REPO_SCAN")"
    printf '  "base_sha": %s,\n' "$(json_or_null "$BASE")"
    printf '  "base_source": %s,\n' "$(json_str "$BASE_SOURCE")"
    printf '  "declared_base": %s,\n' "$(json_or_null "$DECLARED_BASE")"
    printf '  "declared_base_sha": %s,\n' "$(json_or_null "$DECLARED_BASE_SHA")"
    printf '  "base_matches_declared": %s,\n' "$(json_tri "$BASE_MATCHES")"
    printf '  "head_before_commit": %s,\n' "$(json_or_null "$HEAD_BEFORE")"
    printf '  "final_head": %s,\n' "$(json_or_null "$FINAL_HEAD")"
    printf '  "status_porcelain": {"before": %s, "after": %s},\n' \
      "$(json_int_or_null "$STATUS_BEFORE")" "$(json_int_or_null "$STATUS_AFTER")"
    printf '  "cleanup": {"removed": %s},\n' "$(json_lines "${WORK:+$WORK/removed.txt}")"
    printf '  "new_files": %s,\n' "$(json_int_or_null "$NEW_FILES")"
    printf '  "diff_stat": {"files": %s, "insertions": %s, "deletions": %s},\n' \
      "$(json_int_or_null "$STAT_FILES")" "$(json_int_or_null "$STAT_INS")" "$(json_int_or_null "$STAT_DEL")"
    printf '  "patch": {"path": %s, "bytes": %s, "sha256": %s, "source": %s, "kept_existing": %s, "binary_paths": %s},\n' \
      "$(json_or_null "$PATCH_PATH")" "$(json_int_or_null "$PATCH_BYTES")" "$(json_or_null "$PATCH_SHA")" \
      "$(json_or_null "$PATCH_SOURCE")" "$(json_bool "$KEPT")" "$(json_lines "${WORK:+$WORK/binary.txt}")"
    printf '  "committed_patch_sha256": %s,\n' "$(json_or_null "$COMMITTED_SHA")"
    printf '  "delivery": {"method": %s, "path": %s, "submit_ran": %s, "submit_exit_code": %s, "submit_sha256": %s, "replaced_submit_output": %s},\n' \
      "$(json_or_null "$DELIVERY_METHOD")" "$(json_or_null "$DELIVERY_PATH")" "$(json_bool "$SUBMIT_RAN")" \
      "$(json_int_or_null "$SUBMIT_RC")" "$(json_or_null "$SUBMIT_SHA")" "$(json_bool "$REPLACED")"
    printf '  "commit": {"ran": %s, "exit_code": %s},\n' "$(json_bool "$COMMIT_RAN")" "$(json_int_or_null "$COMMIT_RC")"
    printf '  "uid": %s,\n' "$(json_int_or_null "$(id -u 2>/dev/null)")"
    printf '  "user": %s,\n' "$(json_or_null "$(id -un 2>/dev/null)")"
    printf '  "safe_directory_complaint": %s,\n' "$(json_bool "$SAFE_COMPLAINT")"
    printf '  "oversize": %s,\n' "$(json_bool "$oversize")"
    printf '  "flags": %s,\n' "$(json_array ${FLAGS[@]+"${FLAGS[@]}"})"
    printf '  "errors": %s\n' "$(json_array ${ERRORS[@]+"${ERRORS[@]}"})"
    printf '}\n'
  } >"$tmp" && mv -f "$tmp" "$out"
}

cmd_base() {
  STAGE=base
  mkdir -p "$LOG_DIR"
  if ! resolve_repo; then
    err "$REPO_ERROR"
    case "$REPO_SOURCE" in
      declared | metadata)
        write_receipt
        exit 3
        ;;
    esac
    : >"$LOG_DIR/base_sha.txt"
    : >"$LOG_DIR/base_untracked.txt"
    printf '%s\n' "$REPO"
    return 0
  fi
  g rev-parse --verify -q HEAD >"$LOG_DIR/base_sha.txt" 2>/dev/null || : >"$LOG_DIR/base_sha.txt"
  untracked_now >"$LOG_DIR/base_untracked.txt"
  printf '%s\n' "$REPO"
}

cmd_capture() {
  STAGE=capture
  mkdir -p "$LOG_DIR" "$ART_DIR"
  WORK=$(mktemp -d)
  trap 'rm -rf "$WORK"' EXIT
  : >"$WORK/removed.txt"
  : >"$WORK/binary.txt"
  if ! resolve_repo; then
    err "$REPO_ERROR"
    write_receipt
    return 3
  fi
  pick_base
  HEAD_BEFORE=$(g rev-parse --verify -q HEAD 2>/dev/null)
  STATUS_BEFORE=$(g status --porcelain 2>/dev/null | wc -l | tr -d ' ')
  { cat "$LOG_DIR/base_untracked.txt" 2>/dev/null; cat "$LB_UNTRACKED" 2>/dev/null; } |
    LC_ALL=C sort -u >"$WORK/pre.txt"
  cleanup
  standard_patch
  keep_existing
  deliver
  commit_work
  finalize
  write_receipt
  echo "capture: $PATCH_BYTES bytes, $STAT_FILES files, sha256 ${PATCH_SHA:-none} -> $PATCH_PATH ($DELIVERY_METHOD)" >&2
}

case "${1:-}" in
  base) cmd_base ;;
  capture) cmd_capture ;;
  *)
    echo "usage: icode_capture.sh base|capture" >&2
    exit 2
    ;;
esac
