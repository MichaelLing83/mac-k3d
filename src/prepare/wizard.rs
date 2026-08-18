use std::path::PathBuf;

use dialoguer::{theme::ColorfulTheme, Confirm, Input, Select};

use crate::config::{
    ClusterConfig, DependenciesConfig, DependencyEntry, DependencySource, MacK3dConfig,
    StorageConfig,
};
use crate::error::{Error, Result};
use crate::prepare::discovery::{DiscoveredDeps, DiscoveredTool};
use crate::prepare::install::{self, entry_from_path};
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
    let jenkins_enabled = matches!(role, MacRole::Controller);

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

    let (cluster_name, agents, jenkins_port) = prompt_cluster_settings(role)?;

    let storage = StorageConfig {
        base_dir: Some(base_dir.clone()),
        docker: Some(base_dir.join("docker")),
        k3d: Some(base_dir.join("k3d")),
        jenkins: Some(base_dir.join("jenkins")),
        downloads: Some(base_dir.join("downloads")),
    };

    let mut config = MacK3dConfig {
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
        },
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

    if config.jenkins.enabled {
        println!(
            "\nDocker Desktop data stays in its default location unless moved manually."
        );
        if let Some(docker_dir) = config.storage.docker_dir() {
            println!(
                "Recommended Docker data path: {}",
                docker_dir.display()
            );
            println!("Move via Docker Desktop → Settings → Resources, or symlink manually.");
        }
    }

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
    println!();
}

fn print_dep(label: &str, entry: &DependencyEntry) {
    let detail = match entry.source {
        DependencySource::Existing => entry
            .binary
            .as_ref()
            .map(|p| format!("existing ({})", p.display()))
            .unwrap_or_else(|| "existing".into()),
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
    let pairs = [
        ("docker", &mut config.dependencies.docker),
        ("k3d", &mut config.dependencies.k3d),
        ("kubectl", &mut config.dependencies.kubectl),
        ("helm", &mut config.dependencies.helm),
    ];

    for (name, entry) in pairs {
        if entry.source == DependencySource::Install {
            *entry = install::install_and_discover(name)?;
        }
    }

    Ok(())
}
