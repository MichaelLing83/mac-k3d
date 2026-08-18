use std::fs;
use std::time::{SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ClusterStateFile {
    pub name: String,
    pub updated_at_unix: u64,
    pub agents: u8,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct JenkinsStateFile {
    pub namespace: String,
    pub release: String,
    pub host_port: u16,
}

pub fn write_after_start(config: &MacK3dConfig) -> Result<()> {
    let dir = MacK3dConfig::state_dir();
    fs::create_dir_all(&dir)
        .map_err(|e| Error::Config(format!("failed to create state dir: {e}")))?;

    let cluster = ClusterStateFile {
        name: config.cluster.name.clone(),
        updated_at_unix: unix_now(),
        agents: config.cluster.agents,
    };
    write_json(&dir.join("cluster.json"), &cluster)?;

    if config.jenkins.enabled {
        let jenkins = JenkinsStateFile {
            namespace: config.jenkins.namespace.clone(),
            release: config.jenkins.release_name.clone(),
            host_port: config.jenkins.host_port,
        };
        write_json(&dir.join("jenkins.json"), &jenkins)?;
    }

    Ok(())
}

pub fn remove_state_dir() -> Result<()> {
    let dir = MacK3dConfig::state_dir();
    if dir.exists() {
        fs::remove_dir_all(&dir)
            .map_err(|e| Error::Config(format!("failed to remove {}: {e}", dir.display())))?;
        println!("Removed {}", dir.display());
    }
    Ok(())
}

pub fn remove_config_dir() -> Result<()> {
    let dir = MacK3dConfig::config_dir();
    if dir.exists() {
        fs::remove_dir_all(&dir)
            .map_err(|e| Error::Config(format!("failed to remove {}: {e}", dir.display())))?;
        println!("Removed {}", dir.display());
    }
    Ok(())
}

fn write_json<T: Serialize>(path: &std::path::Path, value: &T) -> Result<()> {
    let contents = serde_json::to_string_pretty(value)
        .map_err(|e| Error::Config(format!("failed to serialize {}: {e}", path.display())))?;
    fs::write(path, contents)
        .map_err(|e| Error::Config(format!("failed to write {}: {e}", path.display())))?;
    Ok(())
}

fn unix_now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unix_now_nonzero() {
        assert!(unix_now() > 0);
    }
}
