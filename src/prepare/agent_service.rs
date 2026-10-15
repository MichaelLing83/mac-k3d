//! Thin wrapper: install/stop Jenkins agent daemon via platform adapters.

use std::path::Path;

use crate::error::Result;
use crate::platform;
use crate::prepare::path_env;

/// PATH for the Jenkins agent process.
pub fn agent_path() -> String {
    path_env::agent_tool_path()
}

/// Install and start the OS-specific agent daemon (LaunchAgent or systemd --user).
/// A running agent is restarted only when `changed` (new agent.jar or launch script).
pub fn install_and_start(launch_script: &Path, working_dir: &Path, changed: bool) -> Result<()> {
    let path = agent_path();
    platform::install_agent_daemon(launch_script, working_dir, &path, changed)
}

/// Stop and remove the OS-specific agent daemon (idempotent).
pub fn stop_and_uninstall() -> Result<()> {
    platform::stop_agent_daemon()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn agent_path_nonempty() {
        assert!(!agent_path().is_empty());
        assert!(agent_path().contains('/'));
    }
}
