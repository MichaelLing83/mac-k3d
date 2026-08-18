use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::{state, Tools};

#[derive(Debug, Args)]
pub struct CleanArgs {
    /// Remove local config and state directories
    #[arg(long)]
    pub purge_config: bool,

    /// Skip confirmation prompt
    #[arg(short, long)]
    pub yes: bool,
}

pub async fn run(args: CleanArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    if !args.yes {
        println!(
            "clean will delete k3d cluster '{}'. Re-run with --yes to confirm.",
            config.cluster.name
        );
        if args.purge_config {
            println!(
                "This would also remove {}.",
                MacK3dConfig::config_dir().display()
            );
        }
        return Ok(());
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

    state::remove_state_dir()?;
    if args.purge_config {
        state::remove_config_dir()?;
    }

    println!("Clean complete.");
    Ok(())
}
