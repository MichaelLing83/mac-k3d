//! Linux platform adapter: Docker Engine, apt, systemd --user, /mnt|/media.

use std::path::{Path, PathBuf};
use std::process::Command;

use crate::error::{Error, Result};

const SYSTEMD_UNIT: &str = "mac-k3d-jenkins-agent.service";

pub fn docker_display_name() -> &'static str {
    "Docker Engine"
}

pub fn package_manager_label() -> &'static str {
    "apt"
}

pub fn role_prompt() -> &'static str {
    "What is this machine's role?"
}

pub fn machine_noun() -> &'static str {
    "machine"
}

pub fn default_agent_labels() -> Vec<String> {
    vec!["linux".into(), "docker".into(), "lolbench".into()]
}

pub fn agent_daemon_label() -> &'static str {
    SYSTEMD_UNIT
}

pub fn scan_extra_mount_roots() -> Vec<PathBuf> {
    let mut roots = Vec::new();
    for base in ["/mnt", "/media"] {
        let base = PathBuf::from(base);
        if let Ok(entries) = std::fs::read_dir(&base) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    roots.push(path);
                }
            }
        }
    }
    if let Ok(mounts) = std::fs::read_to_string("/proc/mounts") {
        for line in mounts.lines() {
            let mut parts = line.split_whitespace();
            let _src = parts.next();
            if let Some(target) = parts.next() {
                if target.starts_with("/mnt/")
                    || target.starts_with("/media/")
                    || target.starts_with("/data")
                {
                    let p = PathBuf::from(target);
                    if p.is_dir() && !roots.contains(&p) {
                        roots.push(p);
                    }
                }
            }
        }
    }
    roots
}

pub fn docker_app_default() -> Option<PathBuf> {
    None
}

pub fn requires_docker_app() -> bool {
    false
}

pub fn agent_path_first() -> Vec<&'static str> {
    Vec::new()
}

pub fn agent_path_extras() -> Vec<&'static str> {
    vec![
        "/usr/local/bin",
        "/usr/bin",
        "/bin",
        "/usr/sbin",
        "/sbin",
        "/snap/bin",
    ]
}

pub fn preferred_shell_rc_default(home: &Path) -> PathBuf {
    let bashrc = home.join(".bashrc");
    if bashrc.exists() {
        bashrc
    } else {
        home.join(".profile")
    }
}

pub fn package_install_available() -> bool {
    which("apt-get").is_some() || which("apt").is_some()
}

pub fn install_package(name: &str) -> Result<()> {
    if !package_install_available() {
        return Err(Error::DependencyMissing(format!(
            "apt not found; install {name} manually (Ubuntu/Debian supported for auto-install)"
        )));
    }
    match name {
        "java" => apt_install(&[java_package().as_str()])?,
        "python" => apt_install(&["python3"])?,
        "docker" => install_docker_engine()?,
        "k3d" => install_k3d_curl()?,
        "kubectl" => apt_install(&["kubectl"]).or_else(|_| install_kubectl_curl())?,
        "helm" => install_helm_curl()?,
        "uv" => install_uv_curl()?,
        "git" => apt_install(&["git"])?,
        other => {
            return Err(Error::Config(format!(
                "unknown dependency for apt/curl install: {other}"
            )));
        }
    }
    Ok(())
}

pub fn harbor_bootstrap_hint() -> &'static str {
    "neither uv nor pipx found; install will try curl → uv first"
}

/// JRE package matching the controller's Java (JAVA_MAJOR in toolchain.env).
fn java_package() -> String {
    format!(
        "openjdk-{}-jre-headless",
        crate::prepare::toolchain::java_major()
    )
}

pub fn root_install_command(name: &str) -> Option<String> {
    let java = format!("apt-get install -y {}", java_package());
    let cmd = match name {
        "java" => java.as_str(),
        "python" => "apt-get install -y python3",
        "git" => "apt-get install -y git",
        "docker" => "apt-get install -y docker.io docker-compose-v2 && systemctl enable --now docker",
        "kubectl" => "apt-get install -y kubectl",
        "k3d" => "curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash",
        "helm" => "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
        _ => return None,
    };
    Some(cmd.to_string())
}

