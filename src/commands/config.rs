use std::time::Duration;

use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::runtime::{jenkins, kubectl, k3d, Tools};

#[derive(Debug, Args)]
pub struct ConfigArgs {
    /// Do not merge kubeconfig into ~/.kube/config
    #[arg(long)]
    pub no_merge_kubeconfig: bool,

    /// Print Jenkins URL and initial admin password
    #[arg(long)]
    pub show_jenkins: bool,
}

pub async fn run(args: ConfigArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let tools = Tools::from_config(config)?;

    if !args.no_merge_kubeconfig {
        k3d::merge_kubeconfig(&tools.k3d, &config.cluster.name).await?;
        kubectl::use_context(&tools.kubectl, &config.cluster.name).await?;
    }

    println!("Waiting for Kubernetes API…");
    kubectl::wait_api(&tools.kubectl, Duration::from_secs(120)).await?;
    println!("Kubernetes API is ready.");

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

    println!("Config complete.");
    Ok(())
}
