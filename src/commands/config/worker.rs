//! Worker `config`: check the eval toolchain, then register the Jenkins agent.
//! A worker hosts no cluster, so nothing here touches k3d or kubectl.

use std::path::{Path, PathBuf};

use super::ConfigArgs;
use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::prepare::{discovery, docker_plugins, jenkins_agent, toolchain, wizard};

pub fn run(args: &ConfigArgs, config: &MacK3dConfig, config_path: &Path) -> Result<()> {
    check_toolchain(config, config_path);
    if args.skip_agent {
        return Ok(());
    }
    if crate::platform::host_os() == crate::platform::HostOs::Macos {
        let docker_cpus = crate::prepare::resources::docker_vm_limits().map(|(cpus, _)| cpus);
        if let Some(problem) =
            crate::prepare::resources::cpu_cores_problem(config.jenkins_agent.cpu_cores, docker_cpus)
        {
            return Err(crate::error::Error::Validation(problem));
        }
    }
    let mut config = config.clone();
    if let Some(old) = jenkins_agent::legacy_mac_name(&config) {
        let new_name = jenkins_agent::default_agent_name();
        println!("Renaming Jenkins agent '{old}' to '{new_name}'…");
        let _ = crate::prepare::agent_service::stop_and_uninstall();
        if let Some(url) = config.jenkins_agent.controller_url.as_deref() {
            let user = config.jenkins_agent.api_user();
            let token = config.jenkins_agent.api_token();
            if let Err(err) = jenkins_agent::delete_node(url, &old, user, token) {
                println!(
                    "Warning: could not delete Jenkins agent '{old}' ({err}). Remove it under Manage Jenkins → Nodes if it remains."
                );
            }
            let cores = if config.jenkins_agent.cpu_cores > 0 {
                config.jenkins_agent.cpu_cores
            } else {
                crate::prepare::resources::logical_cpu_cores()
            };
            if let Err(err) = crate::prepare::resources::remove_agent_cpu_cores(
                url,
                &old,
                &config.resources.cpu_cores_label,
                cores,
                user,
                token,
            ) {
                println!(
                    "Warning: could not remove '{old}-core-N' ({err}). Remove those locks in Jenkins if they remain."
                );
            }
        }
        config.jenkins_agent.name = Some(new_name.clone());
        match config.save(Some(config_path)) {
            Ok(()) => println!("Agent name {old} -> {new_name} (saved in {}).", config_path.display()),
            Err(err) => println!(
                "Warning: could not write the new agent name to {} ({err}). The node is still registered as {new_name} this run.",
                config_path.display()
            ),
        }
    }
    if config.jenkins_agent.api_credentials().is_none() {
        println!("{}", wizard::blank_api_keys_hint(config_path));
    }
    println!("Ensuring Jenkins agent registration…");
    jenkins_agent::ensure_worker_agent(&config)
}

/// Warn only: the env phase of every build fails on a wrong Harbor anyway, and
/// the agent should still come up so that failure is visible in Jenkins.
fn check_toolchain(config: &MacK3dConfig, config_path: &Path) {
    let fix = format!(
        "run `mac-k3d setup -c {}` and choose \"Use existing config\"",
        config_path.display()
    );
    let want = toolchain::harbor_version();
    match toolchain::installed_harbor_version() {
        Some(got) if got == want => println!("harbor {want}: ok"),
        Some(got) => println!("Warning: harbor {got} is installed but builds need {want}; {fix}."),
        None => println!("Warning: harbor not found (builds need {want}); {fix}."),
    }
    println!("{}", java_line(&jenkins_agent::agent_java(config), &fix));
    for problem in toolchain::host_tool_problems() {
        println!("Warning: {problem}.");
    }
    if discovery::which("git").is_none() {
        println!("Warning: git not found; the tasks phase clones benchmarks with it; {fix}.");
    }
    for warning in crate::platform::worker_host_warnings() {
        println!("Warning: {warning}.");
    }
    let docker = config
        .dependencies
        .docker
        .binary
        .clone()
        .unwrap_or_else(|| PathBuf::from("docker"));
    for plugin in docker_plugins::PLUGINS {
        match docker_plugins::installed_version(&docker, plugin) {
            Some(v) => println!("docker {}: {v}", plugin.name()),
            None => println!(
                "Warning: docker {} missing; the env phase downloads {} at the next build ({}).",
                plugin.name(),
                plugin.pinned_version(),
                docker_plugins::missing_hint(plugin)
            ),
        }
    }
}

/// `java 21: ok`, or why the controller will refuse this agent.
fn java_line(java: &str, fix: &str) -> String {
    let want = toolchain::java_major();
    let bin = Path::new(java);
    match toolchain::java_major_of(bin) {
        Some(got) if got >= want => format!("java {want}: ok ({got} at {java})"),
        got => {
            let problem = toolchain::java_problem(bin, got, want).unwrap_or_else(|| {
                format!("could not read the version of {java}; Jenkins agents need Java {want}")
            });
            format!("Warning: {problem}; the agent connects, then fails with UnsupportedClassVersionError; {fix}.")
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Exec can hit ETXTBSY while another test thread forks with the write fd
    /// still open, so wait until the script actually runs.
    fn fake_java(dir: &Path, version: &str) -> String {
        use std::os::unix::fs::PermissionsExt;
        let bin = dir.join(format!("java-{version}"));
        std::fs::write(&bin, format!("#!/bin/sh\necho 'openjdk version \"{version}\"' >&2\n")).unwrap();
        std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
        for _ in 0..100 {
            if toolchain::java_major_of(&bin).is_some() {
                return bin.display().to_string();
            }
            std::thread::sleep(std::time::Duration::from_millis(20));
        }
        panic!("fake java {} never ran", bin.display());
    }

    #[test]
    fn config_reports_the_agent_java_against_the_pin() {
        let dir = tempfile::tempdir().unwrap();
        let want = toolchain::java_major();
        let fix = "run `mac-k3d setup -c w.yaml` and choose \"Use existing config\"";

        let ok = java_line(&fake_java(dir.path(), &format!("{want}.0.4")), fix);
        assert!(ok.starts_with(&format!("java {want}: ok")), "{ok}");

        let old = java_line(&fake_java(dir.path(), "17.0.20.1"), fix);
        assert!(old.starts_with("Warning: java 17 at "), "{old}");
        assert!(old.contains("UnsupportedClassVersionError"), "{old}");
        assert!(old.contains("Use existing config"), "{old}");

        let missing = java_line("/nonexistent/java", fix);
        assert!(missing.contains("could not read the version"), "{missing}");
    }
}