/// Root, passwordless sudo, or a sudo password typed now. A user outside
/// sudoers fails `sudo -v` at once, so this never hangs on a non-sudoer.
pub fn can_elevate() -> bool {
    if is_root() {
        return true;
    }
    if which("sudo").is_none() {
        return false;
    }
    let quiet = Command::new("sudo")
        .args(["-n", "true"])
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if quiet {
        return true;
    }
    if !atty::is(atty::Stream::Stdin) {
        return false;
    }
    println!("Some installs need sudo. Enter your password, or press Ctrl-C if you have none.");
    Command::new("sudo")
        .arg("-v")
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

/// systemd with linger runs the agent without a login session; nothing to warn about.
pub fn worker_host_warnings() -> Vec<String> {
    Vec::new()
}

pub fn worker_host_root_steps() -> Vec<super::RootStep> {
    let user = current_user();
    if user == "unknown" || user == "root" {
        return Vec::new();
    }
    let mut steps = Vec::new();
    let docker_ok = Command::new("docker")
        .arg("info")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if which("docker").is_some() && !docker_ok {
        let active = Command::new("systemctl")
            .args(["is-active", "--quiet", "docker"])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        if !active {
            steps.push(super::RootStep {
                why: "start Docker now and at boot".into(),
                command: "systemctl enable --now docker".into(),
            });
        }
        if !user_in_group(&user, "docker") {
            steps.push(super::RootStep {
                why: format!("let {user} use Docker without sudo (then log out and back in)"),
                command: format!("usermod -aG docker {user}"),
            });
        }
    }
    let linger = Command::new("loginctl")
        .args(["show-user", &user, "--property=Linger"])
        .output()
        .map(|o| String::from_utf8_lossy(&o.stdout).trim().to_string())
        .unwrap_or_default();
    if linger != "Linger=yes" {
        steps.push(super::RootStep {
            why: "keep the Jenkins agent running after you log out".into(),
            command: format!("loginctl enable-linger {user}"),
        });
    }
    if crate::prepare::toolchain::on_agent_path("python3").is_none() {
        steps.push(super::RootStep {
            why: "python3 for the pipeline's host scripts".into(),
            command: "apt-get install -y python3".into(),
        });
    }
    steps
}

pub fn run_as_root(command: &str) -> Result<()> {
    if is_root() {
        run_cmd("bash", &["-c", command])
    } else {
        run_cmd("sudo", &["bash", "-c", command])
    }
}

fn user_in_group(user: &str, group: &str) -> bool {
    Command::new("id")
        .args(["-nG", user])
        .output()
        .map(|o| {
            String::from_utf8_lossy(&o.stdout)
                .split_whitespace()
                .any(|g| g == group)
        })
        .unwrap_or(false)
}

fn which(name: &str) -> Option<PathBuf> {
    let path_var = std::env::var_os("PATH")?;
    std::env::split_paths(&path_var).find_map(|dir| {
        let candidate = dir.join(name);
        candidate.exists().then_some(candidate)
    })
}

fn apt_bin() -> &'static str {
    if which("apt-get").is_some() {
        "apt-get"
    } else {
        "apt"
    }
}

fn apt_install(packages: &[&str]) -> Result<()> {
    tracing::info!(?packages, "installing via apt");
    let apt = apt_bin();
    let use_sudo = Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|o| {
            String::from_utf8_lossy(&o.stdout)
                .trim()
                .parse::<u32>()
                .ok()
        })
        .unwrap_or(1)
        != 0;

    if use_sudo {
        let mut args = vec![apt, "install", "-y"];
        args.extend_from_slice(packages);
        run_cmd("sudo", &args)
    } else {
        let mut args = vec!["install", "-y"];
        args.extend_from_slice(packages);
        run_cmd(apt, &args)
    }
}

fn current_user() -> String {
    std::env::var("USER")
        .or_else(|_| std::env::var("LOGNAME"))
        .unwrap_or_else(|_| "unknown".into())
}

