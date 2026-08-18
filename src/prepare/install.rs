use std::path::PathBuf;
use std::process::Command;

use crate::config::{DependencyEntry, DependencySource};
use crate::error::{Error, Result};
use crate::prepare::discovery::{self, DiscoveredTool};

/// Install a dependency via Homebrew and return the discovered entry afterward.
pub fn install_and_discover(name: &str) -> Result<DependencyEntry> {
    if !brew_available() {
        return Err(Error::DependencyMissing(format!(
            "Homebrew not found; install {name} manually"
        )));
    }

    let brew_args: &[&str] = match name {
        "docker" => &["install", "--cask", "docker"],
        "k3d" => &["install", "k3d"],
        "kubectl" => &["install", "kubectl"],
        "helm" => &["install", "helm"],
        other => {
            return Err(Error::Config(format!("unknown dependency for install: {other}")));
        }
    };

    tracing::info!(dependency = name, "installing via Homebrew");
    let status = Command::new("brew")
        .args(brew_args)
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: format!("brew {}", brew_args.join(" ")),
            source: e.into(),
        })?;

    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("brew {}", brew_args.join(" ")),
            source: anyhow::anyhow!("exit code {:?}", status.code()),
        });
    }

    let discovered = discover_single(name);
    match discovered {
        Some(tool) => Ok(tool_to_entry(&tool)),
        None => Err(Error::DependencyMissing(format!(
            "{name} installed but binary not found on PATH"
        ))),
    }
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
        // Informational only; may be false in minimal environments.
        let _ = brew_available();
    }
}
