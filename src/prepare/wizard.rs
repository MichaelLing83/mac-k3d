use std::path::PathBuf;

use dialoguer::{theme::ColorfulTheme, Confirm, Input, Password, Select};

use crate::config::{
    ClusterConfig, DependenciesConfig, DependencyEntry, DependencySource, JenkinsAgentConfig,
    LolbenchConfig, LolbenchSource, MacK3dConfig, NodeRole, StorageConfig,
};
use crate::error::{Error, Result};
use crate::prepare::discovery::{self, DiscoveredDeps, DiscoveredTool};
use crate::prepare::install::{self, entry_from_path};
use crate::prepare::{jenkins_agent, lolbench, resources};
use crate::prepare::volumes::VolumeCandidate;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum MacRole {
    Standalone,
    Controller,
    Worker,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExistingConfigAction {
    RerunWizard,
    ValidateOnly,
    Cancel,
}

/// What to do when config already exists on an interactive run.
pub fn prompt_existing_config() -> Result<ExistingConfigAction> {
    let options = [
        "Re-run wizard (overwrite config)",
        "Validate existing config only",
        "Cancel",
    ];
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("Config already exists")
        .items(&options)
        .default(1)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    Ok(match selection {
        0 => ExistingConfigAction::RerunWizard,
        1 => ExistingConfigAction::ValidateOnly,
        _ => ExistingConfigAction::Cancel,
    })
}

/// Interactive wizard entry point.
pub fn run(volumes: Vec<VolumeCandidate>, discovered: DiscoveredDeps) -> Result<MacK3dConfig> {
    println!("\nmac-k3d prepare — interactive setup\n");

    let base_dir = prompt_storage_base(&volumes)?;
    let role = prompt_role()?;
    let node_role = match role {
        MacRole::Standalone => NodeRole::Standalone,
        MacRole::Controller => NodeRole::Controller,
        MacRole::Worker => NodeRole::Worker,
    };
    let jenkins_enabled = matches!(role, MacRole::Controller);
    let wants_lolbench = !matches!(role, MacRole::Standalone)
        || Confirm::with_theme(&ColorfulTheme::default())
            .with_prompt("Set up LoLBench / Harbor on this Mac?")
            .default(false)
            .interact()
            .map_err(|_| Error::Cancelled)?;

    let docker = prompt_dependency("Docker Desktop", discovered.docker.as_ref(), true, true)?;
    let k3d = prompt_dependency("k3d", discovered.k3d.as_ref(), true, false)?;
    let kubectl = prompt_dependency("kubectl", discovered.kubectl.as_ref(), true, false)?;
    let helm = if jenkins_enabled {
        prompt_dependency("helm", discovered.helm.as_ref(), true, false)?
    } else {
        DependencyEntry {
            source: DependencySource::Skip,
            binary: discovered.helm.as_ref().map(|t| t.binary.clone()),
            app: None,
        }
    };
    let harbor = if wants_lolbench {
        prompt_harbor(discovered.harbor.as_ref(), discovered.uv.is_some(), discovered.pipx.is_some())?
    } else {
        DependencyEntry {
            source: DependencySource::Skip,
            binary: discovered.harbor.as_ref().map(|t| t.binary.clone()),
            app: None,
        }
    };
    let java = if matches!(role, MacRole::Worker) {
        prompt_dependency("java", discovered.java.as_ref(), true, false)?
    } else {
        DependencyEntry {
            source: DependencySource::Skip,
            binary: discovered.java.as_ref().map(|t| t.binary.clone()),
            app: None,
        }
    };

    let lolbench_cfg = if wants_lolbench {
        prompt_lolbench(&base_dir)?
    } else {
        LolbenchConfig::default()
    };

    let cpu_cores = resources::logical_cpu_cores();
    let mut jenkins_agent_cfg = JenkinsAgentConfig {
        cpu_cores,
        labels: vec!["macos".into(), "docker".into(), "lolbench".into()],
        ..JenkinsAgentConfig::default()
    };
    let mut worker_creds: Option<WorkerAgentPrompt> = None;

    if matches!(role, MacRole::Worker) {
        let prompted = prompt_worker_agent(&base_dir, cpu_cores)?;
        jenkins_agent_cfg = prompted.config.clone();
        worker_creds = Some(prompted);
    }

    let (cluster_name, agents, jenkins_port) = prompt_cluster_settings(role)?;

    let storage = StorageConfig {
        base_dir: Some(base_dir.clone()),
        docker: Some(base_dir.join("docker")),
        k3d: Some(base_dir.join("k3d")),
        jenkins: Some(base_dir.join("jenkins")),
        downloads: Some(base_dir.join("downloads")),
    };

    let mut config = MacK3dConfig {
        role: node_role,
        cluster: ClusterConfig {
            name: cluster_name,
            agents,
            ports: default_ports(),
        },
        jenkins: crate::config::JenkinsConfig {
            enabled: jenkins_enabled,
            namespace: "jenkins".into(),
            release_name: "jenkins".into(),
            host_port: jenkins_port,
        },
        storage,
        dependencies: DependenciesConfig {
            docker,
            k3d,
            kubectl,
            helm,
            harbor,
            java,
        },
        lolbench: lolbench_cfg,
        jenkins_agent: jenkins_agent_cfg,
        ..MacK3dConfig::default()
    };

    print_summary(&config, role);

    if !Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt("Write configuration and apply setup?")
        .default(true)
        .interact()
        .map_err(|_| Error::Cancelled)?
    {
        return Err(Error::Cancelled);
    }

    ensure_storage_dirs(&config)?;
    install_pending_dependencies(&mut config)?;
    apply_lolbench_checkout(&mut config)?;
    apply_worker_agent(&mut config, worker_creds.as_ref())?;

    if matches!(role, MacRole::Controller) {
        println!(
            "\nController: after `mac-k3d start`, ensure Lockable Resources label '{}' exists.",
            config.resources.cpu_cores_label
        );
        let _ = resources::ensure_cpu_cores_label_on_controller(
            &format!("http://localhost:{}", config.jenkins.host_port),
            &config.resources.cpu_cores_label,
        );
        if let Some(docker_dir) = config.storage.docker_dir() {
            println!(
                "Recommended Docker data path: {} (move manually in Docker Desktop if desired).",
                docker_dir.display()
            );
        }
    }

    let disk_path = config
        .storage
        .base_dir
        .as_deref()
        .unwrap_or(std::path::Path::new("/"));
    resources::ensure_disk_min(disk_path, config.disk_min_gb())?;

    println!("\nSetup complete. Next: mac-k3d start");
    Ok(config)
}

fn prompt_storage_base(volumes: &[VolumeCandidate]) -> Result<PathBuf> {
    if volumes.is_empty() {
        return prompt_custom_path("Storage base directory");
    }

    let mut items: Vec<String> = volumes
        .iter()
        .enumerate()
        .map(|(i, v)| {
            let rec = if i == 0 { " (recommended)" } else { "" };
            format!("{}{rec}", v.display_label())
        })
        .collect();
    items.push("Enter custom path".to_string());

    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("Select base directory for large installs and caches")
        .items(&items)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    if selection < volumes.len() {
        Ok(volumes[selection].suggested_base.clone())
    } else {
        prompt_custom_path("Storage base directory")
    }
}

fn prompt_custom_path(label: &str) -> Result<PathBuf> {
    let path: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt(label)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;
    let path = PathBuf::from(path.trim());
    if path.as_os_str().is_empty() {
        return Err(Error::Validation("path cannot be empty".into()));
    }
    Ok(path)
}

fn prompt_role() -> Result<MacRole> {
    let options = [
        "Local development only (no Jenkins)",
        "CI controller (Jenkins in k3d)",
        "CI worker (Jenkins agent only)",
    ];
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("What is this Mac's role?")
        .items(&options)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    Ok(match selection {
        1 => MacRole::Controller,
        2 => MacRole::Worker,
        _ => MacRole::Standalone,
    })
}

fn prompt_dependency(
    label: &str,
    discovered: Option<&DiscoveredTool>,
    required: bool,
    is_docker: bool,
) -> Result<DependencyEntry> {
    if let Some(tool) = discovered {
        println!("\n{label}: found");
        println!("  {}", tool.describe());

        let mut options = vec![
            "Use this installation (recommended)".to_string(),
            "Specify a different binary path".to_string(),
        ];
        if is_docker {
            options.push("Install via Homebrew (not recommended if already installed)".to_string());
        } else {
            options.push("Install via Homebrew".to_string());
        }
        if !required {
            options.push("Skip".to_string());
        }

        let selection = Select::with_theme(&ColorfulTheme::default())
            .with_prompt(format!("{label} action"))
            .items(&options)
            .default(0)
            .interact()
            .map_err(|_| Error::Cancelled)?;

        return match selection {
            0 => Ok(install::tool_to_entry(tool)),
            1 => {
                let default = tool.binary.display().to_string();
                let path: String = Input::with_theme(&ColorfulTheme::default())
                    .with_prompt("Binary path")
                    .default(default)
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?;
                let app = if is_docker {
                    Some(PathBuf::from("/Applications/Docker.app"))
                } else {
                    None
                };
                entry_from_path(PathBuf::from(path.trim()), app)
            }
            2 => Ok(DependencyEntry {
                source: DependencySource::Install,
                binary: None,
                app: if is_docker {
                    Some(PathBuf::from("/Applications/Docker.app"))
                } else {
                    None
                },
            }),
            _ if !required => Ok(DependencyEntry {
                source: DependencySource::Skip,
                binary: None,
                app: None,
            }),
            _ => Err(Error::Cancelled),
        };
    }

    println!("\n{label}: not found");

    let mut options = vec![
        "Install via Homebrew".to_string(),
        "Specify path to existing binary".to_string(),
    ];
    if !required {
        options.push("Skip".to_string());
    }

    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt(format!("{label} action"))
        .items(&options)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    match selection {
        0 => Ok(DependencyEntry {
            source: DependencySource::Install,
            binary: None,
            app: if is_docker {
                Some(PathBuf::from("/Applications/Docker.app"))
            } else {
                None
            },
        }),
        1 => {
            let path: String = Input::with_theme(&ColorfulTheme::default())
                .with_prompt("Binary path")
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
            let app = if is_docker {
                Some(PathBuf::from("/Applications/Docker.app"))
            } else {
                None
            };
            entry_from_path(PathBuf::from(path.trim()), app)
        }
        _ if !required => Ok(DependencyEntry {
            source: DependencySource::Skip,
            binary: None,
            app: None,
        }),
        _ => Err(Error::Validation(format!("{label} is required"))),
    }
}

fn prompt_harbor(discovered: Option<&DiscoveredTool>, has_uv: bool, has_pipx: bool) -> Result<DependencyEntry> {
    if let Some(tool) = discovered {
        println!("\nharbor: found");
        println!("  {}", tool.describe());
        let options = [
            "Use this installation (recommended)",
            "Specify a different binary path",
            "Reinstall via uv / pipx",
            "Skip",
        ];
        let selection = Select::with_theme(&ColorfulTheme::default())
            .with_prompt("harbor action")
            .items(&options)
            .default(0)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        return match selection {
            0 => Ok(install::tool_to_entry(tool)),
            1 => {
                let default = tool.binary.display().to_string();
                let path: String = Input::with_theme(&ColorfulTheme::default())
                    .with_prompt("Binary path")
                    .default(default)
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?;
                entry_from_path(PathBuf::from(path.trim()), None)
            }
            2 => Ok(DependencyEntry {
                source: DependencySource::Install,
                binary: None,
                app: None,
            }),
            _ => Ok(DependencyEntry {
                source: DependencySource::Skip,
                binary: None,
                app: None,
            }),
        };
    }

    println!("\nharbor: not found");
    if has_uv {
        println!("  uv is available (preferred: uv tool install harbor)");
    } else if has_pipx {
        println!("  pipx is available (pipx install harbor)");
    } else {
        println!("  neither uv nor pipx found; install will try Homebrew → uv first");
    }

    let options = [
        "Install via uv / pipx (recommended)",
        "Specify path to existing binary",
        "Skip (LoLBench jobs will not work on this Mac)",
    ];
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("harbor action")
        .items(&options)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    match selection {
        0 => Ok(DependencyEntry {
            source: DependencySource::Install,
            binary: None,
            app: None,
        }),
        1 => {
            let path: String = Input::with_theme(&ColorfulTheme::default())
                .with_prompt("Binary path")
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
            entry_from_path(PathBuf::from(path.trim()), None)
        }
        _ => Ok(DependencyEntry {
            source: DependencySource::Skip,
            binary: None,
            app: None,
        }),
    }
}

fn prompt_lolbench(base_dir: &PathBuf) -> Result<LolbenchConfig> {
    let mut found = discovery::find_lolbench_checkouts();
    let under_base = base_dir.join("lolbench");
    if lolbench::looks_like_lolbench(&under_base) && !found.contains(&under_base) {
        found.insert(0, under_base.clone());
    }

    let default_git = MacK3dConfig::default().lolbench.git_url;
    let mut options: Vec<String> = found
        .iter()
        .enumerate()
        .map(|(i, p)| {
            let rec = if i == 0 { " (recommended)" } else { "" };
            format!("Use {}{rec}", p.display())
        })
        .collect();
    options.push("Enter a different path".into());
    options.push("Clone / download fresh into storage base".into());
    options.push("Skip LoLBench checkout".into());

    let default_idx = if found.is_empty() {
        options.len().saturating_sub(2) // clone option
    } else {
        0
    };

    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("LoLBench checkout")
        .items(&options)
        .default(default_idx)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    if !found.is_empty() && selection < found.len() {
        return Ok(LolbenchConfig {
            path: Some(found[selection].clone()),
            source: LolbenchSource::Existing,
            git_url: default_git,
        });
    }

    let enter_path_idx = found.len();
    let clone_idx = found.len() + 1;

    if selection == enter_path_idx {
        let path: String = Input::with_theme(&ColorfulTheme::default())
            .with_prompt("Path to LoLBench checkout")
            .default(under_base.display().to_string())
            .interact_text()
            .map_err(|_| Error::Cancelled)?;
        let path = PathBuf::from(path.trim());
        if !lolbench::looks_like_lolbench(&path) {
            println!(
                "Warning: {} does not look like LoLBench (missing harbor_tasks/ or scripts/run_task.sh)",
                path.display()
            );
        }
        return Ok(LolbenchConfig {
            path: Some(path),
            source: LolbenchSource::Existing,
            git_url: default_git,
        });
    }

    if selection == clone_idx {
        let git_url: String = Input::with_theme(&ColorfulTheme::default())
            .with_prompt("Git clone URL")
            .default(default_git)
            .interact_text()
            .map_err(|_| Error::Cancelled)?;
        let dest: String = Input::with_theme(&ColorfulTheme::default())
            .with_prompt("Clone destination")
            .default(under_base.display().to_string())
            .interact_text()
            .map_err(|_| Error::Cancelled)?;
        let dest = PathBuf::from(dest.trim());

        lolbench::clone_repo(&git_url, &dest, false)?;
        lolbench::print_release_commands(&dest);

        let method = Select::with_theme(&ColorfulTheme::default())
            .with_prompt("How to obtain LoLBench now?")
            .items(&["Run git clone now", "I'll download/unpack myself (record path only)", "Skip"])
            .default(0)
            .interact()
            .map_err(|_| Error::Cancelled)?;

        return match method {
            0 => Ok(LolbenchConfig {
                path: Some(dest),
                source: LolbenchSource::Clone,
                git_url,
            }),
            1 => Ok(LolbenchConfig {
                path: Some(dest),
                source: LolbenchSource::Release,
                git_url,
            }),
            _ => Ok(LolbenchConfig {
                path: None,
                source: LolbenchSource::Skip,
                git_url,
            }),
        };
    }

    Ok(LolbenchConfig {
        path: None,
        source: LolbenchSource::Skip,
        git_url: default_git,
    })
}

struct WorkerAgentPrompt {
    config: JenkinsAgentConfig,
    api_user: Option<String>,
    api_token: Option<String>,
}

fn prompt_worker_agent(base_dir: &PathBuf, cpu_cores: u32) -> Result<WorkerAgentPrompt> {
    println!("\n--- Jenkins worker agent ---\n");
    println!("Detected logical CPU cores: {cpu_cores} (will register as CPU_CORES capacity)");

    let controller_url: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Jenkins controller URL")
        .default("http://localhost:9080".into())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let api_user: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Jenkins API user (empty to skip auto-registration)")
        .allow_empty(true)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let api_token = if api_user.trim().is_empty() {
        None
    } else {
        let token = Password::with_theme(&ColorfulTheme::default())
            .with_prompt("Jenkins API token")
            .allow_empty_password(true)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        if token.trim().is_empty() {
            None
        } else {
            Some(token)
        }
    };

    let default_name = jenkins_agent::default_agent_name();
    let agent_name: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent name")
        .default(default_name)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let labels_default = "macos docker lolbench";
    let labels_str: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent labels (space-separated)")
        .default(labels_default.into())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;
    let labels: Vec<String> = labels_str
        .split_whitespace()
        .map(str::to_string)
        .collect();

    let remote_fs_default = jenkins_agent::default_remote_fs();
    let remote_fs: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent remote root directory")
        .default(remote_fs_default.display().to_string())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let agent_jar = base_dir
        .join("downloads")
        .join("jenkins-agent")
        .join("agent.jar");

    Ok(WorkerAgentPrompt {
        config: JenkinsAgentConfig {
            controller_url: Some(controller_url.trim().to_string()),
            name: Some(agent_name.trim().to_string()),
            labels,
            remote_fs: Some(PathBuf::from(remote_fs.trim())),
            agent_jar: Some(agent_jar),
            cpu_cores,
        },
        api_user: if api_user.trim().is_empty() {
            None
        } else {
            Some(api_user.trim().to_string())
        },
        api_token,
    })
}

fn apply_lolbench_checkout(config: &mut MacK3dConfig) -> Result<()> {
    match config.lolbench.source {
        LolbenchSource::Clone => {
            let Some(dest) = config.lolbench.path.clone() else {
                return Ok(());
            };
            if lolbench::looks_like_lolbench(&dest) {
                println!("LoLBench already present at {}", dest.display());
                return Ok(());
            }
            lolbench::clone_repo(&config.lolbench.git_url, &dest, true)?;
        }
        LolbenchSource::Release => {
            if let Some(dest) = &config.lolbench.path {
                println!(
                    "LoLBench path recorded at {} — unpack a release there if not already present.",
                    dest.display()
                );
                lolbench::print_release_commands(dest);
            }
        }
        LolbenchSource::Existing | LolbenchSource::Skip => {}
    }
    Ok(())
}

fn apply_worker_agent(config: &mut MacK3dConfig, creds: Option<&WorkerAgentPrompt>) -> Result<()> {
    if !matches!(config.role, NodeRole::Worker) {
        return Ok(());
    }
    let Some(url) = config.jenkins_agent.controller_url.clone() else {
        return Ok(());
    };
    let name = config
        .jenkins_agent
        .name
        .clone()
        .unwrap_or_else(jenkins_agent::default_agent_name);
    let jar = config
        .jenkins_agent
        .agent_jar
        .clone()
        .unwrap_or_else(|| {
            config
                .storage
                .downloads_dir()
                .unwrap_or_else(|| PathBuf::from("downloads"))
                .join("jenkins-agent")
                .join("agent.jar")
        });
    let remote_fs = config
        .jenkins_agent
        .remote_fs
        .clone()
        .unwrap_or_else(jenkins_agent::default_remote_fs);

    jenkins_agent::download_agent_jar(&url, &jar)?;

    let (user, token) = match creds {
        Some(c) => (c.api_user.as_deref(), c.api_token.as_deref()),
        None => (None, None),
    };
    let secret = jenkins_agent::try_register_node(
        &url,
        &name,
        &remote_fs,
        &config.jenkins_agent.labels,
        user,
        token,
    )?;
    let secret_placeholder = secret.unwrap_or_else(|| "REPLACE_ME".into());

    let script = remote_fs.join("launch-agent.sh");
    jenkins_agent::write_launch_script(&script, &url, &name, &jar, &secret_placeholder)?;
    println!("Wrote agent launch script: {}", script.display());

    resources::register_agent_cpu_cores(
        &url,
        &name,
        &config.resources.cpu_cores_label,
        config.jenkins_agent.cpu_cores,
    )?;

    Ok(())
}

fn prompt_cluster_settings(role: MacRole) -> Result<(String, u8, u16)> {
    let default_name = match role {
        MacRole::Controller => "ci-controller",
        MacRole::Worker => "ci-worker",
        MacRole::Standalone => "mac-k3d",
    };

    let name: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Cluster name")
        .default(default_name.into())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let agents: u8 = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Number of k3d agent nodes")
        .default(0)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let jenkins_port: u16 = if matches!(role, MacRole::Controller) {
        Input::with_theme(&ColorfulTheme::default())
            .with_prompt("Jenkins UI host port")
            .default(9080)
            .interact_text()
            .map_err(|_| Error::Cancelled)?
    } else {
        9080
    };

    Ok((name, agents, jenkins_port))
}

fn default_ports() -> Vec<crate::config::PortMapping> {
    vec![
        crate::config::PortMapping {
            host: 8080,
            container: 80,
        },
        crate::config::PortMapping {
            host: 8443,
            container: 443,
        },
    ]
}

fn print_summary(config: &MacK3dConfig, role: MacRole) {
    println!("\n--- Configuration summary ---\n");
    if let Some(base) = &config.storage.base_dir {
        println!("  Storage base:   {}", base.display());
    }
    println!("  Role:           {role:?}");
    println!("  Cluster:        {} ({} agents)", config.cluster.name, config.cluster.agents);
    println!(
        "  Jenkins:        {}",
        if config.jenkins.enabled {
            format!("enabled on port {}", config.jenkins.host_port)
        } else {
            "disabled".into()
        }
    );
    print_dep("  Docker", &config.dependencies.docker);
    print_dep("  k3d", &config.dependencies.k3d);
    print_dep("  kubectl", &config.dependencies.kubectl);
    print_dep("  helm", &config.dependencies.helm);
    print_dep("  harbor", &config.dependencies.harbor);
    print_dep("  java", &config.dependencies.java);
    if let Some(path) = &config.lolbench.path {
        println!("  LoLBench:      {} ({:?})", path.display(), config.lolbench.source);
    } else {
        println!("  LoLBench:      skipped");
    }
    if matches!(role, MacRole::Worker) {
        println!(
            "  Agent:         {} @ {} ({} cores)",
            config.jenkins_agent.name.as_deref().unwrap_or("?"),
            config
                .jenkins_agent
                .controller_url
                .as_deref()
                .unwrap_or("?"),
            config.jenkins_agent.cpu_cores
        );
    }
    println!("  Disk minimum:  {} GB", config.disk_min_gb());
    println!();
}

fn print_dep(label: &str, entry: &DependencyEntry) {
    let detail = match entry.source {
        DependencySource::Existing => entry
            .binary
            .as_ref()
            .map(|p| format!("existing ({})", p.display()))
            .unwrap_or_else(|| "existing".into()),
        DependencySource::Install if label.contains("harbor") => "install via uv/pipx".into(),
        DependencySource::Install => "install via Homebrew".into(),
        DependencySource::Skip => "skip".into(),
    };
    println!("{label}:        {detail}");
}

fn ensure_storage_dirs(config: &MacK3dConfig) -> Result<()> {
    let mut dirs = Vec::new();
    if let Some(base) = &config.storage.base_dir {
        dirs.push(base.clone());
    }
    if let Some(p) = config.storage.docker_dir() {
        dirs.push(p);
    }
    if let Some(p) = config.storage.k3d_dir() {
        dirs.push(p);
    }
    if let Some(p) = config.storage.jenkins_dir() {
        dirs.push(p);
    }
    if let Some(p) = config.storage.downloads_dir() {
        dirs.push(p);
    }

    for dir in dirs {
        let path = dir.display().to_string();
        std::fs::create_dir_all(&dir).map_err(|e| {
            Error::Config(format!("failed to create {path}: {e}"))
        })?;
        tracing::info!(%path, "created storage directory");
    }
    Ok(())
}

fn install_pending_dependencies(config: &mut MacK3dConfig) -> Result<()> {
    let names = ["docker", "k3d", "kubectl", "helm", "harbor", "java"];
    for name in names {
        let needs_install = match name {
            "docker" => config.dependencies.docker.source == DependencySource::Install,
            "k3d" => config.dependencies.k3d.source == DependencySource::Install,
            "kubectl" => config.dependencies.kubectl.source == DependencySource::Install,
            "helm" => config.dependencies.helm.source == DependencySource::Install,
            "harbor" => config.dependencies.harbor.source == DependencySource::Install,
            "java" => config.dependencies.java.source == DependencySource::Install,
            _ => false,
        };
        if !needs_install {
            continue;
        }
        let entry = install::install_and_discover(name)?;
        match name {
            "docker" => config.dependencies.docker = entry,
            "k3d" => config.dependencies.k3d = entry,
            "kubectl" => config.dependencies.kubectl = entry,
            "helm" => config.dependencies.helm = entry,
            "harbor" => config.dependencies.harbor = entry,
            "java" => config.dependencies.java = entry,
            _ => {}
        }
    }

    Ok(())
}
