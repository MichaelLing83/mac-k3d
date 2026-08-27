pub mod agent_service;
pub mod discovery;
pub mod install;
pub mod jenkins_agent;
pub mod jenkins_credentials;
pub mod jenkins_job;
pub mod lolbench;
pub mod path_env;
pub mod resources;
pub mod volumes;
pub mod wizard;

use std::path::Path;

use crate::config::{DependencySource, LolbenchSource, MacK3dConfig, NodeRole};
use crate::error::{Error, Result};

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
                        problems.push(format!(
                            "{name} binary not found: {}",
                            bin.display()
                        ));
                    }
                } else if is_required {
                    problems.push(format!("{name} has source=existing but no binary path"));
                }
                if name == "docker" {
                    if let Some(app) = &entry.app {
                        if !app.exists() {
                            problems.push(format!(
                                "Docker.app not found: {}",
                                app.display()
                            ));
                        }
                    }
                }
            }
            DependencySource::Install if is_required => {
                problems.push(format!(
                    "{name} is marked for install; run `mac-k3d prepare` to install"
                ));
            }
            _ => {}
        }
    }

    if config.cluster.name.is_empty()
        || !config
            .cluster
            .name
            .chars()
            .all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '-')
    {
        problems.push(format!(
            "invalid cluster name '{}': use lowercase letters, digits, and hyphens",
            config.cluster.name
        ));
    }

    if matches!(config.role, NodeRole::Worker | NodeRole::Controller)
        || config.lolbench.source != LolbenchSource::Skip
    {
        if let Some(path) = &config.lolbench.path {
            if !path.exists() {
                problems.push(format!("lolbench.path not found: {}", path.display()));
            } else if !lolbench::looks_like_lolbench(path) {
                problems.push(format!(
                    "lolbench.path does not look like LoLBench: {}",
                    path.display()
                ));
            }
        } else if matches!(config.role, NodeRole::Worker)
            && config.dependencies.harbor.source != DependencySource::Skip
        {
            problems.push("worker with Harbor expects lolbench.path".into());
        }
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
    }

    let disk_path = config
        .storage
        .base_dir
        .as_deref()
        .unwrap_or(Path::new("/"));
    if let Err(e) = resources::ensure_disk_min(disk_path, config.disk_min_gb()) {
        problems.push(e.to_string());
    }

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

fn required_dependencies(config: &MacK3dConfig) -> Vec<&'static str> {
    let mut deps = vec!["docker", "k3d", "kubectl"];
    if config.jenkins.enabled || matches!(config.role, NodeRole::Controller) {
        deps.push("helm");
    }
    if matches!(config.role, NodeRole::Worker)
        || config.lolbench.source != LolbenchSource::Skip
    {
        if config.dependencies.harbor.source != DependencySource::Skip {
            deps.push("harbor");
        }
    }
    if matches!(config.role, NodeRole::Worker) {
        deps.push("java");
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
    use crate::config::MacK3dConfig;

    #[test]
    fn validate_default_config_flags_install() {
        let config = MacK3dConfig::default();
        let err = validate(&config).unwrap_err();
        assert!(err.to_string().contains("validation failed"));
    }
}
