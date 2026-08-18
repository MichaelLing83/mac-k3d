use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;
use crate::runtime::docker;
use crate::runtime::k3d;
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
                    "enabled (tools missing)"
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

    if config.jenkins.enabled {
        let phase = kubectl::jenkins_pod_phase(&tools.kubectl, &config.jenkins.namespace).await;
        let phase = phase.unwrap_or_else(|| "unknown".into());
        println!(
            "Jenkins:         enabled, pod {phase}, {}",
            jenkins::ui_url(config)
        );
    } else {
        println!("Jenkins:         disabled");
    }

    Ok(())
}
