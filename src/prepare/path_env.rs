use std::env;
use std::path::{Path, PathBuf};

use dialoguer::{theme::ColorfulTheme, Confirm};

use crate::error::{Error, Result};

const PATH_MARKER: &str = "# Added by mac-k3d prepare (uv/harbor tools)";

/// `~/.local/bin` — default install dir for `uv tool install` / many pipx setups.
pub fn user_local_bin() -> Option<PathBuf> {
    env::var_os("HOME").map(|h| PathBuf::from(h).join(".local").join("bin"))
}

pub fn dir_on_path(dir: &Path) -> bool {
    let Ok(path_var) = env::var("PATH") else {
        return false;
    };
    let dir = canonicalize_loose(dir);
    env::split_paths(&path_var).any(|p| canonicalize_loose(&p) == dir)
}

fn canonicalize_loose(path: &Path) -> PathBuf {
    path.canonicalize().unwrap_or_else(|_| path.to_path_buf())
}

/// Prepend `dir` to this process's `PATH` if missing.
pub fn prepend_to_process_path(dir: &Path) -> Result<()> {
    if dir_on_path(dir) {
        return Ok(());
    }
    let dir_str = dir.display().to_string();
    let new_path = match env::var_os("PATH") {
        Some(existing) => {
            let mut paths = vec![PathBuf::from(&dir_str)];
            paths.extend(env::split_paths(&existing));
            env::join_paths(paths).map_err(|e| {
                Error::Config(format!("failed to update PATH: {e}"))
            })?
        }
        None => dir_str.into(),
    };
    env::set_var("PATH", new_path);
    tracing::info!(path = %dir.display(), "prepended to process PATH");
    Ok(())
}

/// Ensure `~/.local/bin` exists and is on this process PATH (no shell-rc prompt).
pub fn ensure_user_local_bin_in_process() -> Result<()> {
    let Some(local_bin) = user_local_bin() else {
        return Ok(());
    };
    if !local_bin.exists() {
        std::fs::create_dir_all(&local_bin).map_err(|e| {
            Error::Config(format!("failed to create {}: {e}", local_bin.display()))
        })?;
    }
    prepend_to_process_path(&local_bin)
}

/// Ensure `~/.local/bin` is usable in this process; optionally persist in the user's shell RC.
pub fn ensure_user_local_bin(interactive: bool) -> Result<()> {
    ensure_user_local_bin_in_process()?;

    let Some(local_bin) = user_local_bin() else {
        return Ok(());
    };

    if dir_already_in_shell_rc(&local_bin)? {
        return Ok(());
    }

    // Still missing from persisted shell config (process PATH alone does not help new shells).
    if !interactive || !atty::is(atty::Stream::Stdin) {
        println!(
            "Note: {} is on PATH for this process only. Add it to your shell config for new terminals:\n  export PATH=\"{}:$PATH\"",
            local_bin.display(),
            local_bin.display()
        );
        return Ok(());
    }

    let rc = preferred_shell_rc();
    let add = Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt(format!(
            "Add {} to PATH in {} for future shells?",
            local_bin.display(),
            rc.display()
        ))
        .default(true)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    if add {
        append_path_export(&rc, &local_bin)?;
        println!(
            "Updated {}. Open a new terminal (or `source {}`) to pick it up elsewhere.",
            rc.display(),
            rc.display()
        );
    } else {
        println!(
            "Skipped shell update. For this session PATH already includes {}.",
            local_bin.display()
        );
    }
    Ok(())
}

fn dir_already_in_shell_rc(dir: &Path) -> Result<bool> {
    let rc = preferred_shell_rc();
    if !rc.exists() {
        return Ok(false);
    }
    let contents = std::fs::read_to_string(&rc).map_err(|e| {
        Error::Config(format!("failed to read {}: {e}", rc.display()))
    })?;
    let needle = dir.display().to_string();
    Ok(contents.contains(&needle) || contents.contains(PATH_MARKER))
}

fn preferred_shell_rc() -> PathBuf {
    let home = env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));
    let shell = env::var("SHELL").unwrap_or_default();
    if shell.ends_with("/fish") {
        home.join(".config/fish/config.fish")
    } else if shell.ends_with("/bash") {
        let profile = home.join(".bash_profile");
        if profile.exists() {
            profile
        } else {
            home.join(".bashrc")
        }
    } else {
        // zsh default on modern macOS
        home.join(".zshrc")
    }
}

fn append_path_export(rc: &Path, dir: &Path) -> Result<()> {
    if let Some(parent) = rc.parent() {
        std::fs::create_dir_all(parent).map_err(|e| {
            Error::Config(format!("failed to create {}: {e}", parent.display()))
        })?;
    }

    let line = if rc.extension().and_then(|s| s.to_str()) == Some("fish")
        || rc
            .file_name()
            .and_then(|s| s.to_str())
            .is_some_and(|n| n == "config.fish")
    {
        format!(
            "\n{PATH_MARKER}\nfish_add_path {}\n",
            dir.display()
        )
    } else {
        format!(
            "\n{PATH_MARKER}\nexport PATH=\"{}:$PATH\"\n",
            dir.display()
        )
    };

    use std::io::Write;
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(rc)
        .map_err(|e| Error::Config(format!("failed to open {}: {e}", rc.display())))?;
    file.write_all(line.as_bytes())
        .map_err(|e| Error::Config(format!("failed to update {}: {e}", rc.display())))?;
    Ok(())
}

/// PATH for Jenkins agent / LaunchAgent: configuring shell PATH + Docker/Harbor dirs.
///
/// LaunchAgents otherwise only get `/usr/bin:/bin:/usr/sbin:/sbin`, so `docker` and
/// `harbor` (often under `/usr/local/bin` or `~/.local/bin`) are missing in builds.
pub fn agent_tool_path() -> String {
    let mut dirs: Vec<PathBuf> = Vec::new();

    let mut push_unique = |p: PathBuf| {
        if p.as_os_str().is_empty() {
            return;
        }
        if !dirs.iter().any(|d| d == &p) {
            dirs.push(p);
        }
    };

    // Prefer dirs from the shell that ran `mac-k3d config`.
    if let Ok(path) = env::var("PATH") {
        for p in env::split_paths(&path) {
            push_unique(p);
        }
    }

    // Guaranteed extras (even if not on the configuring shell PATH).
    if let Some(local) = user_local_bin() {
        push_unique(local);
    }
    if let Some(home) = env::var_os("HOME").map(PathBuf::from) {
        push_unique(home.join("homebrew/bin"));
        push_unique(home.join(".cargo/bin"));
    }
    for extra in [
        "/usr/local/bin",
        "/opt/homebrew/bin",
        "/Applications/Docker.app/Contents/Resources/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
    ] {
        push_unique(PathBuf::from(extra));
    }

    env::join_paths(&dirs)
        .map(|os| os.to_string_lossy().into_owned())
        .unwrap_or_else(|_| {
            "/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin".into()
        })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn user_local_bin_under_home() {
        if let Some(p) = user_local_bin() {
            assert!(p.ends_with(".local/bin"));
        }
    }

    #[test]
    fn agent_tool_path_includes_docker_and_local_bin() {
        let p = agent_tool_path();
        assert!(p.contains("/usr/local/bin") || p.contains("/opt/homebrew/bin"));
        assert!(p.contains(".local/bin") || p.contains("/usr/bin"));
        assert!(p.contains("Docker.app") || p.contains("/usr/local/bin"));
    }
}
