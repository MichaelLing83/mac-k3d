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
    /// Host OS recorded by prepare (`macos` | `linux`); informational.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub platform: Option<String>,
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub base_dir: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub docker: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub k3d: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub jenkins: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
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
    /// Workers clone benchmarks and iCode with it.
    pub git: DependencyEntry,
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
            git: DependencyEntry {
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub binary: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
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
    /// Shared secret for "Trigger builds remotely" on every eval job.
    /// Local to this controller's config.yaml; `export` omits it.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub remote_trigger_token: String,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
pub struct DockerConfig {
    pub startup_timeout_secs: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
#[serde(default)]
/// Deprecated: the pipeline's tasks phase clones LoLBench itself at a pinned
/// commit. Old YAML still loads; nothing reads these fields any more.
pub struct LolbenchConfig {
    /// Path to a LoLBench-Preview checkout.
    #[serde(default, skip_serializing_if = "Option::is_none")]
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
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub controller_url: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub name: Option<String>,
    pub labels: Vec<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub remote_fs: Option<PathBuf>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub agent_jar: Option<PathBuf>,
    /// Logical CPU cores recorded at prepare time. This is the worker's CPU
    /// upper limit (cloud 16, local 4).
    pub cpu_cores: u32,
    /// CPUs one executor stands for. Executors = cpu_cores / this. 0 or omitted
    /// means 2, which is what a DeepSWE task declares.
    #[serde(default = "default_cpus_per_executor")]
    pub cpus_per_executor: u32,
    /// Jenkins user for REST API (plaintext for now; encrypt later). A worker's
    /// YAML always carries the key; `''` means "not filled in yet".
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "blank_as_none"
    )]
    pub api_user: Option<String>,
    /// Jenkins API token (plaintext for now; encrypt later). Same `''` rule.
    #[serde(
        default,
        skip_serializing_if = "Option::is_none",
        deserialize_with = "blank_as_none"
    )]
    pub api_token: Option<String>,
}

impl JenkinsAgentConfig {
    pub fn api_user(&self) -> Option<&str> {
        filled(self.api_user.as_deref())
    }

    pub fn api_token(&self) -> Option<&str> {
        filled(self.api_token.as_deref()).filter(|t| *t != "REPLACE_ME")
    }

    /// Both REST credentials, or `None` when either is missing or still blank.
    pub fn api_credentials(&self) -> Option<(&str, &str)> {
        Some((self.api_user()?, self.api_token()?))
    }
}

fn filled(value: Option<&str>) -> Option<&str> {
    value.map(str::trim).filter(|s| !s.is_empty())
}

fn blank_as_none<'de, D>(deserializer: D) -> std::result::Result<Option<String>, D::Error>
where
    D: serde::Deserializer<'de>,
{
    let value: Option<String> = Option::deserialize(deserializer)?;
    Ok(value.filter(|s| !s.trim().is_empty()))
}

