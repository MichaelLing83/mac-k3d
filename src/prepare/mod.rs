pub mod agent_service;
pub mod apply;
pub mod discovery;
pub mod docker_plugins;
pub mod eval_assets;
pub mod icode_paths;
pub mod install;
pub mod jenkins_agent;
pub mod jenkins_credentials;
pub mod jenkins_job;
pub mod path_env;
pub mod resources;
pub mod root_steps;
pub mod toolchain;
pub mod volumes;
pub mod wizard;

use std::path::Path;

use crate::config::{DependencySource, MacK3dConfig, NodeRole};
use crate::error::{Error, Result};

pub use apply::apply;
pub use wizard::{ExistingConfigAction, MacRole};

/// Prompt when config already exists.
pub fn prompt_existing_config() -> Result<ExistingConfigAction> {
    wizard::prompt_existing_config()
}

/// Run the interactive prepare wizard and return the generated config.
pub fn run_interactive() -> Result<MacK3dConfig> {
    let volumes = volumes::scan_volumes()?;
    let discovered = discovery::discover_all();
    wizard::run(volumes, discovered)
}

/// Validate config paths and dependencies without prompts.
pub fn validate(config: &MacK3dConfig) -> Result<()> {
    let problems = problems(config);
    if problems.is_empty() {
        tracing::info!("validation passed");
        Ok(())
    } else {
        for p in &problems {
            tracing::error!("{p}");
        }
        Err(Error::Validation(problems.join("; ")))
    }
}

fn problems(config: &MacK3dConfig) -> Vec<String> {
    let mut problems = Vec::new();

    if let Some(base) = &config.storage.base_dir {
        if base.exists() && !is_writable_dir(base) {
            problems.push(format!("storage base_dir not writable: {}", base.display()));
        }
    }

    let required = required_dependencies(config);
    for (name, entry) in config.dependencies.entries() {
        let is_required = required.contains(&name);
        match entry.source {
            DependencySource::Skip if is_required => {
                problems.push(format!("{name} is required but set to skip"));
            }
            DependencySource::Existing => {
                if let Some(bin) = &entry.binary {
                    if !bin.exists() {
                        problems.push(format!("{name} binary not found: {}", bin.display()));
                    }
                } else if is_required {
                    problems.push(format!("{name} has source=existing but no binary path"));
                }
                if name == "docker" && crate::platform::requires_docker_app() {
                    if let Some(app) = &entry.app {
                        if !app.exists() {
                            problems.push(format!("Docker.app not found: {}", app.display()));
                        }
                    }
                }
            }
            DependencySource::Install if is_required => {
                problems.push(format!(
                    "{name} is marked for install; run `mac-k3d setup` and choose \"Use existing config\""
                ));
            }
            _ => {}
        }
    }

    if !matches!(config.role, NodeRole::Worker)
        && (config.cluster.name.is_empty()
            || !config
                .cluster
                .name
                .chars()
                .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-'))
    {
        problems.push(format!(
            "invalid cluster name '{}': use lowercase letters, digits, and hyphens",
            config.cluster.name
        ));
    }

    if matches!(config.role, NodeRole::Worker) {
        if config
            .jenkins_agent
            .controller_url
            .as_ref()
            .map(|s| s.trim().is_empty())
            .unwrap_or(true)
        {
            problems.push("worker role requires jenkins_agent.controller_url".into());
        }
        if config.jenkins_agent.cpu_cores == 0 {
            problems.push("worker role expects jenkins_agent.cpu_cores > 0".into());
        }
        problems.extend(worker_java_problem(config));
    }

    for path in apply::disk_check_paths(config) {
        if let Err(e) = resources::ensure_disk_min(&path, config.disk_min_gb()) {
            problems.push(e.to_string());
        }
    }
    problems
}

/// The agent runs the recorded Java; the controller refuses one below JAVA_MAJOR.
fn worker_java_problem(config: &MacK3dConfig) -> Option<String> {
    let entry = &config.dependencies.java;
    if entry.source != DependencySource::Existing {
        return None;
    }
    let bin = entry.binary.as_deref().filter(|b| b.exists())?;
    toolchain::java_problem(bin, toolchain::java_major_of(bin), toolchain::java_major())
        .map(|p| format!("{p}; run `mac-k3d setup` and choose \"Use existing config\""))
}

/// What each role must have. A worker runs builds: Docker, the agent's Java,
/// git for the clones, and the pinned Harbor. It hosts no cluster.
fn required_dependencies(config: &MacK3dConfig) -> Vec<&'static str> {
    let mut deps = vec!["docker"];
    match config.role {
        NodeRole::Worker => deps.extend(["java", "git", "harbor"]),
        NodeRole::Controller => deps.extend(["k3d", "kubectl", "helm"]),
        NodeRole::Standalone => {
            deps.extend(["k3d", "kubectl"]);
            if config.dependencies.harbor.source != DependencySource::Skip {
                deps.extend(["harbor", "git"]);
            }
        }
    }
    if config.jenkins.enabled && !deps.contains(&"helm") {
        deps.push("helm");
    }
    deps
}

