use std::time::Duration;

use clap::Args;

use crate::cli::JenkinsMode;
use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::runtime::docker::{self, DockerStatus};
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::kubectl;
use crate::runtime::{jenkins, state, Tools};

#[derive(Debug, Args)]
pub struct StartArgs {
    /// Jenkins deployment mode (overrides config when set)
    #[arg(long, value_enum)]
    pub jenkins: Option<JenkinsMode>,

    /// Skip waiting for Docker Desktop to become ready
    #[arg(long)]
    pub no_wait_docker: bool,
}

pub async fn run(args: StartArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let tools = Tools::from_config(config)?;

    match docker::status(&tools.docker).await {
        DockerStatus::Running => {
            println!("Docker Desktop is already running.");
        }
        DockerStatus::Stopped | DockerStatus::Missing => {
            docker::open_desktop(&tools.docker_app).await?;
            if args.no_wait_docker {
                tracing::warn!("skipping Docker Desktop readiness wait");
            } else {
                let timeout = Duration::from_secs(config.docker.startup_timeout_secs.max(1));
                docker::wait_ready(&tools.docker, timeout).await?;
            }
        }
    }

    let info = k3d::inspect(&tools.k3d, &config.cluster.name).await?;
    match info.state {
        ClusterState::Missing => k3d::create(&tools.k3d, config).await?,
        ClusterState::Stopped => k3d::start(&tools.k3d, &config.cluster.name).await?,
        ClusterState::Running => {
            println!(
                "k3d cluster '{}' is already running.",
                config.cluster.name
            );
        }
    }

    // Keep kubeconfig pointed at this config's cluster before any Helm/kubectl work.
    // (A prior `config -c worker.yaml` can leave the shell on another context.)
    if let Err(err) = kubectl::use_context(&tools.kubectl, &config.cluster.name).await {
        tracing::warn!(error = %err, "kubectl use-context failed; merging kubeconfig");
        k3d::merge_kubeconfig(&tools.k3d, &config.cluster.name).await?;
        kubectl::use_context(&tools.kubectl, &config.cluster.name).await?;
    }

    if config.jenkins.enabled {
        let helm = tools.helm_required()?;
        jenkins::install_or_upgrade(helm, config).await?;
        println!("Jenkins UI: {}", jenkins::ui_url(config));
    }

    state::write_after_start(config)?;
    println!("Start complete. Next: mac-k3d config");
    Ok(())
}
