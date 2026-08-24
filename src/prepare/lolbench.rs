use std::path::Path;
use std::process::Command;

use crate::error::{Error, Result};

/// Print and optionally run git clone into `dest`.
pub fn clone_repo(git_url: &str, dest: &Path, run_now: bool) -> Result<()> {
    println!("\nClone LoLBench with:\n");
    println!("  git clone {git_url} {}", dest.display());
    if !run_now {
        return Ok(());
    }
    if let Some(parent) = dest.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            Error::Config(format!("failed to create {}: {e}", parent.display()))
        })?;
    }
    let status = Command::new("git")
        .args(["clone", git_url, &dest.display().to_string()])
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: format!("git clone {git_url}"),
            source: e.into(),
        })?;
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("git clone {git_url}"),
            source: anyhow::anyhow!("exit {:?}", status.code()),
        });
    }
    Ok(())
}

/// Print release download/unpack commands (asset URL may be repo-specific).
pub fn print_release_commands(dest: &Path) {
    println!("\nOr download latest release and unpack:\n");
    println!("  mkdir -p {}", dest.display());
    println!(
        "  curl -sL <release-source-tarball-url> | tar -xz -C {} --strip-components=1",
        dest.display()
    );
    println!("\nReplace <release-source-tarball-url> with the repo's latest source archive URL.");
}

pub fn looks_like_lolbench(path: &Path) -> bool {
    path.join("harbor_tasks").is_dir() || path.join("scripts/run_task.sh").is_file()
}
