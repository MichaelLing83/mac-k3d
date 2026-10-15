//! Controller (and standalone) `config`: kubeconfig, Jenkins admin, CI
//! credentials, then the eval jobs.

use std::path::Path;
use std::time::Duration;

use super::ConfigArgs;
use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::prepare::{jenkins_credentials, jenkins_job};
use crate::runtime::{jenkins, k3d, kubectl, Tools};

pub async fn run(args: &ConfigArgs, config: &MacK3dConfig, config_path: &Path) -> Result<()> {
    let mut config = config.clone();
    let tools = Tools::from_config(&config)?;
    let has_cluster = k3d::inspect(&tools.k3d, &config.cluster.name)
        .await
        .map(|i| !matches!(i.state, k3d::ClusterState::Missing))
        .unwrap_or(false);
    if has_cluster || !args.no_merge_kubeconfig {
        if !args.no_merge_kubeconfig {
            k3d::merge_kubeconfig(&tools.k3d, &config.cluster.name).await?;
            kubectl::use_context(&tools.kubectl, &config.cluster.name).await?;
        }
        println!("Waiting for Kubernetes API…");
        kubectl::wait_api(&tools.kubectl, Duration::from_secs(120)).await?;
        println!("Kubernetes API is ready.");
    }

    let admin_password = if config.jenkins.enabled || args.show_jenkins {
        show_jenkins_admin(&tools, &config).await
    } else {
        None
    };
    if !config.jenkins.enabled {
        return Ok(());
    }

    let credential_ids = credential_ids(args, &config, admin_password.as_deref());
    match credential_ids {
        Some(ids) if !args.skip_job => {
            if let Err(err) = jenkins_job::ensure_remote_trigger_token(&mut config, Some(config_path))
            {
                println!("Warning: could not prepare the remote trigger token ({err}).");
            }
            println!(
                "Ensuring Jenkins eval jobs (one / some / full suite for {}; ui_profile={})…",
                jenkins_job::EVAL_BENCHMARKS.join(", "),
                config.jenkins_job.ui_profile
            );
            if let Err(err) =
                jenkins_job::ensure_eval_jobs_from_cluster(&tools.kubectl, &config, ids).await
            {
                println!("Warning: could not ensure the eval jobs ({err}).");
            }
        }
        _ => {}
    }
    Ok(())
}

async fn show_jenkins_admin(tools: &Tools, config: &MacK3dConfig) -> Option<String> {
    println!("Jenkins UI: {}", jenkins::ui_url(config));
    match jenkins::admin_password(&tools.kubectl, config).await {
        Ok(password) if !password.is_empty() => {
            println!("Jenkins admin user: admin");
            println!("Jenkins admin password: {password}");
            Some(password)
        }
        Ok(_) | Err(_) => {
            println!(
                "Could not read Jenkins admin password yet. Try:\n  kubectl get secret {} -n {} -o jsonpath='{{.data.jenkins-admin-password}}' | base64 -d",
                config.jenkins.release_name, config.jenkins.namespace
            );
            None
        }
    }
}

/// Credential IDs the job XML binds. `None` means: leave the jobs as they are,
/// because rewriting them with an empty list would strip `withCredentials`.
fn credential_ids(
    args: &ConfigArgs,
    config: &MacK3dConfig,
    admin_password: Option<&str>,
) -> Option<Vec<String>> {
    let url = jenkins::ui_url(config);
    let Some(password) = admin_password else {
        if args.skip_secrets {
            println!(
                "Warning: could not read Jenkins admin password; skipping job rewrite so existing binds stay."
            );
            return None;
        }
        return Some(Vec::new());
    };

    if args.skip_secrets {
        return match jenkins_credentials::existing_ids_on_controller(&url, "admin", password) {
            Ok(ids) => {
                if ids.is_empty() {
                    println!(
                        "No CI credentials listed in Jenkins yet; job XML will omit withCredentials binds."
                    );
                } else {
                    println!("Keeping existing Jenkins credential binds ({} id(s)).", ids.len());
                }
                Some(ids)
            }
            Err(err) => {
                println!(
                    "Warning: could not list Jenkins credentials ({err}). Skipping job rewrite so existing binds stay."
                );
                None
            }
        };
    }

    println!("Ensuring Jenkins Credentials…");
    match jenkins_credentials::ensure_credentials_on_controller(
        &url,
        "admin",
        password,
        args.update_secrets,
    ) {
        Ok(ids) => {
            if ids.is_empty() {
                println!(
                    "No CI credentials in Jenkins yet (oracle still works).\n\
                     Re-run with `--update-secrets` or set pending secrets — see docs/secrets.md."
                );
            }
            Some(ids)
        }
        Err(err) => {
            println!("Warning: could not ensure Jenkins Credentials ({err}).");
            Some(Vec::new())
        }
    }
}