/// Non-secret defaults for the `lolbench_one_task` Pipeline job (controller).
///
/// Secrets are never stored here — see `docs/secrets.md` and pending credentials file.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(default)]
pub struct JenkinsJobConfig {
    pub default_task: String,
    /// `release` (`*-full-*` drop) or `git` (clone URL + ref). `binary` is an alias of `release`.
    pub default_eval_mode: String,
    /// GitCode `icode-<os>-<arch>-full-vX.Y.Z.tar.gz` URL or path (or a stub `icode` binary).
    pub default_icode_release: String,
    /// iCode git URL for `ICODE_MODE=git` (private GitCode clone uses `gitcode-pat`).
    pub default_icode_git_url: String,
    /// Git ref for git mode (branch, tag, commit, or PR number). Empty becomes `main`.
    pub default_icode_git_ref: String,
    pub default_icode_args: String,
    /// Deprecated; ignored if `default_icode_release` is set.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_binary_target: String,
    /// Deprecated honeyc-era field; ignored by the job generator.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_honeyc_bin: String,
    /// Deprecated honeyc-era field; ignored by the job generator.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_honeyc_args: String,
    /// Eval harness id (`mac-k3d set --harness`). Catalog: `icode`.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_harness: String,
    /// Deprecated alias; `default_llm` is the live field.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_model: String,
    /// Eval LLM family (`mac-k3d set --llm`). Catalog: `deepseek`.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_llm: String,
    /// DeepSeek Chat Completions id (`mac-k3d set --model`). Catalog: `deepseek-flash` (default), `deepseek-v4-pro`.
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_deepseek_model: String,
    /// Which Jenkins job receives TASK / N_TASKS / TASKS defaults (`deepswe` | `lolbench` | `swebenchpro`).
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub default_benchmark: String,
    /// First N sorted questions when `default_task` and `default_tasks` are empty.
    #[serde(default = "default_n_tasks_one")]
    pub default_n_tasks: u32,
    /// Harbor attempts per question (`N_ROLLOUTS` job param default).
    #[serde(default = "default_n_rollouts_four")]
    pub default_n_rollouts: u32,
    /// Explicit question ids (`mac-k3d set --tasks a,b`). Overrides N and single TASK.
    #[serde(default, skip_serializing_if = "Vec::is_empty")]
    pub default_tasks: Vec<String>,
    /// Git ref kind for job param default: `branch` | `tag` | `commit` | `pr`.
    #[serde(default = "default_icode_git_ref_kind_pr")]
    pub default_icode_git_ref_kind: String,
    /// `user` shows each job's short parameter list; `developer` also shows the
    /// pins, canary and scheduling knobs (`mac-k3d set --ui-profile`).
    #[serde(default = "default_ui_profile")]
    pub ui_profile: String,
    /// Questions per shard. Kept so older YAML still loads; dispatch no longer
    /// uses it. A rollout is one `one_task` build.
    #[serde(default = "default_shard_size")]
    pub default_shard_size: u32,
    /// Builds one dispatcher may have in the Jenkins queue at once. 0 means
    /// 2 x the online executors that can run the suite, counted at dispatch.
    #[serde(default)]
    pub max_in_flight: u32,
    /// How many times an out-of-memory rollout is put back at the tail of the
    /// queue. The rollout then runs at most this many times plus the first try.
    #[serde(default = "default_oom_retries")]
    pub oom_retries: u32,
}

pub const UI_PROFILES: &[&str] = &["user", "developer"];

fn default_ui_profile() -> String {
    "user".into()
}

fn default_shard_size() -> u32 {
    2
}

fn default_cpus_per_executor() -> u32 {
    2
}

fn default_oom_retries() -> u32 {
    2
}

/// CPUs one executor stands for. An omitted or zero value is the DeepSWE size.
pub fn cpus_per_executor(value: u32) -> u32 {
    if value == 0 { 2 } else { value }
}

fn default_n_tasks_one() -> u32 {
    1
}

fn default_n_rollouts_four() -> u32 {
    4
}

fn default_icode_git_ref_kind_pr() -> String {
    "pr".into()
}

