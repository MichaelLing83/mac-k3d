use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::cli::JenkinsMode;
use crate::error::{Error, Result};

/// User-facing configuration loaded from YAML.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct MacK3dConfig {
    pub cluster: ClusterConfig,
    pub jenkins: JenkinsConfig,
    pub docker: DockerConfig,
    pub storage: StorageConfig,
    pub dependencies: DependenciesConfig,
}

/// Where large artifacts and caches are stored (set by `prepare` wizard).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct StorageConfig {
    /// Root directory on the volume with most free space (or user choice).
    pub base_dir: Option<PathBuf>,
    /// Docker image/layer data (may require manual Docker Desktop relocation).
    pub docker: Option<PathBuf>,
    /// k3d cluster and image cache.
    pub k3d: Option<PathBuf>,
    /// Jenkins Helm charts, plugins, and persistent data.
    pub jenkins: Option<PathBuf>,
    /// Large downloads (agent JARs, binaries).
    pub downloads: Option<PathBuf>,
}

/// How each external tool is resolved (discovered, installed, or skipped).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DependenciesConfig {
    pub docker: DependencyEntry,
    pub k3d: DependencyEntry,
    pub kubectl: DependencyEntry,
    pub helm: DependencyEntry,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DependencyEntry {
    /// `existing` = use discovered/specified path; `install` = install via Homebrew; `skip` = not required.
    pub source: DependencySource,
    /// Path to CLI binary when `source` is `existing`.
    pub binary: Option<PathBuf>,
    /// Application bundle path (Docker Desktop only).
    pub app: Option<PathBuf>,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum DependencySource {
    Existing,
    #[default]
    Install,
    Skip,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct ClusterConfig {
    /// k3d cluster name
    pub name: String,
    /// Number of agent nodes (in addition to the server node)
    pub agents: u8,
    /// Host ports mapped to the cluster load balancer
    pub ports: Vec<PortMapping>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PortMapping {
    pub host: u16,
    pub container: u16,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct JenkinsConfig {
    pub enabled: bool,
    pub namespace: String,
    pub release_name: String,
    /// Host port for Jenkins UI (via k3d port mapping)
    pub host_port: u16,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DockerConfig {
    /// Wait up to this many seconds for Docker Desktop to become ready
    pub startup_timeout_secs: u64,
}

impl Default for MacK3dConfig {
    fn default() -> Self {
        Self {
            cluster: ClusterConfig {
                name: "mac-k3d".into(),
                agents: 0,
                ports: default_ports(),
            },
            jenkins: JenkinsConfig {
                enabled: false,
                namespace: "jenkins".into(),
                release_name: "jenkins".into(),
                host_port: 9080,
            },
            docker: DockerConfig {
                startup_timeout_secs: 120,
            },
            storage: StorageConfig::default(),
            dependencies: DependenciesConfig::default(),
        }
    }
}

impl StorageConfig {
    /// Resolve a storage path: explicit override, or `base_dir/<sub>`, or None.
    pub fn resolve<'a>(&'a self, sub: &str, explicit: &'a Option<PathBuf>) -> Option<PathBuf> {
        explicit
            .clone()
            .or_else(|| self.base_dir.as_ref().map(|b| b.join(sub)))
    }

    pub fn docker_dir(&self) -> Option<PathBuf> {
        self.resolve("docker", &self.docker)
    }

    pub fn k3d_dir(&self) -> Option<PathBuf> {
        self.resolve("k3d", &self.k3d)
    }

    pub fn jenkins_dir(&self) -> Option<PathBuf> {
        self.resolve("jenkins", &self.jenkins)
    }

    pub fn downloads_dir(&self) -> Option<PathBuf> {
        self.resolve("downloads", &self.downloads)
    }
}

fn default_ports() -> Vec<PortMapping> {
    vec![
        PortMapping {
            host: 8080,
            container: 80,
        },
        PortMapping {
            host: 8443,
            container: 443,
        },
    ]
}

impl MacK3dConfig {
    pub fn config_dir() -> PathBuf {
        home_dir()
            .map(|h| h.join(".config").join("mac-k3d"))
            .unwrap_or_else(|| PathBuf::from(".mac-k3d"))
    }

    pub fn default_config_path() -> PathBuf {
        Self::config_dir().join("config.yaml")
    }

    pub fn state_dir() -> PathBuf {
        home_dir()
            .map(|h| h.join(".local").join("state").join("mac-k3d"))
            .unwrap_or_else(|| PathBuf::from(".mac-k3d"))
    }

    pub fn load(path: Option<&Path>) -> Result<Self> {
        let path = path
            .map(PathBuf::from)
            .unwrap_or_else(Self::default_config_path);

        if !path.exists() {
            return Ok(Self::default());
        }

        let contents = std::fs::read_to_string(&path)
            .map_err(|e| Error::Config(format!("failed to read {}: {e}", path.display())))?;

        serde_yaml::from_str(&contents)
            .map_err(|e| Error::Config(format!("failed to parse {}: {e}", path.display())))
    }

    pub fn save(&self, path: Option<&Path>) -> Result<()> {
        let path = path
            .map(PathBuf::from)
            .unwrap_or_else(Self::default_config_path);

        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Config(format!("failed to create config dir: {e}")))?;
        }

        let contents = serde_yaml::to_string(self)
            .map_err(|e| Error::Config(format!("failed to serialize config: {e}")))?;

        std::fs::write(&path, contents)
            .map_err(|e| Error::Config(format!("failed to write {}: {e}", path.display())))?;

        Ok(())
    }

    pub fn apply_jenkins_mode(&mut self, mode: JenkinsMode) {
        self.jenkins.enabled = matches!(mode, JenkinsMode::InCluster);
    }
}

impl DependenciesConfig {
    pub fn entries(&self) -> [(&str, &DependencyEntry); 4] {
        [
            ("docker", &self.docker),
            ("k3d", &self.k3d),
            ("kubectl", &self.kubectl),
            ("helm", &self.helm),
        ]
    }
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}
