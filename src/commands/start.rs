use std::path::Path;
use std::time::Duration;

use clap::Args;

use crate::cli::JenkinsMode;
use crate::config::{MacK3dConfig, NodeRole};
use crate::error::{Error, Result};
use crate::platform::ensure_supported_os;
use crate::prepare::{jenkins_credentials, jenkins_job};
use crate::runtime::docker::{self, DockerStatus};
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::kubectl;
use crate::runtime::ports;
use crate::runtime::{jenkins, state, Tools};

#[derive(Debug, Default, Args)]
pub struct StartArgs {
    /// Jenkins deployment mode (overrides config when set)
    #[arg(long, value_enum)]
    pub jenkins: Option<JenkinsMode>,

    /// Skip waiting for Docker to become ready
    #[arg(long)]
    pub no_wait_docker: bool,

    /// Skip creating the `lolbench_one_task` Pipeline job after Jenkins install
    #[arg(long)]
    pub skip_job: bool,
}

pub async fn run(args: StartArgs, config: &MacK3dConfig, config_path: Option<&Path>) -> Result<()> {
    ensure_supported_os()?;
    reject_worker_start(config)?;

    let mut config = config.clone();
    let tools = Tools::from_config(&config)?;

    match docker::status(&tools.docker).await {
        DockerStatus::Running => {
            println!(
                "{} is already running.",
                crate::platform::docker_display_name()
            );
        }
        DockerStatus::Stopped | DockerStatus::Missing => {
            docker::open_desktop(&tools.docker_app).await?;
            if args.no_wait_docker {
                tracing::warn!(
                    "skipping {} readiness wait",
                    crate::platform::docker_display_name()
                );
            } else {
                let timeout = Duration::from_secs(config.docker.startup_timeout_secs.max(1));
                docker::wait_ready(&tools.docker, timeout).await?;
            }
        }
    }

    let info = k3d::inspect(&tools.k3d, &config.cluster.name).await?;
    match info.state {
        ClusterState::Missing => {
            let remaps = ports::ensure_host_ports_available(&mut config)?;
            ports::print_port_remaps(&remaps);
            if !remaps.is_empty() {
                if let Err(err) = config.save(config_path) {
                    tracing::warn!(error = %err, "could not persist remapped host ports to config");
                } else if let Some(path) = config_path {
                    println!("Updated host ports in {}", path.display());
                } else {
                    println!(
                        "Updated host ports in {}",
                        MacK3dConfig::default_config_path().display()
                    );
                }
            }
            k3d::create(&tools.k3d, &config).await?;
        }
        ClusterState::Stopped => k3d::start(&tools.k3d, &config.cluster.name).await?,
        ClusterState::Running => {
            println!("k3d cluster '{}' is already running.", config.cluster.name);
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
        jenkins::install_or_upgrade(helm, &config).await?;
        println!("Jenkins UI: {}", jenkins::ui_url(&config));
        if !args.skip_job {
            let credential_ids = match jenkins::admin_password(&tools.kubectl, &config).await {
                Ok(password) if !password.is_empty() => {
                    match jenkins_credentials::existing_ids_on_controller(
                        &jenkins::ui_url(&config),
                        "admin",
                        &password,
                    ) {
                        Ok(ids) => Some(ids),
                        Err(err) => {
                            println!(
                                "Note: could not list Jenkins credentials ({err}). Skipping job rewrite. Re-run `mac-k3d config`."
                            );
                            None
                        }
                    }
                }
                Ok(_) | Err(_) => {
                    println!(
                        "Note: could not read Jenkins admin password yet. Skipping job rewrite. Re-run `mac-k3d config`."
                    );
                    None
                }
            };
            if let Some(credential_ids) = credential_ids {
                if let Err(err) =
                    jenkins_job::ensure_remote_trigger_token(&mut config, config_path)
                {
                    println!("Warning: could not prepare the remote trigger token ({err}).");
                }
                println!(
                    "Ensuring Jenkins eval jobs (one / some / full suite for {}; ui_profile={})…",
                    jenkins_job::EVAL_BENCHMARKS.join(", "),
                    config.jenkins_job.ui_profile
                );
                // Best-effort: config will retry if Jenkins is still warming up.
                if let Err(err) = jenkins_job::ensure_eval_jobs_from_cluster(
                    &tools.kubectl,
                    &config,
                    credential_ids,
                )
                .await
                {
                    println!(
                        "Note: could not create the eval jobs yet ({err}). Re-run `mac-k3d config`."
                    );
                }
            }
        }
    }

    state::write_after_start(&config)?;
    println!("Start complete. Next: mac-k3d config");
    Ok(())
}

/// Workers must not create a k3d cluster. Use `config` / `setup` instead.
fn reject_worker_start(config: &MacK3dConfig) -> Result<()> {
    if matches!(config.role, NodeRole::Worker) {
        return Err(Error::Config(
            "start is for controller/standalone configs. Workers use `mac-k3d config` (or `setup`). Do not start k3d from a worker YAML.".into(),
        ));
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_yaml_cannot_start() {
        let mut config = MacK3dConfig::default();
        config.role = NodeRole::Worker;
        let err = reject_worker_start(&config).unwrap_err().to_string();
        assert!(err.contains("Workers use"), "{err}");
    }

    #[test]
    fn controller_may_start() {
        let mut config = MacK3dConfig::default();
        config.role = NodeRole::Controller;
        reject_worker_start(&config).unwrap();
    }
}