impl Default for JenkinsJobConfig {
    fn default() -> Self {
        Self {
            default_task: String::new(),
            default_eval_mode: "git".into(),
            default_icode_release: String::new(),
            default_icode_git_url: "https://gitcode.com/michaelling/jiuwenicode".into(),
            default_icode_git_ref: "2".into(),
            default_icode_args: String::new(),
            default_binary_target: String::new(),
            default_honeyc_bin: String::new(),
            default_honeyc_args: String::new(),
            default_harness: String::new(),
            default_model: String::new(),
            default_llm: String::new(),
            default_deepseek_model: String::new(),
            default_benchmark: String::new(),
            default_n_tasks: 1,
            default_n_rollouts: 4,
            default_tasks: Vec::new(),
            default_icode_git_ref_kind: "pr".into(),
            ui_profile: default_ui_profile(),
            default_shard_size: default_shard_size(),
            max_in_flight: 0,
            oom_retries: default_oom_retries(),
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
            platform: None,
            cluster: ClusterConfig {
                name: "mac-k3d".into(),
                agents: 0,
                ports: default_ports(),
            },
            jenkins: JenkinsConfig {
                enabled: false,
                namespace: "jenkins".into(),
                release_name: "jenkins".into(),
                host_port: 17070,
                remote_trigger_token: String::new(),
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
                labels: crate::platform::default_agent_labels(),
                remote_fs: None,
                agent_jar: None,
                cpu_cores: 0,
                cpus_per_executor: default_cpus_per_executor(),
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

    pub fn default_worker_path() -> PathBuf {
        Self::config_dir().join("worker.yaml")
    }

    /// `-c` wins. Else `config.yaml` if it exists, else `worker.yaml` (clean worker-only machines).
    pub fn resolve_config_path(explicit: Option<&Path>) -> PathBuf {
        resolve_config_path_in(&Self::config_dir(), explicit)
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
        let path = Self::resolve_config_path(path);

        if !path.exists() {
            return Ok(Self::default());
        }

        Self::load_file(&path)
    }

    /// `load` for commands that act on a configured host. An explicit `-c` file
    /// must exist: the defaults would point them at a k3d cluster that host never had.
    pub fn load_existing(path: Option<&Path>) -> Result<Self> {
        match path {
            Some(p) if !p.exists() => Err(Error::Config(format!(
                "no config at {c}; run `mac-k3d setup -c {c}` first",
                c = p.display()
            ))),
            _ => Self::load(path),
        }
    }

    /// Load a specific YAML path. Errors if the file is missing or invalid.
    pub fn load_file(path: &Path) -> Result<Self> {
        if !path.exists() {
            return Err(Error::Config(format!(
                "config not found: {}",
                path.display()
            )));
        }

        let contents = std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("failed to read {}: {e}", path.display())))?;

        serde_yaml::from_str(&contents)
            .map_err(|e| Error::Config(format!("failed to parse {}: {e}", path.display())))
    }

    /// Default dest for `import` when `-c` is omitted: worker.yaml vs config.yaml.
    pub fn default_path_for_role(role: NodeRole) -> PathBuf {
        match role {
            NodeRole::Worker => Self::default_worker_path(),
            NodeRole::Standalone | NodeRole::Controller => Self::default_config_path(),
        }
    }

    /// Lab template: keep eval/job/role fields; drop secrets and host-local paths.
    ///
    /// Never copies `credentials.pending.yaml`.
    pub fn for_export(&self) -> Self {
        let mut out = self.clone();
        out.platform = None;
        out.storage = StorageConfig::default();
        out.lolbench.path = None;
        strip_dependency_host_paths(&mut out.dependencies);
        out.jenkins_agent.remote_fs = None;
        out.jenkins_agent.agent_jar = None;
        out.jenkins_agent.cpu_cores = 0;
        out.jenkins_agent.api_token = None;
        out.jenkins.remote_trigger_token.clear();
        out
    }

    /// Record the OS of the machine that is importing this file.
    pub fn apply_host_platform(&mut self) {
        self.platform = Some(crate::platform::host_os().as_str().to_string());
    }

    pub fn save(&self, path: Option<&Path>) -> Result<()> {
        let path = path
            .map(PathBuf::from)
            .unwrap_or_else(Self::default_config_path);
        write_yaml(&path, &self.with_api_slots(), None)
    }

    /// Write a sanitized export with a header that warns the file has no secrets.
    pub fn save_sanitized_export(&self, path: &Path) -> Result<()> {
        write_yaml(path, &self.for_export().with_api_slots(), Some(EXPORT_HEADER))
    }

    /// A worker's YAML always shows `api_user` / `api_token`, blank when not
    /// filled in, so the user can see where to paste them later.
    fn with_api_slots(&self) -> Self {
        let mut out = self.clone();
        if matches!(out.role, NodeRole::Worker) {
            out.jenkins_agent.api_user.get_or_insert_with(String::new);
            out.jenkins_agent.api_token.get_or_insert_with(String::new);
        }
        out
    }

    pub fn apply_jenkins_mode(&mut self, mode: JenkinsMode) {
        self.jenkins.enabled = matches!(mode, JenkinsMode::InCluster);
        if self.jenkins.enabled {
            self.role = NodeRole::Controller;
        }
    }

    /// Minimum free disk in GB for prepare validation. Hosts that run builds
    /// use WORKER_MIN_DISK_GB from toolchain.env, as the env phase does.
    pub fn disk_min_gb(&self) -> u64 {
        if self.resources.disk_min_gb > 0 {
            return self.resources.disk_min_gb;
        }
        match self.role {
            NodeRole::Standalone | NodeRole::Worker => {
                crate::prepare::toolchain::worker_min_disk_gb()
            }
            NodeRole::Controller => 60,
        }
    }
}

impl DependenciesConfig {
    pub fn entries(&self) -> [(&str, &DependencyEntry); 7] {
        [
            ("docker", &self.docker),
            ("k3d", &self.k3d),
            ("kubectl", &self.kubectl),
            ("helm", &self.helm),
            ("harbor", &self.harbor),
            ("java", &self.java),
            ("git", &self.git),
        ]
    }

