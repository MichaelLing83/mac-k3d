use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;

#[derive(Debug, Args)]
pub struct TeardownArgs {
    /// Also stop Docker Desktop (default: leave Docker running)
    #[arg(long)]
    pub stop_docker: bool,
}

pub async fn run(args: TeardownArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    tracing::info!(
        cluster = %config.cluster.name,
        stop_docker = args.stop_docker,
        "teardown: stopping cluster and services"
    );

    // TODO: k3d cluster stop, optional docker desktop quit
    Ok(())
}
