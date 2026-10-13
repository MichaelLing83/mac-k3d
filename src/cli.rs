use std::path::PathBuf;

use clap::{Parser, Subcommand, ValueEnum};

use crate::commands;

#[derive(Debug, Parser)]
#[command(
    name = "mac-k3d",
    version = crate::build_info::VERSION_LINE,
    about = "Manage k3d + Docker on macOS and Linux (optional Jenkins)",
    long_about = None
)]
pub struct Cli {
    /// Path to configuration file (default: ~/.config/mac-k3d/config.yaml)
    #[arg(short, long, global = true)]
    pub config: Option<PathBuf>,

    /// Increase logging verbosity (-v, -vv)
    #[arg(short, long, global = true, action = clap::ArgAction::Count)]
    pub verbose: u8,

    #[command(subcommand)]
    pub command: Option<Command>,
}

#[derive(Debug, Subcommand)]
pub enum Command {
    /// Interactive first-run: wizard, then start (controller) or config (worker)
    Setup(commands::SetupArgs),

    /// Verify prerequisites and prepare the local environment
    Prepare(commands::PrepareArgs),

    /// Start Docker, k3d cluster, and optional Jenkins
    Start(commands::StartArgs),

    /// Apply configuration (kubeconfig, port-forwards, Jenkins setup)
    Config(commands::ConfigArgs),

    /// Write a sanitized copy of this machine's config YAML (no secrets)
    Export(commands::ExportArgs),

    /// Copy a sanitized config YAML onto this machine (write only)
    Import(commands::ImportArgs),

    /// Edit harness / LLM / model / benchmark / questions on a YAML file (no sed)
    Set(commands::SetArgs),

    /// Run iCode / DeepSeek eval (DeepSWE or LoLBench) locally or via Jenkins *_one_task jobs;
    /// `eval prepare` readies a Mac's DeepSWE images first
    Eval(commands::EvalArgs),

    /// Extract the pipeline scripts this binary was built with (Jenkins builds use this)
    Pipeline(commands::PipelineArgs),

    /// Stop cluster and services without removing data
    Teardown(commands::TeardownArgs),

    /// Remove cluster, volumes, and local state
    Clean(commands::CleanArgs),

    /// Show current environment status
    Status,
}

#[derive(Debug, Clone, Copy, Default, ValueEnum)]
pub enum JenkinsMode {
    #[default]
    /// Do not install or manage Jenkins
    Skip,
    /// Deploy Jenkins into the k3d cluster
    InCluster,
}
