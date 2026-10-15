//! The eval toolchain a worker needs: Harbor at the version the pipeline
//! asserts, the agent's Java, and the bash/python3 the pipeline scripts run on.
//! Both sides read the pins from `pipeline/config/toolchain.env`, so `setup`
//! and the env phase cannot disagree.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::error::{Error, Result};
use crate::platform;
use crate::prepare::{discovery, path_env};

const TOOLCHAIN_ENV: &str = include_str!("../../pipeline/config/toolchain.env");

/// Value of `KEY=value` in toolchain.env.
pub fn pin(key: &str) -> Option<&'static str> {
    parse_pin(TOOLCHAIN_ENV, key)
}

fn parse_pin<'a>(text: &'a str, key: &str) -> Option<&'a str> {
    text.lines()
        .map(str::trim)
        .filter(|l| !l.starts_with('#'))
        .filter_map(|l| l.split_once('='))
        .find(|(k, _)| k.trim() == key)
        .map(|(_, v)| v.trim())
        .filter(|v| !v.is_empty())
}

fn required(key: &str) -> &'static str {
    pin(key).unwrap_or_else(|| panic!("pipeline/config/toolchain.env must set {key}"))
}

fn required_number(key: &str) -> u64 {
    required(key)
        .parse()
        .unwrap_or_else(|_| panic!("{key} in pipeline/config/toolchain.env must be a number"))
}

fn required_major_minor(key: &str) -> (u32, u32) {
    parse_major_minor(required(key))
        .unwrap_or_else(|| panic!("{key} in pipeline/config/toolchain.env must look like 3.11"))
}

/// Harbor version the pipeline's env phase checks.
pub fn harbor_version() -> &'static str {
    required("HARBOR_VERSION")
}

/// Compose plugin version downloaded when `docker compose` is missing.
pub fn docker_compose_version() -> &'static str {
    required("DOCKER_COMPOSE_VERSION")
}

/// Buildx plugin version downloaded when `docker buildx` is missing.
pub fn docker_buildx_version() -> &'static str {
    required("DOCKER_BUILDX_VERSION")
}

/// Java major version of the controller image; agents need at least this.
pub fn java_major() -> u32 {
    required_number("JAVA_MAJOR") as u32
}

/// Oldest bash the pipeline scripts run on.
pub fn bash_min() -> (u32, u32) {
    required_major_minor("BASH_MIN")
}

/// Oldest python3 the pipeline's host scripts run on (they import tomllib).
pub fn python_min() -> (u32, u32) {
    required_major_minor("PYTHON_MIN")
}

/// RAM every eval host needs, in GB.
pub fn min_ram_gb() -> u64 {
    required_number("MIN_RAM_GB")
}

/// Free disk a worker needs where builds run, in GB.
pub fn worker_min_disk_gb() -> u64 {
    required_number("WORKER_MIN_DISK_GB")
}

/// Major version from `java -version` or `java --version` output:
/// `openjdk 17.0.20.1 …`, `openjdk version "21" …`, `java version "1.8.0_392"` (8).
pub fn parse_java_major(text: &str) -> Option<u32> {
    text.lines()
        .filter(|l| !l.trim_start().starts_with("Picked up"))
        .find_map(|line| {
            let token = line
                .split_whitespace()
                .map(|t| t.trim_matches('"'))
                .find(|t| t.starts_with(|c: char| c.is_ascii_digit()))?;
            let mut parts = token
                .split(|c: char| !c.is_ascii_digit())
                .filter(|p| !p.is_empty())
                .map(|p| p.parse::<u32>().ok());
            match parts.next()?? {
                1 => parts.next()?,
                n => Some(n),
            }
        })
}

/// First `X.Y` in a version line: `Python 3.12.3`, `GNU bash, version 5.2.21(1)-release`.
pub fn parse_major_minor(text: &str) -> Option<(u32, u32)> {
    text.lines().find_map(|line| {
        line.split_whitespace()
            .map(|t| t.trim_matches(|c: char| c == '"' || c == ','))
            .filter(|t| t.starts_with(|c: char| c.is_ascii_digit()))
            .find_map(|t| {
                let mut parts = t
                    .split(|c: char| !c.is_ascii_digit())
                    .map(|p| p.parse::<u32>().ok());
                let major = parts.next()??;
                let minor = parts.next().flatten().unwrap_or(0);
                Some((major, minor))
            })
    })
}

