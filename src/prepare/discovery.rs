use std::path::PathBuf;
use std::process::Command;

use crate::config::{DependenciesConfig, DependencyEntry, DependencySource};

/// Result of scanning the system for installed tools.
#[derive(Debug, Default)]
pub struct DiscoveredDeps {
    pub docker: Option<DiscoveredTool>,
    pub k3d: Option<DiscoveredTool>,
    pub kubectl: Option<DiscoveredTool>,
    pub helm: Option<DiscoveredTool>,
}

#[derive(Debug, Clone)]
pub struct DiscoveredTool {
    pub binary: PathBuf,
    pub app: Option<PathBuf>,
    pub version_hint: Option<String>,
}

/// Scan PATH and common install locations. Never modifies the system.
pub fn discover_all() -> DiscoveredDeps {
    DiscoveredDeps {
        docker: discover_docker(),
        k3d: discover_on_path("k3d"),
        kubectl: discover_on_path("kubectl"),
        helm: discover_on_path("helm"),
    }
}

fn discover_docker() -> Option<DiscoveredTool> {
    let app = PathBuf::from("/Applications/Docker.app");
    let app_exists = app.exists();

    let binary = which("docker").or_else(|| {
        ["/usr/local/bin/docker", "/opt/homebrew/bin/docker"]
            .map(PathBuf::from)
            .into_iter()
            .find(|p| p.exists())
    });

    match (app_exists, binary) {
        (false, None) => None,
        (_, Some(binary)) => Some(DiscoveredTool {
            binary,
            app: app_exists.then_some(app),
            version_hint: None,
        }),
        (true, None) => Some(DiscoveredTool {
            binary: PathBuf::from("/usr/local/bin/docker"),
            app: Some(app),
            version_hint: None,
        }),
    }
}

fn discover_on_path(name: &str) -> Option<DiscoveredTool> {
    let binary = which(name)?;
    let version_hint = version_of(name, &binary);
    Some(DiscoveredTool {
        binary,
        app: None,
        version_hint,
    })
}

fn version_of(name: &str, binary: &PathBuf) -> Option<String> {
    let flag = if name == "docker" { "--version" } else { "version" };
    let output = Command::new(binary).arg(flag).output().ok()?;
    if output.status.success() {
        let text = String::from_utf8_lossy(&output.stdout);
        Some(text.lines().next()?.trim().to_string())
    } else {
        None
    }
}

pub fn which(name: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    std::env::split_paths(&path_var).find_map(|dir| {
        let candidate = dir.join(name);
        candidate.exists().then_some(candidate)
    })
}

impl DiscoveredTool {
    pub fn describe(&self) -> String {
        let mut parts = vec![format!("binary: {}", self.binary.display())];
        if let Some(app) = &self.app {
            parts.push(format!("app: {}", app.display()));
        }
        if let Some(v) = &self.version_hint {
            parts.push(format!("version: {v}"));
        }
        parts.join(", ")
    }
}

impl DiscoveredDeps {
    pub fn get(&self, name: &str) -> Option<&DiscoveredTool> {
        match name {
            "docker" => self.docker.as_ref(),
            "k3d" => self.k3d.as_ref(),
            "kubectl" => self.kubectl.as_ref(),
            "helm" => self.helm.as_ref(),
            _ => None,
        }
    }
}

pub fn default_entry(tool: &Option<DiscoveredTool>) -> DependencyEntry {
    match tool {
        Some(t) => DependencyEntry {
            source: DependencySource::Existing,
            binary: Some(t.binary.clone()),
            app: t.app.clone(),
        },
        None => DependencyEntry {
            source: DependencySource::Install,
            binary: None,
            app: None,
        },
    }
}

pub fn to_dependencies_config(deps: &DiscoveredDeps) -> DependenciesConfig {
    DependenciesConfig {
        docker: default_entry(&deps.docker),
        k3d: default_entry(&deps.k3d),
        kubectl: default_entry(&deps.kubectl),
        helm: default_entry(&deps.helm),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn which_finds_common_tools_or_none() {
        // Should not panic regardless of what's installed.
        let _ = which("k3d");
    }
}
