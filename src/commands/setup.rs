use std::path::{Path, PathBuf};

use clap::Args;
use dialoguer::{theme::ColorfulTheme, Confirm};

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_supported_os;
use crate::runtime::jenkins;

use super::{ConfigArgs, PrepareArgs, StartArgs};

#[derive(Debug, Default, Args)]
pub struct SetupArgs {
    /// Override minimum free disk (GB) for prepare validation (0 = role default)
    #[arg(long)]
    pub disk_min_gb: Option<u64>,
}

/// Interactive first-run: wizard, then start (controller/standalone) or config (worker).
pub async fn run(
    args: SetupArgs,
    _config: &MacK3dConfig,
    config_path: Option<&Path>,
) -> Result<()> {
    ensure_supported_os()?;

    let config_path: PathBuf = config_path
        .map(PathBuf::from)
        .unwrap_or_else(MacK3dConfig::default_config_path);

    println!("mac-k3d setup — prepare this computer as a controller or worker.\n");

    super::run_prepare(
        PrepareArgs {
            interactive: false,
            non_interactive: false,
            init_config: false,
            disk_min_gb: args.disk_min_gb,
        },
        &MacK3dConfig::load(Some(&config_path))?,
        Some(&config_path),
    )
    .await?;

    let config = MacK3dConfig::load(Some(&config_path))?;

    if !Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt("Continue and apply now (start/config)?")
        .default(true)
        .interact()
        .map_err(|_| crate::error::Error::Cancelled)?
    {
        print_manual_next_steps(&config, &config_path);
        return Ok(());
    }

    match config.role {
        NodeRole::Worker => {
            println!("\nWorker: registering Jenkins agent (not starting k3d/Jenkins)…\n");
            super::run_config(ConfigArgs::default(), &config, Some(&config_path)).await?;
        }
        NodeRole::Controller | NodeRole::Standalone => {
            println!("\nStarting Docker / k3d / Jenkins (if enabled)…\n");
            super::run_start(StartArgs::default(), &config, Some(&config_path)).await?;
            println!("\nApplying kubeconfig / Jenkins job / credentials…\n");
            super::run_config(ConfigArgs::default(), &config, Some(&config_path)).await?;
        }
    }

    print_done_summary(&config, &config_path);
    Ok(())
}

fn print_manual_next_steps(config: &MacK3dConfig, config_path: &Path) {
    let c = config_path.display();
    println!("\nConfig written to {c}.");
    match config.role {
        NodeRole::Worker => {
            println!("Next (worker — do not run start on this file):");
            println!("  mac-k3d config -c {c}");
        }
        NodeRole::Controller => {
            println!("Next (controller):");
            println!("  mac-k3d start -c {c}");
            println!("  mac-k3d config -c {c} --show-jenkins");
        }
        NodeRole::Standalone => {
            println!("Next:");
            println!("  mac-k3d start -c {c}");
            println!("  mac-k3d config -c {c}");
        }
    }
}

fn print_done_summary(config: &MacK3dConfig, config_path: &Path) {
    let role = match config.role {
        NodeRole::Standalone => "standalone",
        NodeRole::Controller => "controller",
        NodeRole::Worker => "worker",
    };
    let jenkins = if config.jenkins.enabled {
        jenkins::ui_url(config)
    } else {
        config
            .jenkins_agent
            .controller_url
            .clone()
            .unwrap_or_else(|| "(none)".into())
    };
    let agent = match config.role {
        NodeRole::Worker => crate::platform::agent_daemon_label().to_string(),
        _ => "(n/a — not a Jenkins agent role)".into(),
    };

    println!(
        "\n=== mac-k3d setup complete ===\n\
           Role:        {role}\n\
           Config:      {}\n\
           Jenkins UI:  {jenkins}\n\
           Agent:       {agent}\n",
        config_path.display()
    );
}
