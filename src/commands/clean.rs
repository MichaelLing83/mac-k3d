use std::path::{Path, PathBuf};

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::prepare::jenkins_agent;
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::{state, Tools};

#[derive(Debug, Args)]
pub struct CleanArgs {
    /// Remove config: the `-c` file if set, otherwise the whole `~/.config/mac-k3d/` directory
    #[arg(long)]
    pub purge_config: bool,

    /// Skip confirmation prompt
    #[arg(short, long)]
    pub yes: bool,
}

pub async fn run(
    args: CleanArgs,
    config: &MacK3dConfig,
    config_path: Option<&Path>,
) -> Result<()> {
    ensure_macos()?;

    let resolved_config = config_path
        .map(PathBuf::from)
        .unwrap_or_else(MacK3dConfig::default_config_path);
    let using_alternate_config = config_path.is_some();

    if !args.yes {
        println!(
            "clean will delete k3d cluster '{}'. Re-run with --yes to confirm.",
            config.cluster.name
        );
        if matches!(config.role, NodeRole::Worker) {
            println!(
                "For workers, this also stops the LaunchAgent and deregisters the Jenkins agent + CPU_CORES resources (when api_token is set)."
            );
        }
        if args.purge_config {
            if using_alternate_config {
                println!("This would also remove {}.", resolved_config.display());
            } else {
                println!(
                    "This would also remove {}.",
                    MacK3dConfig::config_dir().display()
                );
            }
        }
        return Ok(());
    }

    // Deregister before deleting local cluster / config (needs api_token from config).
    if matches!(config.role, NodeRole::Worker) {
        jenkins_agent::remove_worker_agent(config)?;
    }

    let tools = Tools::from_config(config)?;
    let info = k3d::inspect(&tools.k3d, &config.cluster.name).await?;
    match info.state {
        ClusterState::Missing => {
            println!(
                "k3d cluster '{}' does not exist; skipping delete.",
                config.cluster.name
            );
        }
        ClusterState::Running | ClusterState::Stopped => {
            k3d::delete(&tools.k3d, &config.cluster.name).await?;
        }
    }

    // Shared state dir is for the default/controller workflow; don't wipe it when
    // cleaning an alternate config (e.g. worker.yaml) on the same Mac.
    if using_alternate_config {
        println!(
            "Leaving {} intact (shared state; not tied to {}).",
            MacK3dConfig::state_dir().display(),
            resolved_config.display()
        );
    } else {
        state::remove_state_dir()?;
    }

    if args.purge_config {
        if using_alternate_config {
            state::remove_config_file(&resolved_config)?;
        } else {
            state::remove_config_dir()?;
        }
    }

    println!("Clean complete.");
    Ok(())
}
