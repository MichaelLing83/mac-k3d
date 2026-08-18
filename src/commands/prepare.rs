use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};
use crate::platform::ensure_macos;
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
}

pub async fn run(args: PrepareArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    let config_path = MacK3dConfig::default_config_path();
    let is_tty = atty::is(atty::Stream::Stdin);

    if args.non_interactive {
        prepare::validate(config)?;
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

    if config_path.exists() && is_tty && !args.interactive {
        match prepare::prompt_existing_config()? {
            ExistingConfigAction::ValidateOnly => {
                prepare::validate(config)?;
                tracing::info!("prepare: validated existing configuration");
                return Ok(());
            }
            ExistingConfigAction::Cancel => return Err(Error::Cancelled),
            ExistingConfigAction::RerunWizard => {}
        }
    }

    let run_wizard = args.interactive || (is_tty && !config_path.exists());

    if run_wizard {
        let generated = prepare::run_interactive()?;
        generated.save(Some(&config_path))?;
        tracing::info!("wrote config to {}", config_path.display());
        prepare::validate(&generated)?;
    } else {
        prepare::validate(config)?;
        tracing::info!(cluster = %config.cluster.name, "prepare: validated existing configuration");
    }

    Ok(())
}