/// Major version of the Java at `binary`, or None when it does not run.
pub fn java_major_of(binary: &Path) -> Option<u32> {
    let out = Command::new(binary).arg("-version").output().ok()?;
    if !out.status.success() {
        return None;
    }
    let text = format!(
        "{}\n{}",
        String::from_utf8_lossy(&out.stderr),
        String::from_utf8_lossy(&out.stdout)
    );
    parse_java_major(&text)
}

/// `name` the way the Jenkins agent finds it: first match on the agent's PATH.
pub fn on_agent_path(name: &str) -> Option<PathBuf> {
    std::env::split_paths(&path_env::agent_tool_path())
        .map(|dir| dir.join(name))
        .find(|p| p.is_file())
}

/// Binary and `X.Y` of `name` on the agent's PATH, from `<name> --version`.
pub fn agent_tool_version(name: &str) -> Option<(PathBuf, Option<(u32, u32)>)> {
    let bin = on_agent_path(name)?;
    let version = Command::new(&bin)
        .arg("--version")
        .output()
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| {
            let text = format!(
                "{}\n{}",
                String::from_utf8_lossy(&o.stdout),
                String::from_utf8_lossy(&o.stderr)
            );
            parse_major_minor(&text)
        });
    Some((bin, version))
}

/// Why the agent's Java cannot serve this controller, or None when it can.
/// An unreadable version is not reported here; `config` warns about it.
pub fn java_problem(path: &Path, found: Option<u32>, want: u32) -> Option<String> {
    match found {
        Some(major) if major < want => Some(format!(
            "java {major} at {}; Jenkins agents need Java {want} (JAVA_MAJOR in pipeline/config/toolchain.env)",
            path.display()
        )),
        _ => None,
    }
}

/// Why `tool` (bash or python3) on the agent PATH is not good enough, or None.
pub fn version_problem(
    tool: &str,
    found: Option<(PathBuf, Option<(u32, u32)>)>,
    want: (u32, u32),
) -> Option<String> {
    let (want_major, want_minor) = want;
    match found {
        None => Some(format!(
            "{tool} not found on the agent PATH; builds need {tool} {want_major}.{want_minor}+ ({})",
            upgrade_hint(tool)
        )),
        Some((path, Some(got))) if got < want => Some(format!(
            "{tool} {}.{} at {}; builds need {want_major}.{want_minor}+ ({})",
            got.0,
            got.1,
            path.display(),
            upgrade_hint(tool)
        )),
        _ => None,
    }
}

/// How to get a newer bash or python3 on this OS.
pub fn upgrade_hint(tool: &str) -> &'static str {
    match (tool, cfg!(target_os = "macos")) {
        ("bash", true) => "brew install bash",
        ("python3", true) => "brew install python",
        ("bash", false) => "install bash 4.4+ from your distribution",
        _ => "Ubuntu 24.04+ ships python3 3.12; otherwise install 3.11+ ahead of /usr/bin on PATH",
    }
}

/// Bash and python3 problems on the agent PATH for this worker.
pub fn host_tool_problems() -> Vec<String> {
    [("bash", bash_min()), ("python3", python_min())]
        .into_iter()
        .filter_map(|(tool, want)| version_problem(tool, agent_tool_version(tool), want))
        .collect()
}

/// macOS ships bash 3.2 and python3 3.9. Homebrew installs newer ones as this
/// user, and the agent PATH puts the Homebrew bin first.
#[cfg(target_os = "macos")]
pub fn ensure_brew_shell_tools() -> Result<()> {
    for (tool, package, want) in [("bash", "bash", bash_min()), ("python3", "python", python_min())] {
        if version_problem(tool, agent_tool_version(tool), want).is_none() {
            continue;
        }
        println!("{tool}: installing a newer one with Homebrew ({package})");
        platform::install_package(package)?;
        if let Some(problem) = version_problem(tool, agent_tool_version(tool), want) {
            return Err(Error::DependencyMissing(problem));
        }
    }
    Ok(())
}

/// Harbor binary on PATH or in `~/.local/bin`.
pub fn harbor_binary() -> Option<PathBuf> {
    discovery::which("harbor").or_else(|| {
        path_env::user_local_bin()
            .map(|d| d.join("harbor"))
            .filter(|p| p.exists())
    })
}

/// `harbor --version` of the installed binary, if any.
pub fn installed_harbor_version() -> Option<String> {
    let bin = harbor_binary()?;
    let out = Command::new(&bin).arg("--version").output().ok()?;
    if !out.status.success() {
        return None;
    }
    parse_version(&String::from_utf8_lossy(&out.stdout))
}

