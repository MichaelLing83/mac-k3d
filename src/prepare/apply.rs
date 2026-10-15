//! Runs after the wizard's YAML is saved: storage directories, the installs the
//! wizard marked, the pinned Harbor, and host settings. Whatever needs root and
//! cannot be done here ends in one block for an administrator.

use std::path::{Path, PathBuf};

use crate::config::{DependencyEntry, DependencySource, MacK3dConfig, NodeRole};
use crate::error::{Error, Result};
use crate::platform::{self, RootStep};
use crate::prepare::{discovery, docker_plugins, install, resources, root_steps, toolchain};

/// Install order. Harbor last: it only needs uv, never root.
const INSTALL_ORDER: [&str; 7] = ["docker", "git", "java", "k3d", "kubectl", "helm", "harbor"];

/// Bring this host in line with `config`. Updates the dependency entries in
/// place, so the caller saves the config again afterwards.
pub fn apply(config: &mut MacK3dConfig, config_path: &Path) -> Result<()> {
    ensure_storage_dirs(config)?;
    let worker = matches!(config.role, NodeRole::Worker);
    if worker {
        align_java(&mut config.dependencies.java);
    }
    let mut sudo = Sudo::default();
    let mut for_root = install_pending(config, &mut sudo)?;

    if worker {
        let host = platform::worker_host_root_steps();
        if !host.is_empty() {
            if sudo.allowed() {
                root_steps::run(&host)?;
                if host.iter().any(|s| s.command.contains("usermod")) {
                    println!("Log out and back in so the docker group applies to new shells.");
                }
            } else {
                for_root.extend(host);
            }
        }
    }
    if !for_root.is_empty() {
        return Err(Error::NeedsRoot(root_steps::render(&for_root, config_path)));
    }

    if worker {
        #[cfg(target_os = "macos")]
        toolchain::ensure_brew_shell_tools()?;
        if let Some(docker) = &config.dependencies.docker.binary {
            docker_plugins::ensure_all(docker);
        }
        // Warn, do not fail: an older bash or python3 is the env phase's hard
        // check, and setup must still finish so the agent comes up.
        for problem in toolchain::host_tool_problems() {
            println!("Warning: {problem}; every build's env phase refuses it.");
        }
    }
    if matches!(config.role, NodeRole::Controller) {
        controller_notes(config);
    }
    for path in disk_check_paths(config) {
        resources::ensure_disk_min(&path, config.disk_min_gb())?;
    }
    resources::ensure_ram_min(toolchain::min_ram_gb())?;
    Ok(())
}

/// Where free disk is checked: the storage base, and on a worker also the
/// agent root, where builds clone and the env phase checks (often another disk).
pub fn disk_check_paths(config: &MacK3dConfig) -> Vec<PathBuf> {
    let mut paths = vec![config
        .storage
        .base_dir
        .clone()
        .unwrap_or_else(|| PathBuf::from("/"))];
    if matches!(config.role, NodeRole::Worker) {
        if let Some(remote_fs) = &config.jenkins_agent.remote_fs {
            if !paths.contains(remote_fs) {
                paths.push(remote_fs.clone());
            }
        }
    }
    paths
}

/// What to do with a worker's recorded Java, given the controller's major.
#[derive(Debug, PartialEq, Eq)]
pub enum JavaAction {
    Keep,
    Switch(PathBuf, u32),
    Install,
}

/// `recorded`: the major of the Java in the config (None when it is missing or
/// does not run). `best`: what discovery finds on this host now.
pub fn java_action(recorded: Option<u32>, best: Option<(PathBuf, u32)>, want: u32) -> JavaAction {
    if recorded.is_some_and(|m| m >= want) {
        return JavaAction::Keep;
    }
    match best {
        Some((bin, major)) if major >= want => JavaAction::Switch(bin, major),
        _ => JavaAction::Install,
    }
}

/// A config written before the Java pin may record a Java the controller
/// refuses. Switch to a newer one on this host, or mark Java for install.
fn align_java(entry: &mut DependencyEntry) {
    if entry.source != DependencySource::Existing {
        return;
    }
    let want = toolchain::java_major();
    let recorded = entry.binary.as_deref().and_then(toolchain::java_major_of);
    let was = match (&entry.binary, recorded) {
        (Some(bin), Some(m)) => format!("java {m} at {}", bin.display()),
        (Some(bin), None) => format!("java at {} does not run", bin.display()),
        (None, _) => "no java recorded".into(),
    };
    match java_action(recorded, discovery::best_java(), want) {
        JavaAction::Keep => {}
        JavaAction::Switch(bin, major) => {
            println!(
                "{was}; Jenkins agents need Java {want}. Using Java {major} at {}",
                bin.display()
            );
            *entry = DependencyEntry {
                source: DependencySource::Existing,
                binary: Some(bin),
                app: None,
            };
        }
        JavaAction::Install => {
            println!("{was}; Jenkins agents need Java {want}. Installing it.");
            *entry = DependencyEntry {
                source: DependencySource::Install,
                binary: None,
                app: None,
            };
        }
    }
}

