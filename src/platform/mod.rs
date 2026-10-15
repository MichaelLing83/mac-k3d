//! OS-specific adapters for macOS and Linux.
//!
//! Shared prepare/runtime code calls these facades; compile-time `cfg` selects
//! the implementation in [`macos`] or [`linux`].

#[cfg(target_os = "linux")]
mod linux;
#[cfg(target_os = "macos")]
mod macos;

#[cfg(target_os = "linux")]
use linux as os;
#[cfg(target_os = "macos")]
use macos as os;

use std::path::{Path, PathBuf};

use crate::error::{Error, Result};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum HostOs {
    Macos,
    Linux,
}

impl HostOs {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Macos => "macos",
            Self::Linux => "linux",
        }
    }
}

/// Detect the OS this binary was built for.
pub fn host_os() -> HostOs {
    #[cfg(target_os = "macos")]
    {
        HostOs::Macos
    }
    #[cfg(target_os = "linux")]
    {
        HostOs::Linux
    }
    #[cfg(not(any(target_os = "macos", target_os = "linux")))]
    {
        // Unreachable for supported builds; kept for clarity.
        HostOs::Linux
    }
}

/// Guard for commands that require macOS or Linux.
pub fn ensure_supported_os() -> Result<()> {
    if cfg!(any(target_os = "macos", target_os = "linux")) {
        Ok(())
    } else {
        Err(Error::UnsupportedPlatform)
    }
}

/// Backward-compatible alias used during migration.
#[deprecated(note = "use ensure_supported_os")]
pub fn ensure_macos() -> Result<()> {
    ensure_supported_os()
}

pub fn docker_display_name() -> &'static str {
    os::docker_display_name()
}

/// What to do when `docker info` has no Server after install (Linux logout, macOS Desktop).
pub fn docker_not_ready_hint() -> &'static str {
    os::docker_not_ready_hint()
}

pub fn package_manager_label() -> &'static str {
    os::package_manager_label()
}

pub fn role_prompt() -> &'static str {
    os::role_prompt()
}

pub fn machine_noun() -> &'static str {
    os::machine_noun()
}

pub fn default_agent_labels() -> Vec<String> {
    os::default_agent_labels()
}

/// systemd unit (Linux) or LaunchAgent label (macOS) for the Jenkins inbound agent.
pub fn agent_daemon_label() -> &'static str {
    os::agent_daemon_label()
}

pub fn platform_label() -> &'static str {
    host_os().as_str()
}

/// Extra mount roots to scan for storage / LoLBench (beyond `$HOME`).
pub fn scan_extra_mount_roots() -> Vec<PathBuf> {
    os::scan_extra_mount_roots()
}

pub fn docker_app_default() -> Option<PathBuf> {
    os::docker_app_default()
}

pub fn requires_docker_app() -> bool {
    os::requires_docker_app()
}

/// Dirs the agent PATH puts before the configuring shell's PATH.
pub fn agent_path_first() -> Vec<&'static str> {
    os::agent_path_first()
}

pub fn agent_path_extras() -> Vec<&'static str> {
    os::agent_path_extras()
}

pub fn preferred_shell_rc_default(home: &Path) -> PathBuf {
    os::preferred_shell_rc_default(home)
}

/// Install the agent daemon and make sure it runs. `changed` says whether
/// agent.jar, launch-agent.sh or .agent-secret changed on disk; only then is a running agent
/// restarted, so a routine `config` never drops a connected agent mid-build.
pub fn install_agent_daemon(
    launch_script: &Path,
    working_dir: &Path,
    path_env: &str,
    changed: bool,
) -> Result<()> {
    os::install_agent_daemon(launch_script, working_dir, path_env, changed)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AgentAction {
    /// Not running: start it.
    Start,
    /// Running an old jar, script or unit: restart it.
    Restart,
    /// Running and nothing changed: leave it connected.
    LeaveRunning,
}

/// `changed` covers agent.jar, launch-agent.sh, .agent-secret and the daemon's own unit/plist.
pub fn agent_action(running: bool, changed: bool) -> AgentAction {
    match (running, changed) {
        (false, _) => AgentAction::Start,
        (true, true) => AgentAction::Restart,
        (true, false) => AgentAction::LeaveRunning,
    }
}

pub fn stop_agent_daemon() -> Result<()> {
    os::stop_agent_daemon()
}

pub fn package_install_available() -> bool {
    os::package_install_available()
}

pub fn install_package(name: &str) -> Result<()> {
    os::install_package(name)
}

pub fn harbor_bootstrap_hint() -> &'static str {
    os::harbor_bootstrap_hint()
}

/// One command an administrator has to run for this user, and why.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RootStep {
    pub why: String,
    pub command: String,
}

/// Root command that installs `name`, or `None` when this OS installs it without root.
pub fn root_install_command(name: &str) -> Option<String> {
    os::root_install_command(name)
}

/// True when this process may run root commands itself (root, or sudo works).
pub fn can_elevate() -> bool {
    os::can_elevate()
}

/// Host settings a worker needs that only root can change (docker access, linger).
pub fn worker_host_root_steps() -> Vec<RootStep> {
    os::worker_host_root_steps()
}

/// Host settings that stop a worker taking builds unattended (macOS: auto-login, sleep).
pub fn worker_host_warnings() -> Vec<String> {
    os::worker_host_warnings()
}

/// Run one root command: directly as root, else through sudo.
pub fn run_as_root(command: &str) -> Result<()> {
    os::run_as_root(command)
}
