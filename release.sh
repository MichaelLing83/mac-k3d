#!/usr/bin/env bash
# Bump the Cargo minor version (X.Y.Z -> X.(Y+1).0), commit, build,
# tag, push, and publish a GitHub latest release with the macOS binary.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ "$(git rev-parse --abbrev-ref HEAD)" != "main" ]]; then
  echo "error: release.sh must run on main (current: $(git rev-parse --abbrev-ref HEAD))" >&2
  exit 1
fi

if [[ -n "$(git status --porcelain)" ]]; then
  echo "error: working tree is dirty; commit or stash changes first" >&2
  git status --short
  exit 1
fi

git fetch origin
if [[ -n "$(git log HEAD..origin/main --oneline 2>/dev/null)" ]]; then
  echo "error: local main is behind origin/main; pull first" >&2
  exit 1
fi

if ! command -v gh >/dev/null 2>&1; then
  echo "error: gh CLI is required to publish the GitHub release" >&2
  exit 1
fi

if ! command -v cargo >/dev/null 2>&1; then
  echo "error: cargo is required to build the release binary" >&2
  exit 1
fi

current="$(awk '
  $0 == "[package]" { in_pkg = 1; next }
  in_pkg && /^\[/ { in_pkg = 0 }
  in_pkg && $1 == "version" {
    gsub(/"/, "", $3)
    print $3
    exit
  }
' Cargo.toml)"

if [[ ! "$current" =~ ^([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
  echo "error: could not parse package version from Cargo.toml (got: ${current:-empty})" >&2
  exit 1
fi

major="${BASH_REMATCH[1]}"
minor="${BASH_REMATCH[2]}"
new_version="${major}.$((minor + 1)).0"
tag="v${new_version}"

if git rev-parse "$tag" >/dev/null 2>&1; then
  echo "error: tag $tag already exists" >&2
  exit 1
fi

echo "Bumping version ${current} -> ${new_version}"

tmp="$(mktemp)"
awk -v ver="$new_version" '
  $0 == "[package]" { in_pkg = 1; print; next }
  in_pkg && /^\[/ { in_pkg = 0 }
  in_pkg && $1 == "version" {
    print "version = \"" ver "\""
    next
  }
  { print }
' Cargo.toml > "$tmp"
mv "$tmp" Cargo.toml

echo "Building release binary…"
cargo build --release

if [[ -n "$(git status --porcelain -- Cargo.lock)" ]]; then
  git add Cargo.lock
fi

git add Cargo.toml
git commit -m "$(cat <<EOF
Release ${tag}.

Bump minor version from ${current} to ${new_version}.
EOF
)"

git tag -a "$tag" -m "Release ${tag}"

echo "Pushing main and tag ${tag}…"
git push origin HEAD
git push origin "$tag"

arch="$(uname -m)"
os="$(uname -s | tr '[:upper:]' '[:lower:]')"
asset="mac-k3d-${os}-${arch}"
cp "target/release/mac-k3d" "$asset"

echo "Publishing GitHub release ${tag} as latest…"
gh release create "$tag" "$asset" \
  --title "$tag" \
  --notes "mac-k3d ${new_version} for ${os}/${arch}." \
  --latest \
  --target "$(git rev-parse HEAD)"

rm -f "$asset"

echo "Published ${tag}: https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/${tag}"