    pub fn entry_mut(&mut self, name: &str) -> Option<&mut DependencyEntry> {
        Some(match name {
            "docker" => &mut self.docker,
            "k3d" => &mut self.k3d,
            "kubectl" => &mut self.kubectl,
            "helm" => &mut self.helm,
            "harbor" => &mut self.harbor,
            "java" => &mut self.java,
            "git" => &mut self.git,
            _ => return None,
        })
    }
}

const EXPORT_HEADER: &str = "\
# mac-k3d sanitized export — no API tokens, CI keys, or host-local paths.
# Never copy credentials.pending.yaml. Edit jenkins_job / labels / controller_url, then:
#   mac-k3d import this-file.yaml
";

fn strip_dependency_host_paths(deps: &mut DependenciesConfig) {
    for entry in [
        &mut deps.docker,
        &mut deps.k3d,
        &mut deps.kubectl,
        &mut deps.helm,
        &mut deps.harbor,
        &mut deps.java,
        &mut deps.git,
    ] {
        entry.binary = None;
        entry.app = None;
    }
}

fn write_yaml(path: &Path, config: &MacK3dConfig, header: Option<&str>) -> Result<()> {
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Config(format!("failed to create config dir: {e}")))?;
        }
    }

    let body = serde_yaml::to_string(config)
        .map_err(|e| Error::Config(format!("failed to serialize config: {e}")))?;
    let contents = match header {
        Some(h) => format!("{h}{body}"),
        None => body,
    };

    // config.yaml and worker.yaml hold jenkins_agent.api_token.
    write_private(path, contents.as_bytes())
}

/// Write `bytes` to `path` readable by this user only (0600). An existing file
/// is tightened before it is rewritten.
pub fn write_private(path: &Path, bytes: &[u8]) -> Result<()> {
    use std::io::Write;

    make_private(path)?;
    let mut opts = std::fs::OpenOptions::new();
    opts.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        opts.mode(0o600);
    }
    let mut file = opts
        .open(path)
        .map_err(|e| Error::Config(format!("failed to write {}: {e}", path.display())))?;
    file.write_all(bytes)
        .map_err(|e| Error::Config(format!("failed to write {}: {e}", path.display())))
}

/// Set an existing file to 0600; a missing file is left alone.
pub fn make_private(path: &Path) -> Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        match std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)) {
            Err(e) if e.kind() != std::io::ErrorKind::NotFound => {
                return Err(Error::Config(format!(
                    "failed to chmod 600 {}: {e}",
                    path.display()
                )));
            }
            _ => {}
        }
    }
    #[cfg(not(unix))]
    let _ = path;
    Ok(())
}

fn home_dir() -> Option<PathBuf> {
    std::env::var_os("HOME").map(PathBuf::from)
}

