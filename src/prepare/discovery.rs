use std::path::PathBuf;
use std::process::Command;

use crate::config::{DependenciesConfig, DependencyEntry, DependencySource};
use crate::platform;
use crate::prepare::toolchain;

/// Result of scanning the system for installed tools.
#[derive(Debug, Default)]
pub struct DiscoveredDeps {
    pub docker: Option<DiscoveredTool>,
    pub k3d: Option<DiscoveredTool>,
    pub kubectl: Option<DiscoveredTool>,
    pub helm: Option<DiscoveredTool>,
    pub harbor: Option<DiscoveredTool>,
    pub java: Option<DiscoveredTool>,
    pub git: Option<DiscoveredTool>,
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
        git: discover_on_path("git"),
        uv: discover_on_path("uv"),
        pipx: discover_on_path("pipx"),
    }
}

fn discover_docker() -> Option<DiscoveredTool> {
    let app = platform::docker_app_default().filter(|p| p.exists());

    let binary = which("docker").or_else(|| {
        [
            "/usr/local/bin/docker",
            "/opt/homebrew/bin/docker",
            "/usr/bin/docker",
        ]
        .map(PathBuf::from)
        .into_iter()
        .find(|p| p.exists())
    });

    match (app.as_ref(), binary) {
        (None, None) => None,
        (_, Some(binary)) => Some(DiscoveredTool {
            binary,
            app,
            version_hint: None,
        }),
        (Some(app), None) if platform::requires_docker_app() => Some(DiscoveredTool {
            binary: PathBuf::from("/usr/local/bin/docker"),
            app: Some(app.clone()),
            version_hint: None,
        }),
        (Some(_), None) => None,
    }
}

/// Java for the Jenkins agent: the first candidate at or above the controller's
/// major (JAVA_MAJOR), else the newest that runs so validation can name it.
/// A candidate whose `-version` fails (the macOS stub without a JDK) is skipped.
fn discover_java() -> Option<DiscoveredTool> {
    let (binary, major) = best_java()?;
    Some(DiscoveredTool {
        binary,
        app: None,
        version_hint: Some(format!("Java {major}")),
    })
}

/// The Java `discover_java` picks, with its major version.
pub fn best_java() -> Option<(PathBuf, u32)> {
    let want = toolchain::java_major();
    let found = java_candidates(want)
        .into_iter()
        .filter_map(|bin| toolchain::java_major_of(&bin).map(|major| (bin, major)));
    pick_java(found, want)
}

/// First `(binary, major)` meeting `want`, else the highest major seen.
pub fn pick_java(
    found: impl IntoIterator<Item = (PathBuf, u32)>,
    want: u32,
) -> Option<(PathBuf, u32)> {
    let mut best: Option<(PathBuf, u32)> = None;
    for (bin, major) in found {
        if major >= want {
            return Some((bin, major));
        }
        if best.as_ref().map_or(true, |(_, m)| major > *m) {
            best = Some((bin, major));
        }
    }
    best
}

/// Where a Java may live, in preference order: macOS java_home for the pinned
/// major, JAVA_HOME, PATH, then every JVM under /usr/lib/jvm (newest name first).
fn java_candidates(want: u32) -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    if cfg!(target_os = "macos") {
        for args in [vec!["-v".to_string(), format!("{want}+")], Vec::new()] {
            let Ok(o) = Command::new("/usr/libexec/java_home").args(&args).output() else {
                continue;
            };
            let home = String::from_utf8_lossy(&o.stdout).trim().to_string();
            if o.status.success() && !home.is_empty() {
                out.push(PathBuf::from(home).join("bin/java"));
            }
        }
    }
    if let Ok(home) = std::env::var("JAVA_HOME") {
        out.push(PathBuf::from(home).join("bin/java"));
    }
    out.extend(which("java"));
    if let Ok(entries) = std::fs::read_dir("/usr/lib/jvm") {
        let mut jvms: Vec<PathBuf> = entries
            .flatten()
            .map(|e| e.path().join("bin/java"))
            .filter(|p| p.is_file())
            .collect();
        jvms.sort();
        jvms.reverse();
        out.extend(jvms);
    }
    let mut seen = Vec::new();
    out.retain(|p| {
        let key = std::fs::canonicalize(p).unwrap_or_else(|_| p.clone());
        let fresh = !seen.contains(&key);
        seen.push(key);
        fresh
    });
    out
}

fn discover_on_path(name: &str) -> Option<DiscoveredTool> {
    let binary = which(name).or_else(|| {
        if name == "harbor" {
            crate::prepare::path_env::user_local_bin()
                .map(|d| d.join("harbor"))
                .filter(|p| p.exists())
        } else {
            None
        }
    })?;
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
        "docker" | "java" | "harbor" | "git" => "--version",
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
        git: default_entry(&deps.git),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn which_finds_common_tools_or_none() {
        let _ = which("k3d");
    }

    #[test]
    fn pick_java_prefers_the_first_at_or_above_the_pin() {
        let j = |p: &str, m: u32| (PathBuf::from(p), m);
        let found = [j("/usr/bin/java", 17), j("/usr/lib/jvm/java-21/bin/java", 21), j("/x/25", 25)];
        assert_eq!(pick_java(found.clone(), 21), Some(j("/usr/lib/jvm/java-21/bin/java", 21)));
        assert_eq!(pick_java(found, 17), Some(j("/usr/bin/java", 17)));
        let old = [j("/a/11", 11), j("/b/17", 17), j("/c/8", 8)];
        assert_eq!(pick_java(old, 21), Some(j("/b/17", 17)));
        assert_eq!(pick_java(Vec::new(), 21), None);
    }
}
