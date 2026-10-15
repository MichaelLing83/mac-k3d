#!/usr/bin/env bash
# tasks/harness: the harness under test (HARNESS=icode or jiuwenswarm), ready
# to mount read-only in every trial. Comes after tasks/images, which settles
# the task containers' platform.
#
# A harness is prepared once per input and platform and kept in this worker's
# harness store (pipeline/lib/harness_store.py). A shard handed its
# dispatcher's bundle (HARNESS_BUNDLE_SHA256) uses that bundle and no other;
# a build without one looks up what it asks for (a commit, a release sha256),
# and prepares and stores it on a miss. A stored tree is checked against its
# digest before every use. Reads the harness's checkout; never changes its source.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"
# shellcheck source=../../lib/harness_lib.sh
source "$PIPELINE_LIB/harness_lib.sh"

# A reused workspace must not carry the previous run's harness or its identity.
rm -f "$WORKDIR/icode_git.json" "$WORKDIR/icode_release.json" "$WORKDIR/harness.json" \
  "$WORKDIR/harness_host_root.txt" "$WORKDIR/icode_host_root.txt" "$WORKDIR/icode_bin_path.txt" \
  "$WORKDIR/.harness-bundle.json" "$WORKDIR/.harness-bundle.tar.gz" \
  "$WORKDIR/jiuwenswarm-release.bin" "$WORKDIR/jiuwenswarm-release.bin.url"

harness_check_supported
case "$HARNESS" in
  icode)
    ICODE_MODE="$(icode_normalize_mode)"
    export ICODE_MODE
    ;;
  jiuwenswarm)
    JIUWENSWARM_MODE="$(jiuwenswarm_normalize_mode)"
    export JIUWENSWARM_MODE
    ;;
esac
echo "harness: $(harness_display) ($(harness_mode))"

write_container_platform
PLATFORM="$(task_container_platform)"
if task_platform_emulated; then
  ensure_platform_runs "$PLATFORM"
fi

harness_resolve "$PLATFORM"
harness_use "$PLATFORM"
harness_assert_identity
harness_print_identity
if [ -s "$WORKDIR/icode_git.json" ]; then
  cp "$WORKDIR/icode_git.json" "$OUTPUT_DIR/icode_git.json"
fi

if [ -n "$HARNESS_ENTRY" ]; then
  harness_store evict --store "$(harness_store_root)" --harness "$HARNESS" \
    || echo "WARNING: harness store eviction failed; nothing removed" >&2
fi
