#!/usr/bin/env bash
# The dispatcher's "Prepare harness" stage: the harness under test (HARNESS),
# prepared once for the shards' task platform, or found in this worker's
# harness store, and left for Jenkins to archive and hand to every shard:
#
#   $HARNESS_DIR/bundle.tar.gz   the bundle (harness_store.py pack)
#   $HARNESS_DIR/bundle.json     what it holds: harness, identity, platform, recipe
#   $HARNESS_DIR/bundle.sha256   its sha256, the shards' HARNESS_BUNDLE_SHA256
#   $HARNESS_DIR/pin.txt         `git <sha>` or `release <sha256>`, the identity
#                                every shard must find in the bundle
#
# The platform is HARNESS_BUNDLE_PLATFORM (default linux/amd64, what DeepSWE
# runs on Linux workers). A shard whose task containers are another platform
# prepares its own from the same pinned input.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck source=../stages/_common.sh
source "$ROOT/pipeline/stages/_common.sh"
# shellcheck source=../lib/harness_lib.sh
source "$PIPELINE_LIB/harness_lib.sh"

HARNESS_BUNDLE_PLATFORM="${HARNESS_BUNDLE_PLATFORM:-linux/amd64}"
case "$HARNESS_BUNDLE_PLATFORM" in
  linux/amd64 | linux/arm64) ;;
  *) die "HARNESS_BUNDLE_PLATFORM must be linux/amd64 or linux/arm64, not ${HARNESS_BUNDLE_PLATFORM}" ;;
esac
# No tasks are selected here; the bundle is for the platform named above.
task_container_platform() {
  printf '%s\n' "$HARNESS_BUNDLE_PLATFORM"
}

OUT="$HARNESS_DIR"
mkdir -p "$OUT"
rm -f "$OUT/bundle.tar.gz" "$OUT/bundle.json" "$OUT/bundle.sha256" "$OUT/pin.txt" \
  "$WORKDIR/icode_git.json" "$WORKDIR/icode_release.json" "$WORKDIR/harness.json" \
  "$WORKDIR/harness_host_root.txt" "$WORKDIR/icode_host_root.txt" "$WORKDIR/icode_bin_path.txt" \
  "$WORKDIR/.harness-bundle.json" "$WORKDIR/.harness-bundle.tar.gz" \
  "$WORKDIR/jiuwenswarm-release.bin" "$WORKDIR/jiuwenswarm-release.bin.url"
unset HARNESS_BUNDLE_SHA256 HARNESS_BUNDLE_FILE

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
echo "prepare harness: $(harness_display) ($(harness_mode)) for ${HARNESS_BUNDLE_PLATFORM}"

harness_resolve "$HARNESS_BUNDLE_PLATFORM" 1
[ -n "$HARNESS_ENTRY" ] || die "prepare harness: the $(harness_display) tree was not stored"
harness_use "$HARNESS_BUNDLE_PLATFORM"
harness_assert_identity
harness_print_identity

SHA="$(harness_entry_sha "$HARNESS_ENTRY")"
[ -s "$HARNESS_ENTRY/bundle.tar.gz" ] || die "prepare harness: $HARNESS_ENTRY keeps no bundle.tar.gz"
[ "$(harness_store sha256 "$HARNESS_ENTRY/bundle.tar.gz")" = "$SHA" ] \
  || die "prepare harness: $HARNESS_ENTRY/bundle.tar.gz no longer matches its sha256 ${SHA}"
ln -f "$HARNESS_ENTRY/bundle.tar.gz" "$OUT/bundle.tar.gz" 2>/dev/null || cp "$HARNESS_ENTRY/bundle.tar.gz" "$OUT/bundle.tar.gz"
cp "$HARNESS_ENTRY/bundle.json" "$OUT/bundle.json"
printf '%s\n' "$SHA" >"$OUT/bundle.sha256"
harness_pin >"$OUT/pin.txt"
echo "HARNESS_BUNDLE_SHA256=${SHA} ($(du -h "$OUT/bundle.tar.gz" | cut -f1))"

harness_store evict --store "$(harness_store_root)" --harness "$HARNESS" \
  || echo "WARNING: harness store eviction failed; nothing removed" >&2
