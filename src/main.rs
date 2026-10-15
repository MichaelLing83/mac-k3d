use clap::Parser;
use mac_k3d::{Cli, MacK3dConfig};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() {
    if let Err(err) = run().await {
        eprintln!("Error: {err}");
        std::process::exit(1);
    }
}

async fn run() -> mac_k3d::Result<()> {
    let cli = Cli::parse();
    init_tracing(cli.verbose);

    let command = match cli.command {
        Some(cmd) => cmd,
        None => {
            if atty::is(atty::Stream::Stdin) {
                mac_k3d::cli::Command::Setup(mac_k3d::commands::SetupArgs::default())
            } else {
                eprintln!(
                    "mac-k3d: no command given and stdin is not a TTY.\n\
                     Run `mac-k3d setup` in a terminal, or `mac-k3d --help`."
                );
                std::process::exit(2);
            }
        }
    };

    match command {
        mac_k3d::cli::Command::Export(args) => {
            mac_k3d::commands::run_export(args, cli.config.as_deref())?;
        }
        mac_k3d::cli::Command::Import(args) => {
            // Incoming file is args.source; do not require a live dest YAML.
            mac_k3d::commands::run_import(args, cli.config.as_deref())?;
        }
        mac_k3d::cli::Command::Set(args) => {
            mac_k3d::commands::run_set(args, cli.config.as_deref())?;
        }
        mac_k3d::cli::Command::Pipeline(args) => {
            // Runs inside Jenkins builds, where no mac-k3d config is needed.
            mac_k3d::commands::run_pipeline(args)?;
        }
        mac_k3d::cli::Command::Setup(args) => {
            let config = MacK3dConfig::load(cli.config.as_deref())?;
            mac_k3d::commands::run_setup(args, &config, cli.config.as_deref()).await?;
        }
        mac_k3d::cli::Command::Prepare(args) => {
            let config = MacK3dConfig::load(cli.config.as_deref())?;
            mac_k3d::commands::run_prepare(args, &config, cli.config.as_deref()).await?;
        }
        other => {
            let mut config = MacK3dConfig::load_existing(cli.config.as_deref())?;
            match other {
                mac_k3d::cli::Command::Start(args) => {
                    if let Some(mode) = args.jenkins {
                        config.apply_jenkins_mode(mode);
                    }
                    mac_k3d::commands::run_start(args, &config, cli.config.as_deref()).await?;
                }
                mac_k3d::cli::Command::Config(args) => {
                    mac_k3d::commands::run_config(args, &config, cli.config.as_deref()).await?;
                }
                mac_k3d::cli::Command::Eval(args) => {
                    mac_k3d::commands::run_eval(args, &config).await?;
                }
                mac_k3d::cli::Command::Teardown(args) => {
                    mac_k3d::commands::run_teardown(args, &config).await?;
                }
                mac_k3d::cli::Command::Clean(args) => {
                    mac_k3d::commands::run_clean(args, &config, cli.config.as_deref()).await?;
                }
                mac_k3d::cli::Command::Status => {
                    mac_k3d::commands::run_status(&config).await?;
                }
                mac_k3d::cli::Command::Export(_)
                | mac_k3d::cli::Command::Import(_)
                | mac_k3d::cli::Command::Set(_)
                | mac_k3d::cli::Command::Pipeline(_)
                | mac_k3d::cli::Command::Setup(_)
                | mac_k3d::cli::Command::Prepare(_) => {
                    unreachable!("handled above");
                }
            }
        }
    }

    Ok(())
}

fn init_tracing(verbosity: u8) {
    let default_level = match verbosity {
        0 => "warn",
        1 => "info",
        _ => "debug",
    };

    let filter =
        EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new(default_level));

    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}
