//! Question widgets every role shares: storage, role, one dependency, the
//! pinned Harbor, and the summary.

use std::path::{Path, PathBuf};

use dialoguer::{theme::ColorfulTheme, Input, Select};

use super::MacRole;
use crate::config::{DependencyEntry, DependencySource, MacK3dConfig, PortMapping, StorageConfig};
use crate::error::{Error, Result};
use crate::prepare::discovery::DiscoveredTool;
use crate::prepare::install::{self, entry_from_path};
use crate::prepare::toolchain;
use crate::prepare::volumes::VolumeCandidate;

pub fn prompt_storage_base(volumes: &[VolumeCandidate]) -> Result<PathBuf> {
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

pub fn prompt_role() -> Result<MacRole> {
    let options = [
        "Local development only (no Jenkins)",
        "CI controller (Jenkins in k3d)",
        "CI worker (Jenkins agent only)",
    ];
    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt(crate::platform::role_prompt())
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

/// Use what is found, point at another binary, install, or (optional only) skip.
pub fn prompt_dependency(
    label: &str,
    discovered: Option<&DiscoveredTool>,
    required: bool,
    is_docker: bool,
) -> Result<DependencyEntry> {
    let app = || {
        if is_docker {
            crate::platform::docker_app_default()
        } else {
            None
        }
    };
    let install_entry = || DependencyEntry {
        source: DependencySource::Install,
        binary: None,
        app: app(),
    };
    let pkg = install_label(label);

    if let Some(tool) = discovered {
        println!("\n{label}: found");
        println!("  {}", tool.describe());

        let mut options = vec![
            "Use this installation (recommended)".to_string(),
            "Specify a different binary path".to_string(),
        ];
        if is_docker {
            options.push(format!("Install via {pkg} (not recommended if already installed)"));
        } else {
            options.push(format!("Install via {pkg}"));
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
                let path: String = Input::with_theme(&ColorfulTheme::default())
                    .with_prompt("Binary path")
                    .default(tool.binary.display().to_string())
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?;
                entry_from_path(PathBuf::from(path.trim()), app())
            }
            2 => Ok(install_entry()),
            _ if !required => Ok(not_needed(None)),
            _ => Err(Error::Cancelled),
        };
    }

    println!("\n{label}: not found");

    let mut options = vec![
        format!("Install via {pkg}"),
        "Specify path to existing binary".to_string(),
    ];
    if !required {
        options.push("Skip".to_string());
    }

    let selection = Select::with_theme(&ColorfulTheme::default())
        .with_prompt(format!("{label} action"))
        .items(&options)
        .default(if required { 0 } else { options.len().saturating_sub(1) })
        .interact()
        .map_err(|_| Error::Cancelled)?;

    match selection {
        0 => Ok(install_entry()),
        1 => {
            let path: String = Input::with_theme(&ColorfulTheme::default())
                .with_prompt("Binary path")
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
            entry_from_path(PathBuf::from(path.trim()), app())
        }
        _ if !required => Ok(not_needed(None)),
        _ => Err(Error::Validation(format!("{label} is required"))),
    }
}

/// "apt (needs root)" when this user would need an administrator for it.
fn install_label(label: &str) -> String {
    let pkg = crate::platform::package_manager_label();
    let name = if label == crate::platform::docker_display_name() {
        "docker"
    } else {
        label
    };
    if crate::platform::root_install_command(name).is_some() {
        format!("{pkg} (needs root or sudo)")
    } else {
        pkg.to_string()
    }
}

/// A tool this role does not use. Remembers where it is, if found.
pub fn not_needed(discovered: Option<&DiscoveredTool>) -> DependencyEntry {
    DependencyEntry {
        source: DependencySource::Skip,
        binary: discovered.map(|t| t.binary.clone()),
        app: None,
    }
}

/// Harbor is not a question: the pipeline asserts one version, so setup
/// installs exactly that one (per user, with uv) unless it is already there.
pub fn pinned_harbor() -> DependencyEntry {
    let want = toolchain::harbor_version();
    match toolchain::installed_harbor_version() {
        Some(got) if got == want => {
            println!("\nharbor: {want} found (pinned in pipeline/config/toolchain.env)");
            DependencyEntry {
                source: DependencySource::Existing,
                binary: toolchain::harbor_binary(),
                app: None,
            }
        }
        got => {
            let found = got.map(|v| format!("{v} found; ")).unwrap_or_default();
            println!(
                "\nharbor: {found}setup installs the pinned {want} with `uv tool install` into ~/.local/bin (no root)"
            );
            DependencyEntry {
                source: DependencySource::Install,
                binary: None,
                app: None,
            }
        }
    }
}

/// Java is not a question on a worker: the agent must run at least the
/// controller image's Java (JAVA_MAJOR), so a Java that meets it is used and
/// anything older is replaced by an install of that version.
pub fn pinned_java(discovered: Option<&DiscoveredTool>) -> DependencyEntry {
    let want = toolchain::java_major();
    let found = discovered.and_then(|t| toolchain::java_major_of(&t.binary).map(|m| (t, m)));
    match found {
        Some((tool, major)) if major >= want => {
            println!(
                "\njava: Java {major} found at {} (agents need {want}+, pinned in pipeline/config/toolchain.env)",
                tool.binary.display()
            );
            install::tool_to_entry(tool)
        }
        other => {
            let seen = other
                .map(|(t, m)| format!("Java {m} at {} is too old; ", t.binary.display()))
                .unwrap_or_default();
            println!(
                "\njava: {seen}Jenkins agents need Java {want}. Setup installs it via {}",
                install_label("java")
            );
            DependencyEntry {
                source: DependencySource::Install,
                binary: None,
                app: None,
            }
        }
    }
}

pub fn storage_for(base_dir: &Path) -> StorageConfig {
    StorageConfig {
        base_dir: Some(base_dir.to_path_buf()),
        docker: Some(base_dir.join("docker")),
        k3d: Some(base_dir.join("k3d")),
        jenkins: Some(base_dir.join("jenkins")),
        downloads: Some(base_dir.join("downloads")),
    }
}

pub fn default_ports() -> Vec<PortMapping> {
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

pub fn print_summary(config: &MacK3dConfig, tools: &[&str]) {
    println!("\n--- Configuration summary ---\n");
    if let Some(base) = &config.storage.base_dir {
        println!("  Storage base:   {}", base.display());
    }
    println!("  Role:           {:?}", config.role);
    if !matches!(config.role, crate::config::NodeRole::Worker) {
        println!(
            "  Cluster:        {} ({} agents)",
            config.cluster.name, config.cluster.agents
        );
        println!(
            "  Jenkins:        {}",
            if config.jenkins.enabled {
                format!("enabled on port {}", config.jenkins.host_port)
            } else {
                "disabled".into()
            }
        );
    }
    for (name, entry) in config.dependencies.entries() {
        if tools.contains(&name) {
            print_dep(name, entry);
        }
    }
    if matches!(config.role, crate::config::NodeRole::Worker) {
        let agent = &config.jenkins_agent;
        println!(
            "  Agent:          {} @ {} ({} cores)",
            agent.name.as_deref().unwrap_or("?"),
            agent.controller_url.as_deref().unwrap_or("?"),
            agent.cpu_cores
        );
        println!(
            "  API user/token: {}",
            if agent.api_credentials().is_some() {
                "set"
            } else {
                "left blank (fill in later)"
            }
        );
    }
    if config.jenkins.enabled {
        let job = &config.jenkins_job;
        let or_build_time = |s: &str| {
            if s.is_empty() {
                "(set at build time)".to_string()
            } else {
                s.to_string()
            }
        };
        println!(
            "  Job defaults:   mode={} task={} icode_release={} git={}@{} args={}",
            job.default_eval_mode,
            job.default_task,
            or_build_time(&job.default_icode_release),
            or_build_time(&job.default_icode_git_url),
            job.default_icode_git_ref,
            if job.default_icode_args.is_empty() {
                "(none)"
            } else {
                job.default_icode_args.as_str()
            }
        );
    }
    println!("  Disk minimum:   {} GB", config.disk_min_gb());
    println!();
}

fn print_dep(label: &str, entry: &DependencyEntry) {
    let detail = match entry.source {
        DependencySource::Existing => entry
            .binary
            .as_ref()
            .map(|p| format!("existing ({})", p.display()))
            .unwrap_or_else(|| "existing".into()),
        DependencySource::Install if label == "harbor" => {
            format!("install {} via uv", toolchain::harbor_version())
        }
        DependencySource::Install => format!("install via {}", install_label(label)),
        DependencySource::Skip => "skip".into(),
    };
    println!("  {label:<15} {detail}");
}
