use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;

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
        tracing::warn!("clean will delete cluster {}; re-run with --yes to confirm", config.cluster.name);
        return Ok(());
    }

    tracing::info!(
        cluster = %config.cluster.name,
        purge_config = args.purge_config,
        "clean: removing cluster and local artifacts"
    );

    // TODO: k3d cluster delete, prune volumes, optional config/state purge
    Ok(())
}
