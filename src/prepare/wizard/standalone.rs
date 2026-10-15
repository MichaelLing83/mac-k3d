//! Standalone questions: a local k3d cluster with no Jenkins, and optionally
//! the pinned Harbor for `mac-k3d eval --local`.

use dialoguer::{theme::ColorfulTheme, Confirm, Input};

use super::prompts;
use crate::config::{ClusterConfig, DependenciesConfig, MacK3dConfig, NodeRole};
use crate::error::{Error, Result};
use crate::prepare::discovery::DiscoveredDeps;
use crate::prepare::toolchain;

pub(super) const TOOLS: &[&str] = &["docker", "k3d", "kubectl", "harbor", "git"];

pub(super) fn ask(discovered: &DiscoveredDeps) -> Result<MacK3dConfig> {
    let docker = prompts::prompt_dependency(
        crate::platform::docker_display_name(),
        discovered.docker.as_ref(),
        true,
        true,
    )?;
    let k3d = prompts::prompt_dependency("k3d", discovered.k3d.as_ref(), true, false)?;
    let kubectl = prompts::prompt_dependency("kubectl", discovered.kubectl.as_ref(), true, false)?;

    let local_evals = Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt(format!(
            "Run evals on this {} with `mac-k3d eval --local` (installs Harbor {} and needs git)?",
            crate::platform::machine_noun(),
            toolchain::harbor_version()
        ))
        .default(false)
        .interact()
        .map_err(|_| Error::Cancelled)?;
    let (harbor, git) = if local_evals {
        (
            prompts::pinned_harbor(),
            prompts::prompt_dependency("git", discovered.git.as_ref(), true, false)?,
        )
    } else {
        (
            prompts::not_needed(discovered.harbor.as_ref()),
            prompts::not_needed(discovered.git.as_ref()),
        )
    };

    let name: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Cluster name")
        .default("mac-k3d".into())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;
    let agents: u8 = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Number of k3d agent nodes")
        .default(0)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    Ok(MacK3dConfig {
        role: NodeRole::Standalone,
        cluster: ClusterConfig {
            name,
            agents,
            ports: prompts::default_ports(),
        },
        dependencies: DependenciesConfig {
            docker,
            k3d,
            kubectl,
            helm: prompts::not_needed(discovered.helm.as_ref()),
            harbor,
            java: prompts::not_needed(discovered.java.as_ref()),
            git,
        },
        ..MacK3dConfig::default()
    })
}
