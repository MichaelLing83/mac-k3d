use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::prepare::jenkins_agent;
use crate::runtime::docker::{self, DockerStatus};
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::Tools;

#[derive(Debug, Args)]
pub struct TeardownArgs {
    /// Also stop Docker Desktop (default: leave Docker running)
    #[arg(long)]
    pub stop_docker: bool,

    /// Worker: also delete Jenkins agent node + CPU_CORES resources on the controller
    #[arg(long)]
    pub deregister_agent: bool,
}

pub async fn run(args: TeardownArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let tools = Tools::from_config(config)?;
    let info = k3d::inspect(&tools.k3d, &config.cluster.name).await?;

    match info.state {
        ClusterState::Running => k3d::stop(&tools.k3d, &config.cluster.name).await?,
        ClusterState::Stopped => {
            println!(
                "k3d cluster '{}' is already stopped.",
                config.cluster.name
            );
        }
        ClusterState::Missing => {
            println!(
                "k3d cluster '{}' does not exist.",
                config.cluster.name
            );
        }
    }

    if args.stop_docker {
        match docker::status(&tools.docker).await {
            DockerStatus::Running => docker::quit().await?,
            other => println!("Docker Desktop is {other}; nothing to quit."),
        }
    }

    if args.deregister_agent {
        if matches!(config.role, NodeRole::Worker) {
            jenkins_agent::remove_worker_agent(config)?;
        } else {
            println!("--deregister-agent applies to worker role only; ignoring.");
        }
    } else if matches!(config.role, NodeRole::Worker) {
        println!(
            "Jenkins agent left registered (teardown only stops k3d).\n\
             Use `mac-k3d clean --yes` or `teardown --deregister-agent` to remove the node."
        );
    }

    println!("Teardown complete. Cluster data is preserved; use `mac-k3d clean --yes` to delete.");
    Ok(())
}
