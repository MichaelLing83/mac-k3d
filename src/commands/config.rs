use std::time::Duration;

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::prepare::jenkins_agent;
use crate::runtime::{jenkins, kubectl, k3d, Tools};

#[derive(Debug, Args)]
pub struct ConfigArgs {
    /// Do not merge kubeconfig into ~/.kube/config
    #[arg(long)]
    pub no_merge_kubeconfig: bool,

    /// Print Jenkins URL and initial admin password
    #[arg(long)]
    pub show_jenkins: bool,

    /// Skip Jenkins agent register/launch-script update (worker only)
    #[arg(long)]
    pub skip_agent: bool,
}

pub async fn run(args: ConfigArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let tools = Tools::from_config(config)?;

    // Workers may skip a local k3d cluster; only merge when the named cluster exists.
    let has_cluster = k3d::inspect(&tools.k3d, &config.cluster.name)
        .await
        .map(|i| !matches!(i.state, k3d::ClusterState::Missing))
        .unwrap_or(false);

    if has_cluster {
        if !args.no_merge_kubeconfig {
            k3d::merge_kubeconfig(&tools.k3d, &config.cluster.name).await?;
            kubectl::use_context(&tools.kubectl, &config.cluster.name).await?;
        }
        println!("Waiting for Kubernetes API…");
        kubectl::wait_api(&tools.kubectl, Duration::from_secs(120)).await?;
        println!("Kubernetes API is ready.");
    } else if matches!(config.role, NodeRole::Worker) {
        println!(
            "k3d cluster '{}' not present — skipping kubeconfig (OK for Jenkins-agent-only workers).",
            config.cluster.name
        );
    } else if !args.no_merge_kubeconfig {
        k3d::merge_kubeconfig(&tools.k3d, &config.cluster.name).await?;
        kubectl::use_context(&tools.kubectl, &config.cluster.name).await?;
        println!("Waiting for Kubernetes API…");
        kubectl::wait_api(&tools.kubectl, Duration::from_secs(120)).await?;
        println!("Kubernetes API is ready.");
    }

    if config.jenkins.enabled || args.show_jenkins {
        println!("Jenkins UI: {}", jenkins::ui_url(config));
        match jenkins::admin_password(&tools.kubectl, config).await {
            Ok(password) if !password.is_empty() => {
                println!("Jenkins admin user: admin");
                println!("Jenkins admin password: {password}");
            }
            Ok(_) | Err(_) => {
                println!(
                    "Could not read Jenkins admin password yet. Try:\n  kubectl get secret {} -n {} -o jsonpath='{{.data.jenkins-admin-password}}' | base64 -d",
                    config.jenkins.release_name, config.jenkins.namespace
                );
            }
        }
    }

    if matches!(config.role, NodeRole::Worker) && !args.skip_agent {
        println!("Ensuring Jenkins agent registration…");
        jenkins_agent::ensure_worker_agent(config)?;
    }

    println!("Config complete.");
    Ok(())
}
