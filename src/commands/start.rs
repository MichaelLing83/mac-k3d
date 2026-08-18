use clap::Args;

use crate::cli::JenkinsMode;
use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;

#[derive(Debug, Args)]
pub struct StartArgs {
    /// Jenkins deployment mode
    #[arg(long, value_enum, default_value_t = JenkinsMode::Skip)]
    pub jenkins: JenkinsMode,

    /// Skip waiting for Docker Desktop to become ready
    #[arg(long)]
    pub no_wait_docker: bool,
}

pub async fn run(args: StartArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    tracing::info!(cluster = %config.cluster.name, ?args.jenkins, "start: bringing up environment");

    if args.no_wait_docker {
        tracing::warn!("skipping Docker Desktop readiness check");
    }

    // TODO: open/wait for Docker Desktop, k3d cluster create/start, optional Jenkins helm install
    Ok(())
}