fn install_docker_engine() -> Result<()> {
    if let Err(e) = apt_install(&["docker.io"]) {
        tracing::warn!(error = %e, "apt install docker.io failed");
        return Err(Error::DependencyMissing(
            "failed to install Docker Engine via apt; install docker.io or docker-ce, \
             then add your user to the docker group: sudo usermod -aG docker $USER"
                .into(),
        ));
    }
    if let Err(e) = apt_install(&["docker-compose-v2"]) {
        tracing::warn!(
            error = %e,
            "apt install docker-compose-v2 failed (P0 can install a user plugin)"
        );
    }
    let _ = Command::new("sudo")
        .args(["systemctl", "enable", "--now", "docker"])
        .status();
    let user = current_user();
    if user != "unknown" && user != "root" {
        let _ = run_cmd("sudo", &["usermod", "-aG", "docker", &user]);
        let _ = Command::new("loginctl")
            .args(["enable-linger", &user])
            .status();
    }
    let info_ok = Command::new("docker")
        .arg("info")
        .output()
        .map(|o| o.status.success())
        .unwrap_or(false);
    if !info_ok {
        return Err(Error::Validation(format!(
            "Docker Engine is installed, but this shell cannot talk to it yet.\n{}",
            docker_not_ready_hint()
        )));
    }
    Ok(())
}

pub fn docker_not_ready_hint() -> &'static str {
    if is_root() {
        "As root: sudo systemctl enable --now docker, then retry `docker info` / `mac-k3d setup`."
    } else {
        "Log out of the desktop completely, log back in, then run `mac-k3d setup` again \
         so your docker group applies. Check: groups | grep docker; sudo systemctl status docker"
    }
}

fn is_root() -> bool {
    Command::new("id")
        .arg("-u")
        .output()
        .ok()
        .and_then(|o| {
            String::from_utf8_lossy(&o.stdout)
                .trim()
                .parse::<u32>()
                .ok()
        })
        .unwrap_or(1)
        == 0
}

fn install_k3d_curl() -> Result<()> {
    tracing::info!("installing k3d via official install script");
    run_shell("curl -s https://raw.githubusercontent.com/k3d-io/k3d/main/install.sh | bash")
}

fn install_kubectl_curl() -> Result<()> {
    tracing::info!("installing kubectl via curl");
    run_shell(
        "curl -fsSL -o /tmp/kubectl \"https://dl.k8s.io/release/$(curl -L -s https://dl.k8s.io/release/stable.txt)/bin/linux/amd64/kubectl\" \
         && chmod +x /tmp/kubectl \
         && sudo mv /tmp/kubectl /usr/local/bin/kubectl",
    )
}

fn install_helm_curl() -> Result<()> {
    tracing::info!("installing helm via get-helm-3");
    run_shell(
        "curl -fsSL https://raw.githubusercontent.com/helm/helm/main/scripts/get-helm-3 | bash",
    )
}

fn install_uv_curl() -> Result<()> {
    tracing::info!("installing uv via astral install script");
    run_shell("curl -LsSf https://astral.sh/uv/install.sh | sh")
}

fn run_shell(script: &str) -> Result<()> {
    let status = Command::new("bash")
        .args(["-lc", script])
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: script.into(),
            source: e.into(),
        })?;
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: script.into(),
            source: anyhow::anyhow!("exit code {:?}", status.code()),
        });
    }
    Ok(())
}

