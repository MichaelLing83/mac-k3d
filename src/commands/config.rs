use clap::Args;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::platform::ensure_macos;

#[derive(Debug, Args)]
pub struct ConfigArgs {
    /// Merge kubeconfig into the default kubectl context
    #[arg(long, default_value_t = true)]
    pub merge_kubeconfig: bool,

    /// Print Jenkins URL and initial admin password hints
    #[arg(long)]
    pub show_jenkins: bool,
}

pub async fn run(args: ConfigArgs, config: &MacK3dConfig) -> Result<()> {
    ensure_macos()?;

    tracing::info!(
        merge_kubeconfig = args.merge_kubeconfig,
        show_jenkins = args.show_jenkins,
        "config: applying environment configuration"
    );

    if config.jenkins.enabled || args.show_jenkins {
        tracing::info!(port = config.jenkins.host_port, "jenkins UI expected on localhost");
    }

    // TODO: k3d kubeconfig merge, kubectl context, port-forward setup
    Ok(())
}
