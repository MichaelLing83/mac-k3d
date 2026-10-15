//! `mac-k3d config`: apply a saved YAML to the services it describes. Workers
//! register their Jenkins agent; controllers configure the cluster and Jenkins.
//! This is the only command that registers an agent.

mod controller;
mod worker;

use std::path::{Path, PathBuf};

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::Result;
use crate::platform::ensure_supported_os;

#[derive(Debug, Default, Args)]
pub struct ConfigArgs {
    /// Do not merge kubeconfig into ~/.kube/config
    #[arg(long)]
    pub no_merge_kubeconfig: bool,

    /// Print Jenkins URL and initial admin password
    #[arg(long)]
    pub show_jenkins: bool,

    /// Skip Jenkins agent register/launch-script update (worker only)
    #[arg(long)]
    pub skip_agent: bool,

    /// Skip creating the `lolbench_one_task` Pipeline job (controller / Jenkins enabled)
    #[arg(long)]
    pub skip_job: bool,

    /// Skip creating/updating Jenkins Credentials; still list existing IDs for job binds
    #[arg(long)]
    pub skip_secrets: bool,

    /// Re-prompt for CI secrets even if Jenkins credentials already exist
    #[arg(long)]
    pub update_secrets: bool,
}

pub async fn run(args: ConfigArgs, config: &MacK3dConfig, config_path: Option<&Path>) -> Result<()> {
    ensure_supported_os()?;
    let config_path: PathBuf = MacK3dConfig::resolve_config_path(config_path);
    // A YAML written before config files were private may still be 0644, with the API token in it.
    if let Err(err) = crate::config::make_private(&config_path) {
        println!("Warning: {err}; the API token in it may be readable by other users.");
    }

    match config.role {
        NodeRole::Worker => worker::run(&args, config, &config_path)?,
        NodeRole::Controller | NodeRole::Standalone => {
            controller::run(&args, config, &config_path).await?
        }
    }
    extract_share_pipeline();

    println!("Config complete.");
    Ok(())
}

/// The pipeline copy `eval --local` and manual runs use. Builds extract their own.
fn extract_share_pipeline() {
    if let Err(err) = crate::prepare::eval_assets::ensure_share_pipeline_reported() {
        println!("Warning: could not extract pipeline ({err}).");
    }
}