/// Last word of the first line: `0.22.0` and `harbor 0.22.0` both give `0.22.0`.
fn parse_version(text: &str) -> Option<String> {
    text.lines()
        .next()?
        .split_whitespace()
        .last()
        .map(str::to_string)
}

/// Install or replace Harbor so `harbor --version` equals the pin. User-level
/// only (`uv tool install` into `~/.local/bin`); never needs root.
pub fn ensure_harbor() -> Result<PathBuf> {
    path_env::ensure_user_local_bin_in_process()?;
    let want = harbor_version();
    match installed_harbor_version() {
        Some(got) if got == want => {
            println!("harbor {want}: already installed");
        }
        got => {
            ensure_uv()?;
            match got {
                Some(other) => println!("harbor {other} found; installing the pinned {want}"),
                None => println!("Installing harbor {want} with uv"),
            }
            run("uv", &["tool", "install", "--force", &format!("harbor=={want}")])?;
            path_env::ensure_user_local_bin(true)?;
            let now = installed_harbor_version();
            if now.as_deref() != Some(want) {
                return Err(Error::DependencyMissing(format!(
                    "harbor --version is {}, wanted {want} after uv tool install",
                    now.as_deref().unwrap_or("missing")
                )));
            }
        }
    }
    harbor_binary().ok_or_else(|| Error::DependencyMissing("harbor not on PATH after install".into()))
}

/// uv in `~/.local/bin`, installed with its own script (no root).
pub fn ensure_uv() -> Result<()> {
    path_env::ensure_user_local_bin_in_process()?;
    if discovery::which("uv").is_some() {
        return Ok(());
    }
    platform::install_package("uv")?;
    if let Some(home) = std::env::var_os("HOME").map(PathBuf::from) {
        let _ = path_env::prepend_to_process_path(&home.join(".local").join("bin"));
        let _ = path_env::prepend_to_process_path(&home.join(".cargo").join("bin"));
    }
    discovery::which("uv")
        .map(|_| ())
        .ok_or_else(|| Error::DependencyMissing("uv not on PATH after install".into()))
}

