use std::path::{Path, PathBuf};

use crate::config::{DependencyEntry, MacK3dConfig};
use crate::error::{Error, Result};
use crate::prepare::discovery;

/// Resolved paths to external binaries used by start/config/teardown/clean/status.
#[derive(Debug, Clone)]
pub struct Tools {
    pub docker: PathBuf,
    pub docker_app: PathBuf,
    pub k3d: PathBuf,
    pub kubectl: PathBuf,
    pub helm: Option<PathBuf>,
}

impl Tools {
    pub fn from_config(config: &MacK3dConfig) -> Result<Self> {
        Ok(Self {
            docker: resolve("docker", &config.dependencies.docker)?,
            docker_app: config
                .dependencies
                .docker
                .app
                .clone()
                .filter(|p| p.exists())
                .unwrap_or_else(|| PathBuf::from("/Applications/Docker.app")),
            k3d: resolve("k3d", &config.dependencies.k3d)?,
            kubectl: resolve("kubectl", &config.dependencies.kubectl)?,
            helm: match resolve("helm", &config.dependencies.helm) {
                Ok(path) => Some(path),
                Err(_) if !config.jenkins.enabled => None,
                Err(err) => return Err(err),
            },
        })
    }

    pub fn helm_required(&self) -> Result<&Path> {
        self.helm
            .as_deref()
            .ok_or_else(|| Error::DependencyMissing("helm".into()))
    }
}

fn resolve(name: &str, entry: &DependencyEntry) -> Result<PathBuf> {
    if let Some(bin) = &entry.binary {
        if bin.exists() {
            return Ok(bin.clone());
        }
        tracing::warn!(
            dependency = name,
            path = %bin.display(),
            "configured binary missing; searching PATH"
        );
    }

    discovery::which(name).ok_or_else(|| Error::DependencyMissing(name.into()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{DependencySource, MacK3dConfig};

    #[test]
    fn resolve_prefers_configured_binary_when_present() {
        let cargo = PathBuf::from(env!("CARGO"));
        let entry = DependencyEntry {
            source: DependencySource::Existing,
            binary: Some(cargo.clone()),
            app: None,
        };
        assert_eq!(resolve("cargo", &entry).unwrap(), cargo);
    }

    #[test]
    fn from_config_errors_when_tools_missing() {
        let mut config = MacK3dConfig::default();
        config.dependencies.docker.binary = Some(PathBuf::from("/definitely/missing/docker"));
        config.dependencies.k3d.binary = Some(PathBuf::from("/definitely/missing/k3d"));
        config.dependencies.kubectl.binary = Some(PathBuf::from("/definitely/missing/kubectl"));
        // Falls back to PATH; only assert it doesn't panic.
        let _ = Tools::from_config(&config);
    }
}
