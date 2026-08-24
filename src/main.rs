use clap::Parser;
use mac_k3d::{Cli, MacK3dConfig};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> mac_k3d::Result<()> {
    let cli = Cli::parse();
    init_tracing(cli.verbose);

    let mut config = MacK3dConfig::load(cli.config.as_deref())?;

    match cli.command {
        mac_k3d::cli::Command::Prepare(args) => {
            mac_k3d::commands::run_prepare(args, &config, cli.config.as_deref()).await?;
        }
        mac_k3d::cli::Command::Start(args) => {
            if let Some(mode) = args.jenkins {
                config.apply_jenkins_mode(mode);
            }
            mac_k3d::commands::run_start(args, &config).await?;
        }
        mac_k3d::cli::Command::Config(args) => {
            mac_k3d::commands::run_config(args, &config).await?;
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
    }

    Ok(())
}

fn init_tracing(verbosity: u8) {
    let default_level = match verbosity {
        0 => "warn",
        1 => "info",
        _ => "debug",
    };

    let filter = EnvFilter::try_from_default_env()
        .unwrap_or_else(|_| EnvFilter::new(default_level));

    tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_target(false)
        .init();
}
