//! `mac-k3d prepare` questions. Each role asks its own questions in its own
//! module; this one asks the shared ones and shows the summary. Nothing here
//! installs or registers anything: `prepare::apply` does that after the YAML
//! is saved, so a failed install never loses the answers.

mod controller;
mod prompts;
mod standalone;
mod worker;

use dialoguer::{theme::ColorfulTheme, Confirm, Select};

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::{Error, Result};
use crate::prepare::discovery::DiscoveredDeps;
use crate::prepare::volumes::VolumeCandidate;

pub use worker::{blank_api_keys_hint, DEFAULT_CONTROLLER_JENKINS_URL};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MacRole {
    Standalone,
    Controller,
    Worker,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExistingConfigAction {
    /// Keep the answers: finish pending installs, then validate.
    UseExisting,
    RerunWizard,
    Cancel,
}

/// What to do when config already exists on an interactive run.
pub fn prompt_existing_config() -> Result<ExistingConfigAction> {
    let options = [
        "Use existing config (finish pending installs, then validate)",
        "Re-run wizard (overwrite config)",
        "Cancel",
    ];
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("Config already exists")
        .items(&options)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    Ok(match selection {
        0 => ExistingConfigAction::UseExisting,
        1 => ExistingConfigAction::RerunWizard,
        _ => ExistingConfigAction::Cancel,
    })
}

/// Ask every question for this machine and return the config to save.
pub fn run(volumes: Vec<VolumeCandidate>, discovered: DiscoveredDeps) -> Result<MacK3dConfig> {
    println!("\nmac-k3d prepare — interactive setup\n");

    let base_dir = prompts::prompt_storage_base(&volumes)?;
    let role = prompts::prompt_role()?;
    let mut config = match role {
        MacRole::Worker => worker::ask(&base_dir, &discovered)?,
        MacRole::Controller => controller::ask(&discovered)?,
        MacRole::Standalone => standalone::ask(&discovered)?,
    };
    config.platform = Some(crate::platform::platform_label().into());
    config.storage = prompts::storage_for(&base_dir);
    if config.jenkins_agent.cpu_cores == 0 {
        config.jenkins_agent.cpu_cores = crate::prepare::resources::logical_cpu_cores();
    }

    // Remap busy host ports before the summary so users see the final ports.
    if !matches!(config.role, NodeRole::Worker) {
        let remaps = crate::runtime::ports::ensure_host_ports_available(&mut config)?;
        crate::runtime::ports::print_port_remaps(&remaps);
    }

    prompts::print_summary(&config, role_tools(config.role));

    if !Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt("Write configuration and apply setup?")
        .default(true)
        .interact()
        .map_err(|_| Error::Cancelled)?
    {
        return Err(Error::Cancelled);
    }
    Ok(config)
}

/// Tools each role is asked about, in question order.
pub fn role_tools(role: NodeRole) -> &'static [&'static str] {
    match role {
        NodeRole::Worker => worker::TOOLS,
        NodeRole::Controller => controller::TOOLS,
        NodeRole::Standalone => standalone::TOOLS,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_is_asked_only_what_an_agent_needs() {
        let tools = role_tools(NodeRole::Worker);
        assert_eq!(tools, &["docker", "java", "git", "harbor"]);
        for absent in ["k3d", "kubectl", "helm", "lolbench"] {
            assert!(!tools.contains(&absent), "worker must not be asked about {absent}");
        }
    }

    #[test]
    fn controller_hosts_the_cluster_and_never_runs_harbor() {
        let tools = role_tools(NodeRole::Controller);
        assert_eq!(tools, &["docker", "k3d", "kubectl", "helm"]);
    }
}
