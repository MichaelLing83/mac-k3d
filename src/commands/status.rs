use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::runtime::docker;
use crate::runtime::k3d::{self, ClusterState};
use crate::runtime::kubectl;
use crate::runtime::{jenkins, Tools};

pub async fn run(config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let tools = match Tools::from_config(config) {
        Ok(t) => t,
        Err(err) => {
            println!("Tools:           {err}");
            println!("Cluster:         {}", config.cluster.name);
            println!(
                "Jenkins:         {}",
                if config.jenkins.enabled {
                    "configured (tools missing)"
                } else {
                    "disabled"
                }
            );
            return Ok(());
        }
    };

    let docker_status = docker::status(&tools.docker).await;
    println!("Docker Desktop:  {docker_status}");

    let info = k3d::inspect(&tools.k3d, &config.cluster.name).await?;
    println!(
        "k3d cluster:     {} ({}, {} server, {} agents)",
        config.cluster.name, info.state, info.servers, info.agents
    );

    match kubectl::current_context(&tools.kubectl).await {
        Some(ctx) => println!("kubectl context: {ctx}"),
        None => println!("kubectl context: (unavailable)"),
    }

    if !config.jenkins.enabled {
        println!("Jenkins:         disabled");
    } else {
        let url = jenkins::ui_url(config);
        let phase = match info.state {
            ClusterState::Running => {
                let ctx = kubectl::context_name(&config.cluster.name);
                kubectl::jenkins_pod_phase(
                    &tools.kubectl,
                    &config.jenkins.namespace,
                    Some(&ctx),
                )
                .await
            }
            ClusterState::Missing | ClusterState::Stopped => None,
        };
        println!(
            "Jenkins:         {}",
            format_jenkins_line(info.state, phase.as_deref(), &url)
        );
    }

    Ok(())
}

/// Human-readable Jenkins line for `status` (config intent vs live cluster).
fn format_jenkins_line(cluster: ClusterState, pod_phase: Option<&str>, url: &str) -> String {
    match cluster {
        ClusterState::Missing => {
            format!("configured, not running (cluster missing), {url}")
        }
        ClusterState::Stopped => {
            format!("configured, not running (cluster stopped), {url}")
        }
        ClusterState::Running => match pod_phase {
            Some(p) => format!("configured, pod {p}, {url}"),
            None => format!("configured, pod not found, {url}"),
        },
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jenkins_line_missing_cluster() {
        let s = format_jenkins_line(ClusterState::Missing, None, "http://localhost:9080");
        assert_eq!(
            s,
            "configured, not running (cluster missing), http://localhost:9080"
        );
    }

    #[test]
    fn jenkins_line_stopped_cluster() {
        let s = format_jenkins_line(ClusterState::Stopped, None, "http://localhost:9080");
        assert!(s.contains("cluster stopped"));
    }

    #[test]
    fn jenkins_line_running_pod() {
        let s = format_jenkins_line(
            ClusterState::Running,
            Some("Running"),
            "http://localhost:9080",
        );
        assert_eq!(s, "configured, pod Running, http://localhost:9080");
    }

    #[test]
    fn jenkins_line_running_no_pod() {
        let s = format_jenkins_line(ClusterState::Running, None, "http://localhost:9080");
        assert!(s.contains("pod not found"));
    }
}
