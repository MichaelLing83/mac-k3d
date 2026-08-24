use std::path::PathBuf;
use std::process::Command;

use crate::config::{DependencyEntry, DependencySource};
use crate::error::{Error, Result};
use crate::prepare::discovery::{self, DiscoveredTool};

/// Install a dependency and return the discovered entry afterward.
pub fn install_and_discover(name: &str) -> Result<DependencyEntry> {
    match name {
        "harbor" => install_harbor(),
        "java" => install_via_brew(&["install", "--cask", "temurin"], "java"),
        "docker" => install_via_brew(&["install", "--cask", "docker"], "docker"),
        "k3d" => install_via_brew(&["install", "k3d"], "k3d"),
        "kubectl" => install_via_brew(&["install", "kubectl"], "kubectl"),
        "helm" => install_via_brew(&["install", "helm"], "helm"),
        other => Err(Error::Config(format!(
            "unknown dependency for install: {other}"
        ))),
    }
}

fn install_harbor() -> Result<DependencyEntry> {
    if discovery::which("uv").is_some() {
        tracing::info!("installing harbor via uv tool install");
        run_cmd("uv", &["tool", "install", "harbor"])?;
    } else if discovery::which("pipx").is_some() {
        tracing::info!("installing harbor via pipx");
        run_cmd("pipx", &["install", "harbor"])?;
    } else if brew_available() {
        tracing::info!("installing uv via Homebrew, then harbor");
        run_cmd("brew", &["install", "uv"])?;
        run_cmd("uv", &["tool", "install", "harbor"])?;
    } else {
        return Err(Error::DependencyMissing(
            "harbor: need uv, pipx, or Homebrew to install".into(),
        ));
    }

    discovery::discover_all()
        .harbor
        .map(|t| tool_to_entry(&t))
        .ok_or_else(|| {
            Error::DependencyMissing("harbor installed but binary not found on PATH".into())
        })
}

fn install_via_brew(brew_args: &[&str], name: &str) -> Result<DependencyEntry> {
    if !brew_available() {
        return Err(Error::DependencyMissing(format!(
            "Homebrew not found; install {name} manually"
        )));
    }
    tracing::info!(dependency = name, "installing via Homebrew");
    run_cmd("brew", brew_args)?;
    discover_single(name)
        .map(|t| tool_to_entry(&t))
        .ok_or_else(|| {
            Error::DependencyMissing(format!("{name} installed but binary not found on PATH"))
        })
}

fn run_cmd(program: &str, args: &[&str]) -> Result<()> {
    let status = Command::new(program).args(args).status().map_err(|e| {
        Error::CommandFailed {
            cmd: format!("{program} {}", args.join(" ")),
            source: e.into(),
        }
    })?;
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("{program} {}", args.join(" ")),
            source: anyhow::anyhow!("exit code {:?}", status.code()),
        });
    }
    Ok(())
}

pub fn brew_available() -> bool {
    discovery::which("brew").is_some()
}

fn discover_single(name: &str) -> Option<DiscoveredTool> {
    let deps = discovery::discover_all();
    match name {
        "docker" => deps.docker,
        "k3d" => deps.k3d,
        "kubectl" => deps.kubectl,
        "helm" => deps.helm,
        "harbor" => deps.harbor,
        "java" => deps.java,
        _ => None,
    }
}

pub fn tool_to_entry(tool: &DiscoveredTool) -> DependencyEntry {
    DependencyEntry {
        source: DependencySource::Existing,
        binary: Some(tool.binary.clone()),
        app: tool.app.clone(),
    }
}

pub fn entry_from_path(path: PathBuf, app: Option<PathBuf>) -> Result<DependencyEntry> {
    if !path.exists() {
        return Err(Error::Validation(format!(
            "binary not found: {}",
            path.display()
        )));
    }
    Ok(DependencyEntry {
        source: DependencySource::Existing,
        binary: Some(path),
        app,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn brew_available_on_macos_ci() {
        let _ = brew_available();
    }
}
