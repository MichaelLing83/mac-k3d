use std::path::PathBuf;

use crate::config::{DependencyEntry, DependencySource};
use crate::error::{Error, Result};
use crate::platform;
use crate::prepare::discovery::{self, DiscoveredTool};
use crate::prepare::toolchain;

/// Install a dependency and return the discovered entry afterward.
pub fn install_and_discover(name: &str) -> Result<DependencyEntry> {
    match name {
        "harbor" => {
            let binary = toolchain::ensure_harbor()?;
            Ok(DependencyEntry {
                source: DependencySource::Existing,
                binary: Some(binary),
                app: None,
            })
        }
        "java" | "docker" | "k3d" | "kubectl" | "helm" | "git" | "uv" => {
            platform::install_package(name)?;
            discover_usable(name)
                .map(|t| tool_to_entry(&t))
                .ok_or_else(|| {
                    if name == "java" {
                        Error::DependencyMissing(format!(
                            "java installed but no Java {} found (JAVA_HOME, PATH, /usr/lib/jvm)",
                            toolchain::java_major()
                        ))
                    } else {
                        Error::DependencyMissing(format!(
                            "{name} installed but binary not found on PATH"
                        ))
                    }
                })
        }
        other => Err(Error::Config(format!(
            "unknown dependency for install: {other}"
        ))),
    }
}

pub fn brew_available() -> bool {
    #[cfg(target_os = "macos")]
    {
        discovery::which("brew").is_some()
    }
    #[cfg(not(target_os = "macos"))]
    {
        false
    }
}

/// What is on this host now for `name`, if anything.
pub fn discover_single(name: &str) -> Option<DiscoveredTool> {
    let deps = discovery::discover_all();
    match name {
        "docker" => deps.docker,
        "k3d" => deps.k3d,
        "kubectl" => deps.kubectl,
        "helm" => deps.helm,
        "harbor" => deps.harbor,
        "java" => deps.java,
        "git" => deps.git,
        _ => None,
    }
}

/// Like `discover_single`, but a Java below the controller's major does not count.
pub fn discover_usable(name: &str) -> Option<DiscoveredTool> {
    let tool = discover_single(name)?;
    if name == "java"
        && toolchain::java_major_of(&tool.binary).map_or(true, |m| m < toolchain::java_major())
    {
        return None;
    }
    Some(tool)
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
    fn package_manager_probe() {
        let _ = platform::package_install_available();
        let _ = brew_available();
    }
}
