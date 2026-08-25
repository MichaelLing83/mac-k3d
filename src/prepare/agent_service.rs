use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

use crate::error::{Error, Result};
use crate::prepare::path_env;

const LAUNCH_AGENT_LABEL: &str = "com.mac-k3d.jenkins-agent";

/// `~/Library/LaunchAgents/com.mac-k3d.jenkins-agent.plist`
pub fn plist_path() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join("Library/LaunchAgents")
        .join(format!("{LAUNCH_AGENT_LABEL}.plist"))
}

fn gui_domain() -> String {
    let uid = Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|o| {
            String::from_utf8_lossy(&o.stdout)
                .trim()
                .parse::<u32>()
                .ok()
        })
        .unwrap_or(501);
    format!("gui/{uid}")
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// PATH for the Jenkins agent process (LaunchAgents default to /usr/bin:/bin only).
///
/// Merges the configuring shell's PATH with dirs needed for Docker Desktop + Harbor.
pub fn agent_path() -> String {
    path_env::agent_tool_path()
}

/// Install LaunchAgent plist and load it (KeepAlive + RunAtLoad).
pub fn install_and_start(launch_script: &Path, working_dir: &Path) -> Result<()> {
    if !launch_script.exists() {
        return Err(Error::Config(format!(
            "launch script not found: {}",
            launch_script.display()
        )));
    }

    // Don't start if secret was never filled in.
    let body = std::fs::read_to_string(launch_script).map_err(|e| {
        Error::Config(format!("failed to read {}: {e}", launch_script.display()))
    })?;
    if body.contains("REPLACE_ME") {
        println!(
            "Launch script still has REPLACE_ME secret — not starting LaunchAgent.\n\
             Re-run config with a valid API token, or paste the secret, then run config again."
        );
        return Ok(());
    }

    let plist = plist_path();
    if let Some(parent) = plist.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            Error::Config(format!("failed to create {}: {e}", parent.display()))
        })?;
    }

    let log_out = working_dir.join("jenkins-agent.stdout.log");
    let log_err = working_dir.join("jenkins-agent.stderr.log");
    let path = agent_path();
    let xml = format!(
        r#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>{script}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{cwd}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>{path}</string>
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>StandardOutPath</key>
  <string>{stdout}</string>
  <key>StandardErrorPath</key>
  <string>{stderr}</string>
</dict>
</plist>
"#,
        label = LAUNCH_AGENT_LABEL,
        script = xml_escape(&launch_script.display().to_string()),
        cwd = xml_escape(&working_dir.display().to_string()),
        path = xml_escape(&path),
        stdout = xml_escape(&log_out.display().to_string()),
        stderr = xml_escape(&log_err.display().to_string()),
    );

    std::fs::write(&plist, xml).map_err(|e| {
        Error::Config(format!("failed to write {}: {e}", plist.display()))
    })?;

    // Replace any previous job, then bootstrap.
    let _ = bootout();
    bootstrap(&plist)?;
    println!(
        "Jenkins agent LaunchAgent started ({LAUNCH_AGENT_LABEL}).\n\
         Logs: {}\n\
         Survives this shell; restarts if the process exits. Stop with teardown/clean.",
        log_out.display()
    );
    Ok(())
}

/// Unload LaunchAgent and remove plist (idempotent).
pub fn stop_and_uninstall() -> Result<()> {
    let plist = plist_path();
    let _ = bootout();
    if plist.exists() {
        std::fs::remove_file(&plist).map_err(|e| {
            Error::Config(format!("failed to remove {}: {e}", plist.display()))
        })?;
        println!("Removed LaunchAgent {}", plist.display());
    } else {
        println!("Jenkins agent LaunchAgent not installed; nothing to stop.");
    }
    Ok(())
}

fn bootstrap(plist: &Path) -> Result<()> {
    let domain = gui_domain();
    // Silence launchctl chatter (e.g. "Bootstrap failed: 5: Input/output error") —
    // we fall back to load/kickstart when needed.
    let status = Command::new("launchctl")
        .args(["bootstrap", &domain, &plist.display().to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: "launchctl bootstrap".into(),
            source: e.into(),
        })?;
    if status.success() {
        return Ok(());
    }

    // Already loaded or transient I/O: force a restart of the existing job.
    let service = format!("{domain}/{LAUNCH_AGENT_LABEL}");
    let kick = Command::new("launchctl")
        .args(["kickstart", "-k", &service])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: "launchctl kickstart".into(),
            source: e.into(),
        })?;
    if kick.success() {
        return Ok(());
    }

    // Fallback for older macOS / edge cases.
    let status = Command::new("launchctl")
        .args(["load", "-w", &plist.display().to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: "launchctl load".into(),
            source: e.into(),
        })?;
    if status.success() {
        Ok(())
    } else {
        Err(Error::CommandFailed {
            cmd: "launchctl bootstrap/kickstart/load".into(),
            source: anyhow::anyhow!("exit {:?}", status.code()),
        })
    }
}

fn bootout() -> Result<()> {
    let domain = gui_domain();
    let service = format!("{domain}/{LAUNCH_AGENT_LABEL}");
    let _ = Command::new("launchctl")
        .args(["bootout", &service])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
    let plist = plist_path();
    if plist.exists() {
        let _ = Command::new("launchctl")
            .args(["unload", "-w", &plist.display().to_string()])
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plist_path_ends_with_label() {
        assert!(plist_path()
            .to_string_lossy()
            .contains("com.mac-k3d.jenkins-agent.plist"));
    }

    #[test]
    fn xml_escape_ampersand() {
        assert_eq!(xml_escape("a&b"), "a&amp;b");
    }

    #[test]
    fn agent_path_nonempty() {
        assert!(!agent_path().is_empty());
        assert!(agent_path().contains('/'));
    }
}