fn resolve_config_path_in(dir: &Path, explicit: Option<&Path>) -> PathBuf {
    if let Some(p) = explicit {
        return p.to_path_buf();
    }
    let primary = dir.join("config.yaml");
    if primary.exists() {
        return primary;
    }
    let worker = dir.join("worker.yaml");
    if worker.exists() {
        return worker;
    }
    primary
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolve_prefers_config_yaml_when_present() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-cfg-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("config.yaml"), "role: controller\n").unwrap();
        std::fs::write(dir.join("worker.yaml"), "role: worker\n").unwrap();
        assert_eq!(resolve_config_path_in(&dir, None), dir.join("config.yaml"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn resolve_falls_back_to_worker_yaml() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-cfg-worker-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("worker.yaml"), "role: worker\n").unwrap();
        assert_eq!(resolve_config_path_in(&dir, None), dir.join("worker.yaml"));
        let _ = std::fs::remove_dir_all(&dir);
    }

    fn sample_worker() -> MacK3dConfig {
        let mut cfg = MacK3dConfig::default();
        cfg.role = NodeRole::Worker;
        cfg.platform = Some("linux".into());
        cfg.storage.base_dir = Some(PathBuf::from("/home/src/mac-k3d"));
        cfg.dependencies.docker.binary = Some(PathBuf::from("/usr/bin/docker"));
        cfg.dependencies.docker.app = Some(PathBuf::from("/Applications/Docker.app"));
        cfg.lolbench.path = Some(PathBuf::from("/home/src/lolbench"));
        cfg.lolbench.source = LolbenchSource::Existing;
        cfg.jenkins_agent.controller_url = Some("http://43.107.42.252:17070".into());
        cfg.jenkins_agent.name = Some("linux-eval-1".into());
        cfg.jenkins_agent.labels = vec!["linux".into(), "docker".into()];
        cfg.jenkins_agent.remote_fs = Some(PathBuf::from("/home/src/jenkins-agent"));
        cfg.jenkins_agent.agent_jar = Some(PathBuf::from("/tmp/agent.jar"));
        cfg.jenkins_agent.cpu_cores = 8;
        cfg.jenkins_agent.api_user = Some("admin".into());
        cfg.jenkins_agent.api_token = Some("test-jenkins-token".into());
        cfg.jenkins.remote_trigger_token = "remote-trigger-secret".into();
        cfg.jenkins_job.default_task = "ruff_1".into();
        cfg.jenkins_job.default_eval_mode = "binary".into();
        cfg
    }

    #[test]
    fn for_export_strips_token_and_host_paths_keeps_job_defaults() {
        let exported = sample_worker().for_export();
        assert_eq!(exported.role, NodeRole::Worker);
        assert!(exported.platform.is_none());
        assert!(exported.storage.base_dir.is_none());
        assert!(exported.dependencies.docker.binary.is_none());
        assert!(exported.dependencies.docker.app.is_none());
        assert!(exported.lolbench.path.is_none());
        assert_eq!(exported.lolbench.source, LolbenchSource::Existing);
        assert_eq!(
            exported.jenkins_agent.controller_url.as_deref(),
            Some("http://43.107.42.252:17070")
        );
        assert_eq!(exported.jenkins_agent.name.as_deref(), Some("linux-eval-1"));
        assert_eq!(exported.jenkins_agent.api_user.as_deref(), Some("admin"));
        assert!(exported.jenkins_agent.api_token.is_none());
        assert!(exported.jenkins.remote_trigger_token.is_empty());
        assert!(exported.jenkins_agent.remote_fs.is_none());
        assert!(exported.jenkins_agent.agent_jar.is_none());
        assert_eq!(exported.jenkins_agent.cpu_cores, 0);
        assert_eq!(exported.jenkins_job.default_task, "ruff_1");
        assert_eq!(exported.jenkins_job.default_eval_mode, "binary");
    }

    #[test]
    fn sanitized_export_yaml_omits_token() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-export-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("worker.yaml");
        sample_worker().save_sanitized_export(&path).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("sanitized export"));
        assert!(!text.contains("test-jenkins-token"));
        assert!(!text.contains("remote-trigger-secret"));
        assert!(text.contains("api_token: ''"), "{text}");
        assert!(text.contains("default_task: ruff_1"));
        let loaded = MacK3dConfig::load_file(&path).unwrap();
        assert!(loaded.jenkins_agent.api_token.is_none());
        assert_eq!(loaded.jenkins_agent.api_user(), Some("admin"));
        assert!(loaded.jenkins_agent.api_credentials().is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn worker_yaml_shows_blank_api_keys_when_skipped() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-slots-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let mut worker = sample_worker();
        worker.jenkins_agent.api_user = None;
        worker.jenkins_agent.api_token = None;
        let path = dir.join("worker.yaml");
        worker.save(Some(&path)).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("api_user: ''"), "{text}");
        assert!(text.contains("api_token: ''"), "{text}");
        let loaded = MacK3dConfig::load_file(&path).unwrap();
        assert!(loaded.jenkins_agent.api_user.is_none());
        assert!(loaded.jenkins_agent.api_credentials().is_none());

        let filled = text
            .replace("api_user: ''", "api_user: admin")
            .replace("api_token: ''", "api_token: abc");
        std::fs::write(&path, filled).unwrap();
        let loaded = MacK3dConfig::load_file(&path).unwrap();
        assert_eq!(loaded.jenkins_agent.api_credentials(), Some(("admin", "abc")));

        let mut controller = MacK3dConfig::default();
        controller.role = NodeRole::Controller;
        let path = dir.join("config.yaml");
        controller.save(Some(&path)).unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(!text.contains("api_user"), "{text}");
        assert!(!text.contains("api_token"), "{text}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn config_files_are_private_even_when_they_existed() {
        use std::os::unix::fs::PermissionsExt;
        let mode = |p: &Path| std::fs::metadata(p).unwrap().permissions().mode() & 0o777;
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("worker.yaml");
        sample_worker().save(Some(&path)).unwrap();
        assert_eq!(mode(&path), 0o600);

        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644)).unwrap();
        sample_worker().save(Some(&path)).unwrap();
        assert_eq!(mode(&path), 0o600, "a rewrite tightens an old 0644 file");

        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o664)).unwrap();
        make_private(&path).unwrap();
        assert_eq!(mode(&path), 0o600, "`config` tightens the file without rewriting it");
        make_private(&dir.path().join("missing.yaml")).unwrap();

        let export = dir.path().join("export.yaml");
        sample_worker().save_sanitized_export(&export).unwrap();
        assert_eq!(mode(&export), 0o600);
    }

    #[test]
    fn placeholder_token_is_not_a_credential() {
        let mut agent = sample_worker().jenkins_agent;
        agent.api_token = Some("REPLACE_ME".into());
        assert!(agent.api_credentials().is_none());
        agent.api_token = Some("  ".into());
        assert!(agent.api_token().is_none());
    }

    #[test]
    fn jenkins_job_eval_fields_roundtrip() {
        let mut cfg = MacK3dConfig::default();
        cfg.jenkins_job.default_harness = "icode".into();
        cfg.jenkins_job.default_llm = "deepseek".into();
        cfg.jenkins_job.default_deepseek_model = "deepseek-flash".into();
        cfg.jenkins_job.default_benchmark = "deepswe".into();
        cfg.jenkins_job.default_n_tasks = 2;
        cfg.jenkins_job.default_task.clear();
        cfg.jenkins_job.default_tasks =
            vec!["abs-module-cache-flags".into(), "abs-stepped-slices".into()];
        let yaml = serde_yaml::to_string(&cfg).unwrap();
        assert!(yaml.contains("default_harness: icode"));
        assert!(yaml.contains("default_llm: deepseek"));
        assert!(yaml.contains("default_deepseek_model: deepseek-flash"));
        assert!(yaml.contains("default_benchmark: deepswe"));
        assert!(yaml.contains("default_n_tasks: 2"));
        assert!(yaml.contains("abs-module-cache-flags"));
        let loaded: MacK3dConfig = serde_yaml::from_str(&yaml).unwrap();
        assert_eq!(loaded.jenkins_job.default_harness, "icode");
        assert_eq!(loaded.jenkins_job.default_llm, "deepseek");
        assert_eq!(loaded.jenkins_job.default_deepseek_model, "deepseek-flash");
        assert_eq!(loaded.jenkins_job.default_benchmark, "deepswe");
        assert_eq!(loaded.jenkins_job.default_n_tasks, 2);
        assert_eq!(
            loaded.jenkins_job.default_tasks,
            vec!["abs-module-cache-flags", "abs-stepped-slices"]
        );
        assert!(loaded.jenkins_job.default_task.is_empty());

        let omitted: MacK3dConfig = serde_yaml::from_str("role: controller\n").unwrap();
        assert_eq!(omitted.jenkins_job.default_n_tasks, 1);
        assert!(omitted.jenkins_job.default_tasks.is_empty());

        cfg.jenkins_job.default_task = "abs-stepped-slices".into();
        let exported = cfg.for_export();
        assert_eq!(exported.jenkins_job.default_harness, "icode");
        assert_eq!(
            exported.jenkins_job.default_deepseek_model,
            "deepseek-flash"
        );
        assert_eq!(exported.jenkins_job.default_benchmark, "deepswe");
        assert_eq!(exported.jenkins_job.default_tasks.len(), 2);
    }

    #[test]
    fn ui_profile_defaults_to_user_and_roundtrips() {
        let omitted: MacK3dConfig = serde_yaml::from_str("role: controller\n").unwrap();
        assert_eq!(omitted.jenkins_job.ui_profile, "user");
        assert_eq!(omitted.jenkins_job.default_shard_size, 2);

        let mut cfg = MacK3dConfig::default();
        cfg.jenkins_job.ui_profile = "developer".into();
        cfg.jenkins_job.default_shard_size = 3;
        let yaml = serde_yaml::to_string(&cfg).unwrap();
        assert!(yaml.contains("ui_profile: developer"));
        assert!(yaml.contains("default_shard_size: 3"));
        let loaded: MacK3dConfig = serde_yaml::from_str(&yaml).unwrap();
        assert_eq!(loaded.jenkins_job.ui_profile, "developer");
        assert_eq!(loaded.jenkins_job.default_shard_size, 3);
    }

    #[test]
    fn default_path_for_role_splits_worker_and_controller() {
        assert_eq!(
            MacK3dConfig::default_path_for_role(NodeRole::Worker),
            MacK3dConfig::default_worker_path()
        );
        assert_eq!(
            MacK3dConfig::default_path_for_role(NodeRole::Controller),
            MacK3dConfig::default_config_path()
        );
        assert_eq!(
            MacK3dConfig::default_path_for_role(NodeRole::Standalone),
            MacK3dConfig::default_config_path()
        );
    }
}
