//! The `docker compose` and `docker buildx` CLI plugins a worker's builds need
//! (Harbor runs every trial through compose; LoLBench's egress sidecar needs
//! buildx). Same no-root install as `pipeline/stages/env/compose.sh`: an
//! existing plugin is kept, a missing one is downloaded at the toolchain.env
//! pin into `~/.docker/cli-plugins`.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::error::{Error, Result};
use crate::prepare::toolchain;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plugin {
    Compose,
    Buildx,
}

pub const PLUGINS: [Plugin; 2] = [Plugin::Compose, Plugin::Buildx];

impl Plugin {
    pub fn name(self) -> &'static str {
        match self {
            Plugin::Compose => "compose",
            Plugin::Buildx => "buildx",
        }
    }

    fn file_name(self) -> &'static str {
        match self {
            Plugin::Compose => "docker-compose",
            Plugin::Buildx => "docker-buildx",
        }
    }

    pub fn pinned_version(self) -> &'static str {
        match self {
            Plugin::Compose => toolchain::docker_compose_version(),
            Plugin::Buildx => toolchain::docker_buildx_version(),
        }
    }
}

/// Release asset for `plugin` on `os` (`linux`/`macos`) and `arch` (`x86_64`/`aarch64`),
/// the same names env/compose.sh downloads.
pub fn asset_url(plugin: Plugin, version: &str, os: &str, arch: &str) -> String {
    let os = if os == "macos" { "darwin" } else { "linux" };
    let arm = matches!(arch, "aarch64" | "arm64");
    match plugin {
        Plugin::Compose => {
            let arch = if arm { "aarch64" } else { "x86_64" };
            format!("https://github.com/docker/compose/releases/download/{version}/docker-compose-{os}-{arch}")
        }
        Plugin::Buildx => {
            let arch = if arm { "arm64" } else { "amd64" };
            format!("https://github.com/docker/buildx/releases/download/{version}/buildx-{version}.{os}-{arch}")
        }
    }
}

/// First line of `docker <plugin> version`, or None when the plugin is missing.
pub fn installed_version(docker: &Path, plugin: Plugin) -> Option<String> {
    let out = Command::new(docker)
        .args([plugin.name(), "version"])
        .output()
        .ok()
        .filter(|o| o.status.success())?;
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .next()
        .map(|l| l.trim().to_string())
        .filter(|l| !l.is_empty())
}

fn plugin_dir() -> Result<PathBuf> {
    std::env::var_os("HOME")
        .map(|h| PathBuf::from(h).join(".docker/cli-plugins"))
        .ok_or_else(|| Error::Config("HOME is not set".into()))
}

fn download(plugin: Plugin) -> Result<()> {
    let dir = plugin_dir()?;
    std::fs::create_dir_all(&dir)
        .map_err(|e| Error::Config(format!("failed to create {}: {e}", dir.display())))?;
    let url = asset_url(
        plugin,
        plugin.pinned_version(),
        std::env::consts::OS,
        std::env::consts::ARCH,
    );
    let dest = dir.join(plugin.file_name());
    let part = dest.with_extension("part");
    println!("docker {}: downloading {url}", plugin.name());
    let ok = Command::new("curl")
        .args(["-fsSL", "-o", &part.display().to_string(), &url])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    if !ok {
        let _ = std::fs::remove_file(&part);
        return Err(Error::DependencyMissing(format!(
            "failed to download docker {} from {url}",
            plugin.name()
        )));
    }
    use std::os::unix::fs::PermissionsExt;
    std::fs::set_permissions(&part, std::fs::Permissions::from_mode(0o755))
        .and_then(|_| std::fs::rename(&part, &dest))
        .map_err(|e| Error::Config(format!("failed to install {}: {e}", dest.display())))
}

/// Install each missing plugin. A failure is a warning: the env phase tries
/// again at the start of every build.
pub fn ensure_all(docker: &Path) {
    for plugin in PLUGINS {
        if let Some(v) = installed_version(docker, plugin) {
            println!("docker {}: {v}", plugin.name());
            continue;
        }
        let result = download(plugin).and_then(|_| {
            installed_version(docker, plugin).ok_or_else(|| {
                Error::DependencyMissing(format!(
                    "docker {} still missing after the download",
                    plugin.name()
                ))
            })
        });
        match result {
            Ok(v) => println!("docker {}: installed {v}", plugin.name()),
            Err(e) => tracing::warn!(
                "{e}; the env phase retries at the start of each build ({})",
                missing_hint(plugin)
            ),
        }
    }
}

/// How to get `plugin` when the download is not possible.
pub fn missing_hint(plugin: Plugin) -> &'static str {
    match (plugin, cfg!(target_os = "macos")) {
        (_, true) => "Docker Desktop ships both plugins",
        (Plugin::Compose, false) => "apt-get install -y docker-compose-v2",
        (Plugin::Buildx, false) => "apt-get install -y docker-buildx",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn asset_urls_match_env_compose_sh() {
        let script = std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("pipeline/stages/env/compose.sh"),
        )
        .unwrap();
        let cases = [
            (Plugin::Compose, "linux", "x86_64", "docker-compose-linux-x86_64"),
            (Plugin::Compose, "linux", "aarch64", "docker-compose-linux-aarch64"),
            (Plugin::Compose, "macos", "aarch64", "docker-compose-darwin-aarch64"),
            (Plugin::Compose, "macos", "x86_64", "docker-compose-darwin-x86_64"),
            (Plugin::Buildx, "linux", "x86_64", "linux-amd64"),
            (Plugin::Buildx, "linux", "aarch64", "linux-arm64"),
            (Plugin::Buildx, "macos", "aarch64", "darwin-arm64"),
            (Plugin::Buildx, "macos", "x86_64", "darwin-amd64"),
        ];
        for (plugin, os, arch, asset) in cases {
            let url = asset_url(plugin, plugin.pinned_version(), os, arch);
            assert!(url.ends_with(asset), "{url}");
            assert!(url.contains(plugin.pinned_version()), "{url}");
            assert!(script.contains(asset), "env/compose.sh has no {asset}");
        }
        assert!(script.contains("https://github.com/docker/compose/releases/download/${ver}/${asset}"));
        assert!(script.contains("https://github.com/docker/buildx/releases/download/${ver}/buildx-${ver}.${arch}"));
    }

    #[test]
    fn pins_come_from_toolchain_env() {
        assert!(Plugin::Compose.pinned_version().starts_with('v'));
        assert!(Plugin::Buildx.pinned_version().starts_with('v'));
    }
}
