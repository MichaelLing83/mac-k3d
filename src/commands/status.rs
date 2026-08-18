use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;

pub async fn run(config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    tracing::info!(cluster = %config.cluster.name, jenkins = config.jenkins.enabled, "status: reporting environment state");

    // TODO: docker info, k3d cluster list, kubectl cluster-info, jenkins pod status
    Ok(())
}