fn run_cmd(program: &str, args: &[&str]) -> Result<()> {
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

/// systemd user unit. `OOMScoreAdjust=-1000` makes the host out-of-memory killer
/// skip this process, so a full machine does not take the Jenkins node offline.
fn agent_unit_text(working_dir: &Path, path_env: &str, launch_script: &Path, log_out: &Path) -> String {
    format!(
        r#"[Unit]
Description=mac-k3d Jenkins agent
After=network-online.target

[Service]
Type=simple
WorkingDirectory={cwd}
Environment=PATH={path}
ExecStart=/bin/bash {script}
Restart=always
RestartSec=5
OOMScoreAdjust=-1000
StandardOutput=append:{stdout}
StandardError=append:{stdout}

[Install]
WantedBy=default.target
"#,
        cwd = working_dir.display(),
        path = path_env.replace('%', "%%").replace(' ', "\\x20"),
        script = launch_script.display(),
        stdout = log_out.display(),
    )
}

fn unit_path() -> PathBuf {
    std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."))
        .join(".config/systemd/user")
        .join(SYSTEMD_UNIT)
}

pub fn install_agent_daemon(
    launch_script: &Path,
    working_dir: &Path,
    path_env: &str,
    changed: bool,
) -> Result<()> {
    if !launch_script.exists() {
        return Err(Error::Config(format!(
            "launch script not found: {}",
            launch_script.display()
        )));
    }

    if !crate::prepare::jenkins_agent::has_agent_secret(launch_script) {
        println!(
            "{} still has the REPLACE_ME secret — not starting systemd unit.\n\
             Re-run config with a valid API token, or paste the secret there, then run config again.",
            crate::prepare::jenkins_agent::secret_file_for(launch_script).display()
        );
        return Ok(());
    }

    let unit = unit_path();
    if let Some(parent) = unit.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| Error::Config(format!("failed to create {}: {e}", parent.display())))?;
    }

    let log_out = working_dir.join("jenkins-agent.stdout.log");
    let content = agent_unit_text(working_dir, path_env, launch_script, &log_out);

    let unit_changed = std::fs::read_to_string(&unit).ok().as_deref() != Some(content.as_str());
    if unit_changed {
        std::fs::write(&unit, content)
            .map_err(|e| Error::Config(format!("failed to write {}: {e}", unit.display())))?;
    }

    run_cmd("systemctl", &["--user", "daemon-reload"])?;
    let running = Command::new("systemctl")
        .args(["--user", "is-active", "--quiet", SYSTEMD_UNIT])
        .status()
        .map(|s| s.success())
        .unwrap_or(false);
    match super::agent_action(running, changed || unit_changed) {
        super::AgentAction::Start => {
            run_cmd("systemctl", &["--user", "enable", "--now", SYSTEMD_UNIT])?;
            println!(
                "Jenkins agent systemd user unit started ({SYSTEMD_UNIT}).\n\
                 Logs: {}\n\
                 Tip: run `loginctl enable-linger \"$USER\"` so the agent survives logout.\n\
                 Stop with teardown/clean.",
                log_out.display()
            );
        }
        super::AgentAction::Restart => {
            run_cmd("systemctl", &["--user", "enable", SYSTEMD_UNIT])?;
            run_cmd("systemctl", &["--user", "restart", SYSTEMD_UNIT])?;
            println!(
                "Jenkins agent restarted ({SYSTEMD_UNIT}): agent.jar, launch-agent.sh, .agent-secret or the unit changed.\n\
                 Logs: {}",
                log_out.display()
            );
        }
        super::AgentAction::LeaveRunning => {
            run_cmd("systemctl", &["--user", "enable", SYSTEMD_UNIT])?;
            println!("Jenkins agent unchanged, left running ({SYSTEMD_UNIT}).");
        }
    }
    Ok(())
}

pub fn stop_agent_daemon() -> Result<()> {
    let unit = unit_path();
    let _ = Command::new("systemctl")
        .args(["--user", "disable", "--now", SYSTEMD_UNIT])
        .status();
    if unit.exists() {
        std::fs::remove_file(&unit)
            .map_err(|e| Error::Config(format!("failed to remove {}: {e}", unit.display())))?;
        let _ = Command::new("systemctl")
            .args(["--user", "daemon-reload"])
            .status();
        println!("Removed systemd unit {}", unit.display());
    } else {
        println!("Jenkins agent systemd unit not installed; nothing to stop.");
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn labels_include_linux() {
        assert!(default_agent_labels().contains(&"linux".into()));
    }

    #[test]
    fn unit_name() {
        assert!(unit_path()
            .to_string_lossy()
            .contains("mac-k3d-jenkins-agent.service"));
    }

    #[test]
    fn the_agent_unit_is_never_chosen_by_the_oom_killer() {
        let text = agent_unit_text(
            Path::new("/var/lib/jenkins-agent"),
            "/usr/bin",
            Path::new("/var/lib/jenkins-agent/launch-agent.sh"),
            Path::new("/var/lib/jenkins-agent/jenkins-agent.stdout.log"),
        );
        assert!(text.contains("OOMScoreAdjust=-1000\n"));
        assert!(text.contains("Restart=always\n"));
    }

    #[test]
    fn docker_hint_mentions_logout() {
        let h = docker_not_ready_hint();
        assert!(
            h.contains("Log out") || h.contains("systemctl enable --now docker"),
            "{h}"
        );
        assert!(
            h.contains("mac-k3d setup") || h.contains("docker info"),
            "{h}"
        );
    }
}
