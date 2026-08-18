#!/usr/bin/env bash
# Bump the Cargo patch version (X.Y.Z -> X.Y.(Z+1)), or use a version
# string supplied as an argument or interactively. Build the release
# binary first so a failed compile never produces a commit or tag.
# Then commit, tag, push, and publish a GitHub latest release.
#
# Usage:
#   ./release.sh              # auto bump patch (prompts if stdin is a TTY)
#   ./release.sh 0.2.0        # explicit version
#   ./release.sh v0.2.0       # leading v is optional
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

usage() {
  cat <<'EOF'
Usage: ./release.sh [VERSION]

With no VERSION, bump the patch number (0.1.0 -> 0.1.1).
If stdin is a TTY, you can confirm or type a different version.

VERSION is semver X.Y.Z; a leading v is optional.

Build order:
  1. cargo build --release on the current tree (must succeed)
  2. bump version in Cargo.toml
  3. cargo build --release again (binary --version matches the tag)
  4. commit, tag, push, publish

A failed build never creates a commit or tag. If the post-bump rebuild
fails, Cargo.toml / Cargo.lock are restored.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

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
patch="${BASH_REMATCH[3]}"
patch_bump="${major}.${minor}.$((patch + 1))"

normalize_version() {
  local raw="${1#v}"
  raw="${raw#V}"
  if [[ ! "$raw" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    echo "error: version must be X.Y.Z (got: $1)" >&2
    return 1
  fi
  printf '%s\n' "$raw"
}

if [[ $# -ge 1 ]]; then
  new_version="$(normalize_version "$1")"
else
  new_version="$patch_bump"
  if [[ -t 0 ]]; then
    read -r -p "Release version [${patch_bump}]: " entered
    if [[ -n "${entered:-}" ]]; then
      new_version="$(normalize_version "$entered")"
    fi
  fi
fi

if [[ "$new_version" == "$current" ]]; then
  echo "error: version ${new_version} is already the current package version" >&2
  exit 1
fi

tag="v${new_version}"

if git rev-parse "$tag" >/dev/null 2>&1; then
  echo "error: tag $tag already exists" >&2
  exit 1
fi

echo "Preflight: cargo build --release (current tree ${current})"
cargo build --release

echo "Bumping version ${current} -> ${new_version}"

restore_version_files() {
  git checkout -- Cargo.toml Cargo.lock
}

trap restore_version_files ERR

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

echo "Building release binary for ${new_version}…"
cargo build --release

trap - ERR

git add Cargo.toml
if [[ -n "$(git status --porcelain -- Cargo.lock)" ]]; then
  git add Cargo.lock
fi

git commit -m "$(cat <<EOF
Release ${tag}.

Bump version from ${current} to ${new_version}.
EOF
)"

git tag -a "$tag" -m "Release ${tag}"

echo "Pushing main and tag ${tag}…"
git_push() {
  git -c credential.helper= -c credential.helper='!gh auth git-credential' push "$@"
}
git_push origin HEAD
git_push origin "$tag"

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
