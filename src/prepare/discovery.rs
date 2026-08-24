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
    pub harbor: Option<DiscoveredTool>,
    pub java: Option<DiscoveredTool>,
    pub uv: Option<DiscoveredTool>,
    pub pipx: Option<DiscoveredTool>,
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
        harbor: discover_on_path("harbor"),
        java: discover_java(),
        uv: discover_on_path("uv"),
        pipx: discover_on_path("pipx"),
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

fn discover_java() -> Option<DiscoveredTool> {
    if let Some(binary) = which("java") {
        return Some(DiscoveredTool {
            binary,
            app: None,
            version_hint: version_of("java", &which("java").unwrap_or_default()),
        });
    }
    let output = Command::new("/usr/libexec/java_home").output().ok()?;
    if !output.status.success() {
        return None;
    }
    let home = String::from_utf8_lossy(&output.stdout).trim().to_string();
    let binary = PathBuf::from(&home).join("bin/java");
    binary.exists().then_some(DiscoveredTool {
        binary,
        app: None,
        version_hint: Some(home),
    })
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
    if binary.as_os_str().is_empty() {
        return None;
    }
    let flag = match name {
        "docker" | "java" => "--version",
        "harbor" => "--version",
        _ => "version",
    };
    let output = Command::new(binary).arg(flag).output().ok()?;
    if output.status.success() {
        let text = String::from_utf8_lossy(&output.stdout);
        let err = String::from_utf8_lossy(&output.stderr);
        let line = text.lines().next().or_else(|| err.lines().next())?;
        Some(line.trim().to_string())
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

/// Search common locations for a LoLBench-Preview checkout.
pub fn find_lolbench_checkouts() -> Vec<PathBuf> {
    let mut found = Vec::new();
    let mut candidates = Vec::new();

    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        candidates.push(home.join("github/LoLBench-Preview"));
        candidates.push(home.join("src/LoLBench-Preview"));
        candidates.push(home.join("LoLBench-Preview"));
    }

    if let Ok(entries) = std::fs::read_dir("/Volumes") {
        for entry in entries.flatten() {
            let p = entry.path().join("github/LoLBench-Preview");
            candidates.push(p);
        }
    }

    for path in candidates {
        if path.join("harbor_tasks").is_dir() || path.join("scripts/run_task.sh").is_file() {
            if !found.contains(&path) {
                found.push(path);
            }
        }
    }
    found
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
        harbor: default_entry(&deps.harbor),
        java: default_entry(&deps.java),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn which_finds_common_tools_or_none() {
        let _ = which("k3d");
    }
}