/// Asks for sudo at most once per run, and only when something needs it.
#[derive(Default)]
struct Sudo(Option<bool>);

impl Sudo {
    fn allowed(&mut self) -> bool {
        *self.0.get_or_insert_with(platform::can_elevate)
    }
}

/// Install what the wizard marked. A tool that appeared since the wizard ran
/// (root installed it) is recorded instead. Returns the root steps left over.
fn install_pending(config: &mut MacK3dConfig, sudo: &mut Sudo) -> Result<Vec<RootStep>> {
    let mut for_root = Vec::new();
    for name in INSTALL_ORDER {
        let Some(entry) = config.dependencies.entry_mut(name) else {
            continue;
        };
        if entry.source != DependencySource::Install {
            continue;
        }
        if name != "harbor" {
            if let Some(tool) = install::discover_usable(name) {
                println!("{name}: found {}; nothing to install", tool.binary.display());
                *entry = install::tool_to_entry(&tool);
                continue;
            }
            if platform::root_install_command(name).is_some() && !sudo.allowed() {
                for_root.extend(root_steps::for_packages(&[name]));
                continue;
            }
        }
        *entry = install::install_and_discover(name)?;
    }
    Ok(for_root)
}

fn ensure_storage_dirs(config: &MacK3dConfig) -> Result<()> {
    let storage = &config.storage;
    let dirs = [
        storage.base_dir.clone(),
        storage.docker_dir(),
        storage.k3d_dir(),
        storage.jenkins_dir(),
        storage.downloads_dir(),
    ];
    for dir in dirs.into_iter().flatten() {
        let path = dir.display().to_string();
        std::fs::create_dir_all(&dir)
            .map_err(|e| Error::Config(format!("failed to create {path}: {e}")))?;
        tracing::info!(%path, "created storage directory");
    }
    Ok(())
}

fn controller_notes(config: &MacK3dConfig) {
    println!(
        "\nController: after `mac-k3d start`, ensure Lockable Resources label '{}' exists.",
        config.resources.cpu_cores_label
    );
    let _ = resources::ensure_cpu_cores_label_on_controller(
        &format!("http://localhost:{}", config.jenkins.host_port),
        &config.resources.cpu_cores_label,
    );
    if let Some(docker_dir) = config.storage.docker_dir() {
        println!(
            "Recommended Docker data path: {} (configure {} data-root if desired).",
            docker_dir.display(),
            platform::docker_display_name()
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn apply_switches_to_a_newer_java_when_present() {
        let jdk21 = PathBuf::from("/usr/lib/jvm/java-21-openjdk-amd64/bin/java");
        let jdk17 = PathBuf::from("/usr/bin/java");
        assert_eq!(
            java_action(Some(17), Some((jdk21.clone(), 21)), 21),
            JavaAction::Switch(jdk21.clone(), 21)
        );
        assert_eq!(java_action(None, Some((jdk21.clone(), 21)), 21), JavaAction::Switch(jdk21, 21));
        assert_eq!(java_action(Some(17), Some((jdk17.clone(), 17)), 21), JavaAction::Install);
        assert_eq!(java_action(None, None, 21), JavaAction::Install);
        assert_eq!(java_action(Some(21), Some((jdk17.clone(), 17)), 21), JavaAction::Keep);
        assert_eq!(java_action(Some(25), None, 21), JavaAction::Keep);
    }

    #[test]
    fn worker_disk_is_checked_where_builds_run() {
        let mut cfg = MacK3dConfig::default();
        cfg.role = NodeRole::Worker;
        cfg.storage.base_dir = Some(PathBuf::from("/data/mac-k3d"));
        cfg.jenkins_agent.remote_fs = Some(PathBuf::from("/home/toby/jenkins-agent"));
        assert_eq!(
            disk_check_paths(&cfg),
            vec![PathBuf::from("/data/mac-k3d"), PathBuf::from("/home/toby/jenkins-agent")]
        );
        cfg.role = NodeRole::Controller;
        assert_eq!(disk_check_paths(&cfg), vec![PathBuf::from("/data/mac-k3d")]);
    }
}
