use std::time::Duration;

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::prepare::{jenkins_agent, jenkins_credentials, jenkins_job};
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

    /// Skip creating the `lolbench_one_task` Pipeline job (controller / Jenkins enabled)
    #[arg(long)]
    pub skip_job: bool,

    /// Skip creating/updating Jenkins Credentials from pending secrets
    #[arg(long)]
    pub skip_secrets: bool,

    /// Re-prompt for CI secrets even if Jenkins credentials already exist
    #[arg(long)]
    pub update_secrets: bool,
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

    let mut admin_password: Option<String> = None;
    if config.jenkins.enabled || args.show_jenkins {
        println!("Jenkins UI: {}", jenkins::ui_url(config));
        match jenkins::admin_password(&tools.kubectl, config).await {
            Ok(password) if !password.is_empty() => {
                println!("Jenkins admin user: admin");
                println!("Jenkins admin password: {password}");
                admin_password = Some(password);
            }
            Ok(_) | Err(_) => {
                println!(
                    "Could not read Jenkins admin password yet. Try:\n  kubectl get secret {} -n {} -o jsonpath='{{.data.jenkins-admin-password}}' | base64 -d",
                    config.jenkins.release_name, config.jenkins.namespace
                );
            }
        }
    }

    let mut credential_ids = Vec::new();
    if config.jenkins.enabled && !args.skip_secrets {
        if let Some(password) = admin_password.as_deref() {
            println!("Ensuring Jenkins Credentials…");
            match jenkins_credentials::ensure_credentials_on_controller(
                &jenkins::ui_url(config),
                "admin",
                password,
                args.update_secrets,
            ) {
                Ok(ids) => {
                    credential_ids = ids;
                    if credential_ids.is_empty() {
                        println!(
                            "No CI credentials in Jenkins yet (oracle still works).\n\
                             Re-run with `--update-secrets` or set pending secrets — see docs/secrets.md."
                        );
                    }
                }
                Err(err) => {
                    println!("Warning: could not ensure Jenkins Credentials ({err}).");
                }
            }
        }
    }

    if config.jenkins.enabled && !args.skip_job {
        println!("Ensuring Jenkins job '{}'…", jenkins_job::LOLBENCH_ONE_TASK);
        if let Err(err) = jenkins_job::ensure_lolbench_one_task_from_cluster(
            &tools.kubectl,
            config,
            credential_ids,
        )
        .await
        {
            println!(
                "Warning: could not ensure '{}' ({err}).",
                jenkins_job::LOLBENCH_ONE_TASK
            );
        }
    }

    if matches!(config.role, NodeRole::Worker) && !args.skip_agent {
        println!("Ensuring Jenkins agent registration…");
        jenkins_agent::ensure_worker_agent(config)?;
    }

    println!("Config complete.");
    Ok(())
}