fn is_writable_dir(path: &Path) -> bool {
    let test = path.join(".mac-k3d-write-test");
    match std::fs::File::create(&test) {
        Ok(_) => {
            let _ = std::fs::remove_file(test);
            true
        }
        Err(_) => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{DependencyEntry, MacK3dConfig};
    use std::path::PathBuf;

    #[test]
    fn validate_default_config_flags_install() {
        let config = MacK3dConfig::default();
        let err = validate(&config).unwrap_err();
        assert!(err.to_string().contains("validation failed"));
    }

    fn found() -> DependencyEntry {
        DependencyEntry {
            source: DependencySource::Existing,
            binary: Some(PathBuf::from(env!("CARGO"))),
            app: None,
        }
    }

    fn worker() -> MacK3dConfig {
        let mut cfg = MacK3dConfig::default();
        cfg.role = NodeRole::Worker;
        cfg.resources.disk_min_gb = 1;
        cfg.jenkins_agent.controller_url = Some("http://192.0.2.1:17070".into());
        cfg.jenkins_agent.cpu_cores = 4;
        cfg.dependencies.docker = found();
        cfg.dependencies.java = found();
        cfg.dependencies.git = found();
        cfg.dependencies.harbor = found();
        cfg.dependencies.k3d.source = DependencySource::Skip;
        cfg.dependencies.kubectl.source = DependencySource::Skip;
        cfg
    }

    #[test]
    fn worker_needs_no_cluster_tools_or_lolbench_checkout() {
        let cfg = worker();
        assert!(cfg.lolbench.path.is_none());
        assert_eq!(problems(&cfg), Vec::<String>::new());
    }

    #[test]
    fn worker_without_java_git_or_harbor_fails() {
        for name in ["java", "git", "harbor"] {
            let mut cfg = worker();
            cfg.dependencies.entry_mut(name).unwrap().source = DependencySource::Skip;
            let found = problems(&cfg);
            assert!(
                found.iter().any(|p| p.starts_with(&format!("{name} is required"))),
                "{name}: {found:?}"
            );
        }
        let mut cfg = worker();
        cfg.dependencies.harbor = DependencyEntry::default();
        assert!(problems(&cfg).iter().any(|p| p.contains("harbor is marked for install")));
    }

    /// A stand-in `java` that prints `version` the way `java -version` does.
    /// Exec can hit ETXTBSY while another test thread forks with the write fd
    /// still open, so wait until the script actually runs.
    fn fake_java(dir: &Path, version: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        let bin = dir.join(format!("java-{version}"));
        std::fs::write(
            &bin,
            format!("#!/bin/sh\necho 'openjdk version \"{version}\" 2024-01-16' >&2\n"),
        )
        .unwrap();
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        for _ in 0..100 {
            if toolchain::java_major_of(&bin).is_some() {
                return bin;
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        panic!("fake java {} never ran", bin.display());
    }

    #[test]
    fn worker_with_old_java_fails_validation() {
        let dir = tempfile::tempdir().unwrap();
        let want = toolchain::java_major();

        let mut cfg = worker();
        let old = fake_java(dir.path(), "17.0.20.1");
        cfg.dependencies.java.binary = Some(old.clone());
        let found = problems(&cfg);
        let msg = format!("java 17 at {}; Jenkins agents need Java {want}", old.display());
        assert!(found.iter().any(|p| p.starts_with(&msg)), "{found:?}");
        assert!(found.iter().any(|p| p.contains("Use existing config")), "{found:?}");

        cfg.dependencies.java.binary = Some(fake_java(dir.path(), &format!("{want}.0.4")));
        let found = problems(&cfg);
        assert!(!found.iter().any(|p| p.starts_with("java ")), "{found:?}");
    }
}
