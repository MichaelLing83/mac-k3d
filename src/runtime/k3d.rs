use std::path::Path;

use serde::Deserialize;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::runtime::exec;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ClusterState {
    Missing,
    Stopped,
    Running,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct K3dCluster {
    #[serde(default)]
    name: String,
    #[serde(default)]
    nodes: Vec<K3dNode>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct K3dNode {
    #[serde(default)]
    #[allow(dead_code)]
    name: String,
    #[serde(default)]
    role: String,
    #[serde(default, alias = "State", alias = "state")]
    state: Option<K3dNodeState>,
}

#[derive(Debug, Clone, Default, Deserialize)]
struct K3dNodeState {
    #[serde(default, alias = "Running", alias = "running")]
    running: bool,
}

#[derive(Debug, Clone)]
pub struct ClusterInfo {
    pub state: ClusterState,
    pub servers: usize,
    pub agents: usize,
}

impl Default for ClusterInfo {
    fn default() -> Self {
        Self {
            state: ClusterState::Missing,
            servers: 0,
            agents: 0,
        }
    }
}

pub fn create_args(config: &MacK3dConfig) -> Vec<String> {
    let mut args = vec![
        "cluster".into(),
        "create".into(),
        config.cluster.name.clone(),
        "--agents".into(),
        config.cluster.agents.to_string(),
        "--wait".into(),
    ];

    for port in &config.cluster.ports {
        args.push("--port".into());
        args.push(format!("{}:{}@loadbalancer", port.host, port.container));
    }

    if config.jenkins.enabled {
        let jenkins_map = format!("{}:8080@loadbalancer", config.jenkins.host_port);
        if !args.iter().any(|a| a == &jenkins_map) {
            args.push("--port".into());
            args.push(jenkins_map);
        }
    }

    args
}

pub async fn inspect(k3d: &Path, name: &str) -> Result<ClusterInfo> {
    let stdout = match exec::capture(k3d, &["cluster", "list", "--output", "json"]).await {
        Ok(s) => s,
        Err(_) => return Ok(ClusterInfo::default()),
    };

    let clusters: Vec<K3dCluster> = serde_json::from_str(&stdout).unwrap_or_default();
    let Some(cluster) = clusters.into_iter().find(|c| c.name == name) else {
        return Ok(ClusterInfo::default());
    };

    let mut servers = 0usize;
    let mut agents = 0usize;
    let mut any_running = false;

    for node in &cluster.nodes {
        let running = node
            .state
            .as_ref()
            .map(|s| s.running)
            .unwrap_or(false);
        match node.role.to_lowercase().as_str() {
            "server" => servers += 1,
            "agent" => agents += 1,
            _ => {}
        }
        if running {
            any_running = true;
        }
    }

    // If JSON omitted node state, treat listed clusters as running.
    if cluster.nodes.is_empty() {
        any_running = true;
        servers = 1;
    }

    Ok(ClusterInfo {
        state: if any_running {
            ClusterState::Running
        } else {
            ClusterState::Stopped
        },
        servers: servers.max(1),
        agents,
    })
}

pub async fn create(k3d: &Path, config: &MacK3dConfig) -> Result<()> {
    let args = create_args(config);
    let display: Vec<&str> = args.iter().map(String::as_str).collect();
    println!("Creating k3d cluster '{}'…", config.cluster.name);
    exec::visible(k3d, &display).await
}

pub async fn start(k3d: &Path, name: &str) -> Result<()> {
    println!("Starting k3d cluster '{name}'…");
    exec::visible(k3d, &["cluster", "start", name]).await
}

pub async fn stop(k3d: &Path, name: &str) -> Result<()> {
    println!("Stopping k3d cluster '{name}'…");
    exec::visible(k3d, &["cluster", "stop", name]).await
}

pub async fn delete(k3d: &Path, name: &str) -> Result<()> {
    println!("Deleting k3d cluster '{name}'…");
    exec::visible(k3d, &["cluster", "delete", name]).await
}

pub async fn merge_kubeconfig(k3d: &Path, name: &str) -> Result<()> {
    println!("Merging kubeconfig for cluster '{name}'…");
    exec::visible(
        k3d,
        &[
            "kubeconfig",
            "merge",
            name,
            "--kubeconfig-merge-default",
            "--kubeconfig-switch-context",
        ],
    )
    .await
}

impl std::fmt::Display for ClusterState {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Missing => write!(f, "missing"),
            Self::Stopped => write!(f, "stopped"),
            Self::Running => write!(f, "running"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{MacK3dConfig, PortMapping};

    #[test]
    fn create_args_include_ports_and_agents() {
        let mut config = MacK3dConfig::default();
        config.cluster.name = "dev".into();
        config.cluster.agents = 2;
        config.cluster.ports = vec![PortMapping {
            host: 8080,
            container: 80,
        }];
        config.jenkins.enabled = false;

        let args = create_args(&config);
        assert!(args.contains(&"dev".to_string()));
        assert!(args.contains(&"2".to_string()));
        assert!(args.iter().any(|a| a == "8080:80@loadbalancer"));
        assert!(!args.iter().any(|a| a.contains(":8080@loadbalancer")));
    }

    #[test]
    fn create_args_add_jenkins_port_when_enabled() {
        let mut config = MacK3dConfig::default();
        config.jenkins.enabled = true;
        config.jenkins.host_port = 9080;
        let args = create_args(&config);
        assert!(args.iter().any(|a| a == "9080:8080@loadbalancer"));
    }
}
