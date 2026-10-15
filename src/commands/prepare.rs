use std::path::{Path, PathBuf};

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::{Error, Result};
use crate::platform::ensure_supported_os;
use crate::prepare::{self, ExistingConfigAction};

#[derive(Debug, Args)]
pub struct PrepareArgs {
    /// Run interactive wizard to generate config
    #[arg(short, long)]
    pub interactive: bool,

    /// Validate existing config only; no prompts or writes
    #[arg(long, conflicts_with_all = ["interactive", "init_config"])]
    pub non_interactive: bool,

    /// Write default config file if it does not exist (no wizard)
    #[arg(long, conflicts_with = "interactive")]
    pub init_config: bool,

    /// Override minimum free disk (GB) for prepare validation (0 = role default)
    #[arg(long)]
    pub disk_min_gb: Option<u64>,
}

/// Wizard (questions only), save the YAML, then apply it to this host.
pub async fn run(
    args: PrepareArgs,
    config: &MacK3dConfig,
    config_path: Option<&Path>,
) -> Result<()> {
    ensure_supported_os()?;

    let config_path: PathBuf = config_path
        .map(PathBuf::from)
        .unwrap_or_else(MacK3dConfig::default_config_path);
    let is_tty = atty::is(atty::Stream::Stdin);

    let mut config = config.clone();
    if let Some(min) = args.disk_min_gb {
        config.resources.disk_min_gb = min;
    }

    if args.non_interactive {
        prepare::validate(&config)?;
        tracing::info!("prepare: non-interactive validation complete");
        return Ok(());
    }

    if args.init_config {
        if !config_path.exists() {
            config.save(Some(&config_path))?;
            tracing::info!("wrote default config to {}", config_path.display());
        } else {
            tracing::info!("config already exists at {}", config_path.display());
        }
        return Ok(());
    }

    let mut rerun_wizard = args.interactive;

    if config_path.exists() && is_tty && !args.interactive {
        match prepare::prompt_existing_config()? {
            ExistingConfigAction::UseExisting => return apply_and_validate(config, &config_path),
            ExistingConfigAction::Cancel => return Err(Error::Cancelled),
            ExistingConfigAction::RerunWizard => rerun_wizard = true,
        }
    }

    if rerun_wizard || (is_tty && !config_path.exists()) {
        let mut generated = prepare::run_interactive()?;
        if let Some(min) = args.disk_min_gb {
            generated.resources.disk_min_gb = min;
        }
        // Saved before anything is installed: a failed install keeps the answers.
        generated.save(Some(&config_path))?;
        println!("Saved {}", config_path.display());
        apply_and_validate(generated, &config_path)
    } else {
        prepare::validate(&config)?;
        tracing::info!(cluster = %config.cluster.name, "prepare: validated existing configuration");
        Ok(())
    }
}

fn apply_and_validate(mut config: MacK3dConfig, config_path: &Path) -> Result<()> {
    let applied = prepare::apply(&mut config, config_path);
    // Record whatever did get installed, even when apply stopped for root.
    config.save(Some(config_path))?;
    applied?;
    prepare::validate(&config)?;
    let c = config_path.display();
    match config.role {
        NodeRole::Worker => {
            println!("\nPrepare complete. Next: mac-k3d config -c {c} (setup runs it for you)")
        }
        NodeRole::Controller => println!(
            "\nPrepare complete. Next: mac-k3d start -c {c} && mac-k3d config -c {c} (setup runs both)"
        ),
        NodeRole::Standalone => println!("\nPrepare complete. Next: mac-k3d start -c {c}"),
    }
    Ok(())
}
