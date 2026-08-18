use std::ffi::OsStr;
use std::path::Path;

use tokio::process::Command;

use crate::error::{Error, Result};

pub fn format_cmd(program: &Path, args: &[impl AsRef<OsStr>]) -> String {
    let mut parts = vec![program.display().to_string()];
    for arg in args {
        parts.push(arg.as_ref().to_string_lossy().into_owned());
    }
    parts.join(" ")
}

/// Run a command and capture stdout. Stderr is included in the error on failure.
pub async fn capture(program: &Path, args: &[impl AsRef<OsStr>]) -> Result<String> {
    let cmd = format_cmd(program, args);
    tracing::debug!(%cmd, "running");

    let output = Command::new(program)
        .args(args)
        .output()
        .await
        .map_err(|e| Error::CommandFailed {
            cmd: cmd.clone(),
            source: e.into(),
        })?;

    if !output.status.success() {
        let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
        let stdout = String::from_utf8_lossy(&output.stdout).trim().to_string();
        let detail = if stderr.is_empty() { stdout } else { stderr };
        return Err(Error::CommandFailed {
            cmd,
            source: anyhow::anyhow!(
                "exit {}: {}",
                output.status.code().unwrap_or(-1),
                detail
            ),
        });
    }

    Ok(String::from_utf8_lossy(&output.stdout).into_owned())
}

/// Run a command with inherited stdio so the user sees progress.
pub async fn visible(program: &Path, args: &[impl AsRef<OsStr>]) -> Result<()> {
    let cmd = format_cmd(program, args);
    tracing::debug!(%cmd, "running (visible)");

    let status = Command::new(program)
        .args(args)
        .status()
        .await
        .map_err(|e| Error::CommandFailed {
            cmd: cmd.clone(),
            source: e.into(),
        })?;

    if !status.success() {
        return Err(Error::CommandFailed {
            cmd,
            source: anyhow::anyhow!("exit {}", status.code().unwrap_or(-1)),
        });
    }

    Ok(())
}

/// Like [`capture`], but returns `Ok(None)` when the command exits non-zero.
pub async fn capture_ok(program: &Path, args: &[impl AsRef<OsStr>]) -> Option<String> {
    capture(program, args).await.ok()
}