fn run(program: &str, args: &[&str]) -> Result<()> {
    let status = Command::new(program)
        .args(args)
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: format!("{program} {}", args.join(" ")),
            source: e.into(),
        })?;
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("{program} {}", args.join(" ")),
            source: anyhow::anyhow!("exit code {:?}", status.code()),
        });
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn pin_reads_key_value_lines_only() {
        let text = "# HARBOR_VERSION=9.9.9\nHARBOR_VERSION=0.22.0\nEMPTY=\n";
        assert_eq!(parse_pin(text, "HARBOR_VERSION"), Some("0.22.0"));
        assert_eq!(parse_pin(text, "EMPTY"), None);
        assert_eq!(parse_pin(text, "MISSING"), None);
    }

    #[test]
    fn harbor_pin_matches_what_common_sh_reads() {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        let file = std::fs::read_to_string(root.join("pipeline/config/toolchain.env")).unwrap();
        assert_eq!(parse_pin(&file, "HARBOR_VERSION"), Some(harbor_version()));
        let common = std::fs::read_to_string(root.join("pipeline/stages/_common.sh")).unwrap();
        assert!(common.contains("pipeline/config/toolchain.env"));
        assert!(
            !common.contains("HARBOR_VERSION:-"),
            "_common.sh must take the Harbor pin from toolchain.env, not a literal"
        );
    }

    #[test]
    fn version_is_last_word_of_first_line() {
        assert_eq!(parse_version("0.22.0\n").as_deref(), Some("0.22.0"));
        assert_eq!(parse_version("harbor 0.24.0\nmore").as_deref(), Some("0.24.0"));
        assert_eq!(parse_version(""), None);
    }

    fn repo_file(rel: &str) -> String {
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR"));
        std::fs::read_to_string(root.join(rel)).unwrap()
    }

    #[test]
    fn java_major_pin_matches_what_common_sh_reads() {
        let file = repo_file("pipeline/config/toolchain.env");
        assert_eq!(parse_pin(&file, "JAVA_MAJOR"), Some(java_major().to_string().as_str()));
        assert!(java_major() >= 17);
        for key in ["BASH_MIN", "PYTHON_MIN", "MIN_RAM_GB", "WORKER_MIN_DISK_GB"] {
            assert!(parse_pin(&file, key).is_some(), "{key} missing from toolchain.env");
        }
        let common = repo_file("pipeline/stages/_common.sh");
        assert!(!common.contains("MIN_RAM_GB:-8"), "RAM minimum must come from toolchain.env");
        assert!(!common.contains("MIN_DISK_GB:-40"), "disk minimum must come from toolchain.env");
    }

    #[test]
    fn parse_java_major_handles_all_version_formats() {
        let cases = [
            ("openjdk 17.0.20.1 2026-08-18\nOpenJDK Runtime Environment", Some(17)),
            ("openjdk version \"17.0.20.1\" 2026-08-18\n", Some(17)),
            ("openjdk version \"21\" 2023-09-19\n", Some(21)),
            ("java version \"1.8.0_392\"\nJava(TM) SE Runtime", Some(8)),
            ("Picked up JAVA_TOOL_OPTIONS: -Xmx1g\nopenjdk version \"21.0.4\"", Some(21)),
            ("openjdk 25 2025-09-16\n", Some(25)),
            ("", None),
            ("no version here", None),
        ];
        for (text, want) in cases {
            assert_eq!(parse_java_major(text), want, "{text:?}");
        }
    }

    #[test]
    fn parse_python_version() {
        assert_eq!(parse_major_minor("Python 3.12.3\n"), Some((3, 12)));
        assert_eq!(parse_major_minor("Python 3.9.6"), Some((3, 9)));
        assert_eq!(parse_major_minor("3.11"), Some((3, 11)));
        assert_eq!(parse_major_minor("Python"), None);
    }

    #[test]
    fn parse_bash_version() {
        let linux = "GNU bash, version 5.2.21(1)-release (x86_64-pc-linux-gnu)\nCopyright";
        assert_eq!(parse_major_minor(linux), Some((5, 2)));
        let mac = "GNU bash, version 3.2.57(1)-release (arm64-apple-darwin23)";
        assert_eq!(parse_major_minor(mac), Some((3, 2)));
        assert!(parse_major_minor(mac).unwrap() < bash_min());
        assert!(parse_major_minor(linux).unwrap() >= bash_min());
    }

    #[test]
    fn bash_min_pin_matches_run_all_guard() {
        assert_eq!(bash_min(), (4, 4));
        let common = repo_file("pipeline/stages/_common.sh");
        let guard = common.find("require_bash_min\n").expect("_common.sh calls require_bash_min");
        assert!(common.contains("BASH_VERSINFO"));
        assert!(common.contains("brew install bash"));
        for later in ["source \"$PIPELINE_LIB/parallel_degree.sh\"", "mkdir -p \"$WORKDIR\""] {
            let at = common.find(later).unwrap_or_else(|| panic!("{later} in _common.sh"));
            assert!(guard < at, "the bash guard must run before {later}");
        }
        let run_all = repo_file("pipeline/stages/run_all.sh");
        let first_cmd = run_all
            .lines()
            .find(|l| !l.trim().is_empty() && !l.starts_with('#') && !l.starts_with("set ") && !l.starts_with("DIR="))
            .unwrap();
        assert_eq!(first_cmd, "source \"$DIR/_common.sh\"", "run_all.sh sources the guard first");
    }

    #[test]
    fn old_java_is_a_problem_and_unknown_is_not() {
        let p = Path::new("/usr/bin/java");
        let msg = java_problem(p, Some(17), 21).unwrap();
        assert!(msg.contains("java 17 at /usr/bin/java"), "{msg}");
        assert!(msg.contains("need Java 21"), "{msg}");
        assert_eq!(java_problem(p, Some(21), 21), None);
        assert_eq!(java_problem(p, Some(25), 21), None);
        assert_eq!(java_problem(p, None, 21), None);
    }

    #[test]
    fn bash_and_python_below_the_pin_are_problems() {
        let bin = PathBuf::from("/bin/bash");
        let old = version_problem("bash", Some((bin.clone(), Some((3, 2)))), (4, 4)).unwrap();
        assert!(old.contains("bash 3.2 at /bin/bash"), "{old}");
        assert!(old.contains("4.4+"), "{old}");
        assert_eq!(version_problem("bash", Some((bin, Some((5, 2)))), (4, 4)), None);
        let missing = version_problem("python3", None, (3, 11)).unwrap();
        assert!(missing.contains("python3 not found"), "{missing}");
        let py = PathBuf::from("/usr/bin/python3");
        assert!(version_problem("python3", Some((py.clone(), Some((3, 9)))), (3, 11)).is_some());
        assert_eq!(version_problem("python3", Some((py, Some((3, 12)))), (3, 11)), None);
    }
}
