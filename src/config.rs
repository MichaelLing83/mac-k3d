use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::cli::JenkinsMode;
use crate::error::{Error, Result};

/// User-facing configuration loaded from YAML.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct MacK3dConfig {
    /// Machine role from prepare wizard.
    pub role: NodeRole,
    pub cluster: ClusterConfig,
    pub jenkins: JenkinsConfig,
    pub docker: DockerConfig,
    pub storage: StorageConfig,
    pub dependencies: DependenciesConfig,
    pub lolbench: LolbenchConfig,
    pub jenkins_agent: JenkinsAgentConfig,
    pub jenkins_job: JenkinsJobConfig,
    pub resources: ResourcesConfig,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum NodeRole {
    #[default]
    Standalone,
    Controller,
    Worker,
}

/// Where large artifacts and caches are stored (set by `prepare` wizard).
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct StorageConfig {
    pub base_dir: Option<PathBuf>,
    pub docker: Option<PathBuf>,
    pub k3d: Option<PathBuf>,
    pub jenkins: Option<PathBuf>,
    pub downloads: Option<PathBuf>,
}

/// How each external tool is resolved (discovered, installed, or skipped).
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct DependenciesConfig {
    pub docker: DependencyEntry,
    pub k3d: DependencyEntry,
    pub kubectl: DependencyEntry,
    pub helm: DependencyEntry,
    pub harbor: DependencyEntry,
    pub java: DependencyEntry,
}

impl Default for DependenciesConfig {
    fn default() -> Self {
        Self {
            docker: DependencyEntry::default(),
            k3d: DependencyEntry::default(),
            kubectl: DependencyEntry::default(),
            helm: DependencyEntry {
                source: DependencySource::Skip,
                ..DependencyEntry::default()
            },
            harbor: DependencyEntry {
                source: DependencySource::Skip,
                ..DependencyEntry::default()
            },
            java: DependencyEntry {
                source: DependencySource::Skip,
                ..DependencyEntry::default()
            },
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DependencyEntry {
    pub source: DependencySource,
    pub binary: Option<PathBuf>,
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
    pub name: String,
    pub agents: u8,
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
    pub host_port: u16,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DockerConfig {
    pub startup_timeout_secs: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct LolbenchConfig {
    /// Path to LoLBench-Preview checkout (optional on standalone).
    pub path: Option<PathBuf>,
    pub source: LolbenchSource,
    /// Git remote used when cloning (printed/used by prepare).
    pub git_url: String,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum LolbenchSource {
    #[default]
    Skip,
    Existing,
    Clone,
    Release,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct JenkinsAgentConfig {
    /// Worker only: Jenkins controller base URL.
    pub controller_url: Option<String>,
    pub name: Option<String>,
    pub labels: Vec<String>,
    pub remote_fs: Option<PathBuf>,
    pub agent_jar: Option<PathBuf>,
    /// Logical CPU cores recorded at prepare time.
    pub cpu_cores: u32,
    /// Jenkins user for REST API (plaintext for now; encrypt later).
    pub api_user: Option<String>,
    /// Jenkins API token (plaintext for now; encrypt later).
    pub api_token: Option<String>,
}

/// Non-secret defaults for the `lolbench_one_task` Pipeline job (controller).
///
/// Secrets are never stored here — see `docs/secrets.md` and pending credentials file.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JenkinsJobConfig {
    pub default_harness: String,
    pub default_task: String,
    pub default_model: String,
}

impl Default for JenkinsJobConfig {
    fn default() -> Self {
        Self {
            default_harness: "oracle".into(),
            default_task: "ruff_1".into(),
            default_model: "openrouter/deepseek/deepseek-v4-pro".into(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct ResourcesConfig {
    /// Jenkins Lockable Resources label for CPU capacity.
    pub cpu_cores_label: String,
    /// Minimum free disk (GB) required by prepare for this role (0 = use role default).
    pub disk_min_gb: u64,
}

impl Default for ResourcesConfig {
    fn default() -> Self {
        Self {
            cpu_cores_label: "CPU_CORES".into(),
            disk_min_gb: 0,
        }
    }
}

impl Default for MacK3dConfig {
    fn default() -> Self {
        Self {
            role: NodeRole::Standalone,
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
            lolbench: LolbenchConfig {
                path: None,
                source: LolbenchSource::Skip,
                git_url: "https://github.com/MichaelLing83/LoLBench-Preview.git".into(),
            },
            jenkins_agent: JenkinsAgentConfig {
                controller_url: None,
                name: None,
                labels: vec![
                    "macos".into(),
                    "docker".into(),
                    "lolbench".into(),
                ],
                remote_fs: None,
                agent_jar: None,
                cpu_cores: 0,
                api_user: None,
                api_token: None,
            },
            jenkins_job: JenkinsJobConfig::default(),
            resources: ResourcesConfig::default(),
        }
    }
}

impl StorageConfig {
    pub fn resolve(&self, sub: &str, explicit: &Option<PathBuf>) -> Option<PathBuf> {
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

    /// Pending CI secrets from prepare (0600 YAML); consumed by `config` into Jenkins Credentials.
    pub fn pending_credentials_path() -> PathBuf {
        Self::config_dir().join("credentials.pending.yaml")
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
        if self.jenkins.enabled {
            self.role = NodeRole::Controller;
        }
    }

    /// Minimum free disk in GB for prepare validation.
    pub fn disk_min_gb(&self) -> u64 {
        if self.resources.disk_min_gb > 0 {
            return self.resources.disk_min_gb;
        }
        match self.role {
            NodeRole::Standalone => 40,
            NodeRole::Controller => 60,
            NodeRole::Worker => 100,
        }
    }
}

impl DependenciesConfig {
    pub fn entries(&self) -> [(&str, &DependencyEntry); 6] {
        [
            ("docker", &self.docker),
            ("k3d", &self.k3d),
            ("kubectl", &self.kubectl),
            ("helm", &self.helm),
            ("harbor", &self.harbor),
            ("java", &self.java),
        ]
    }
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}
