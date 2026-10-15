#!/usr/bin/env bash
# env/compose: `docker compose` (v2 plugin) and `docker buildx`. An existing
# plugin is kept; a missing one is downloaded user-level at the version pinned
# in pipeline/config/toolchain.env (`mac-k3d setup` does the same on a worker,
# src/prepare/docker_plugins.rs). Harbor runs every trial through compose;
# LoLBench's egress sidecar needs buildx.
set -euo pipefail
# shellcheck source=../_common.sh
source "$(cd "$(dirname "$0")/.." && pwd)/_common.sh"

_cli_plugin_arch() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64 | Darwin-aarch64) echo "darwin-arm64" ;;
    Darwin-x86_64) echo "darwin-amd64" ;;
    Linux-aarch64 | Linux-arm64) echo "linux-arm64" ;;
    *) echo "linux-amd64" ;;
  esac
}

ensure_docker_compose() {
  if docker compose version >/dev/null 2>&1; then
    echo "OK docker compose $(docker compose version 2>/dev/null | head -n 1)"
    return 0
  fi

  echo "Installing Docker Compose v2 plugin under ~/.docker/cli-plugins (no sudo)…"
  mkdir -p "${HOME}/.docker/cli-plugins"
  local ver="${DOCKER_COMPOSE_VERSION:?pin in pipeline/config/toolchain.env}" url asset
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64 | Darwin-aarch64) asset="docker-compose-darwin-aarch64" ;;
    Darwin-x86_64) asset="docker-compose-darwin-x86_64" ;;
    Linux-aarch64 | Linux-arm64) asset="docker-compose-linux-aarch64" ;;
    *) asset="docker-compose-linux-x86_64" ;;
  esac
  url="https://github.com/docker/compose/releases/download/${ver}/${asset}"
  if ! curl -fsSL "$url" -o "${HOME}/.docker/cli-plugins/docker-compose"; then
    die "failed to download Docker Compose from $url"
  fi
  chmod +x "${HOME}/.docker/cli-plugins/docker-compose"
  docker compose version >/dev/null 2>&1 \
    || die "docker compose still missing after plugin install. Install Docker Desktop (macOS) or docker-compose-v2 (Linux apt)."
  echo "OK docker compose $(docker compose version 2>/dev/null | head -n 1)"
}

ensure_docker_buildx() {
  if docker buildx version >/dev/null 2>&1; then
    echo "OK docker buildx $(docker buildx version 2>/dev/null | head -n 1)"
    return 0
  fi

  echo "Installing Docker buildx plugin under ~/.docker/cli-plugins (Harbor allowlist sidecar needs it)…"
  mkdir -p "${HOME}/.docker/cli-plugins"
  local ver="${DOCKER_BUILDX_VERSION:?pin in pipeline/config/toolchain.env}" arch url
  arch="$(_cli_plugin_arch)"
  url="https://github.com/docker/buildx/releases/download/${ver}/buildx-${ver}.${arch}"
  if ! curl -fsSL "$url" -o "${HOME}/.docker/cli-plugins/docker-buildx"; then
    die "failed to download Docker buildx from $url"
  fi
  chmod +x "${HOME}/.docker/cli-plugins/docker-buildx"
  docker buildx version >/dev/null 2>&1 \
    || die "docker buildx still missing after plugin install. Harbor LoLBench tasks need buildx to build the egress sidecar."
  echo "OK docker buildx $(docker buildx version 2>/dev/null | head -n 1)"
}

have docker || die "docker not on PATH"
ensure_docker_compose
ensure_docker_buildx
