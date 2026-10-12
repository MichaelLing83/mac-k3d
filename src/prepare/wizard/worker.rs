//! Worker questions: the tools a Jenkins agent needs and the agent block. A
//! worker runs builds only, so there is no k3d, kubectl, cluster or LoLBench
//! question: the pipeline clones each benchmark itself.

use std::path::{Path, PathBuf};

use dialoguer::{theme::ColorfulTheme, Input, Password};

use super::prompts;
use crate::config::{
    ClusterConfig, DependenciesConfig, JenkinsAgentConfig, MacK3dConfig, NodeRole,
};
use crate::error::{Error, Result};
use crate::prepare::discovery::DiscoveredDeps;
use crate::prepare::{jenkins_agent, resources};

/// Shown in the summary, in question order. Java and Harbor are pinned, not asked.
pub(super) const TOOLS: &[&str] = &["docker", "java", "git", "harbor"];

/// Lab cloud Jenkins; type a new URL when you stand up another controller.
pub const DEFAULT_CONTROLLER_JENKINS_URL: &str = "http://43.107.42.252:17070";

pub(super) fn ask(base_dir: &Path, discovered: &DiscoveredDeps) -> Result<MacK3dConfig> {
    let docker = prompts::prompt_dependency(
        crate::platform::docker_display_name(),
        discovered.docker.as_ref(),
        true,
        true,
    )?;
    let java = prompts::pinned_java(discovered.java.as_ref());
    let git = prompts::prompt_dependency("git", discovered.git.as_ref(), true, false)?;
    let harbor = prompts::pinned_harbor();
    let agent = prompt_agent(base_dir, resources::logical_cpu_cores())?;

    Ok(MacK3dConfig {
        role: NodeRole::Worker,
        cluster: ClusterConfig {
            name: "ci-worker".into(),
            agents: 0,
            ports: prompts::default_ports(),
        },
        dependencies: DependenciesConfig {
            docker,
            k3d: prompts::not_needed(discovered.k3d.as_ref()),
            kubectl: prompts::not_needed(discovered.kubectl.as_ref()),
            helm: prompts::not_needed(discovered.helm.as_ref()),
            harbor,
            java,
            git,
        },
        jenkins_agent: agent,
        ..MacK3dConfig::default()
    })
}

fn default_worker_jenkins_url() -> String {
    for key in ["MAC_K3D_JENKINS_URL", "JENKINS_URL"] {
        if let Ok(v) = std::env::var(key) {
            let t = v.trim();
            if !t.is_empty() {
                return t.to_string();
            }
        }
    }
    DEFAULT_CONTROLLER_JENKINS_URL.into()
}

fn prompt_agent(base_dir: &Path, cpu_cores: u32) -> Result<JenkinsAgentConfig> {
    println!("\n--- Jenkins worker agent ---\n");
    println!("Detected logical CPU cores: {cpu_cores} (will register as CPU_CORES capacity)");
    println!(
        "Jenkins URL: Enter to use the current cloud controller ({DEFAULT_CONTROLLER_JENKINS_URL}),\n\
         or type http://<new-ip>:17070 when you create another controller."
    );

    let controller_url: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Jenkins controller URL")
        .default(default_worker_jenkins_url())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let api_user: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Jenkins API user (Enter to skip and fill in worker.yaml later)")
        .allow_empty(true)
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let api_token = if api_user.trim().is_empty() {
        println!(
            "Skipped. worker.yaml will keep `api_user: ''` and `api_token: ''` under jenkins_agent: for you to fill in."
        );
        None
    } else {
        let token = Password::with_theme(&ColorfulTheme::default())
            .with_prompt("Jenkins API token (stored plaintext in config for now)")
            .allow_empty_password(true)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        if token.trim().is_empty() {
            None
        } else {
            println!(
                "Note: API token will be written to config in plaintext until encryption is added."
            );
            Some(token)
        }
    };

    let agent_name: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent name")
        .default(jenkins_agent::default_agent_name())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    let labels_str: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent labels (space-separated)")
        .default(crate::platform::default_agent_labels().join(" "))
        .interact_text()
        .map_err(|_| Error::Cancelled)?;
    let labels: Vec<String> = labels_str.split_whitespace().map(str::to_string).collect();

    let remote_fs: String = Input::with_theme(&ColorfulTheme::default())
        .with_prompt("Agent remote root directory")
        .default(jenkins_agent::default_remote_fs().display().to_string())
        .interact_text()
        .map_err(|_| Error::Cancelled)?;

    Ok(JenkinsAgentConfig {
        controller_url: Some(controller_url.trim().to_string()),
        name: Some(agent_name.trim().to_string()),
        labels,
        remote_fs: Some(PathBuf::from(remote_fs.trim())),
        agent_jar: Some(base_dir.join("downloads").join("jenkins-agent").join("agent.jar")),
        cpu_cores,
        cpus_per_executor: crate::config::cpus_per_executor(0),
        api_user: Some(api_user.trim().to_string()).filter(|s| !s.is_empty()),
        api_token,
    })
}

/// What to do when a worker's YAML still has blank API keys.
pub fn blank_api_keys_hint(config_path: &Path) -> String {
    let c = config_path.display();
    format!(
        "Left jenkins_agent.api_user / api_token empty in {c}.\n\
         Fill them in (Jenkins: your name > Security > API Token), then run:\n  mac-k3d config -c {c}"
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn worker_jenkins_url_default_and_env_override() {
        let _serial = crate::test_support::global_state();
        let prev_j = std::env::var_os("JENKINS_URL");
        let prev_m = std::env::var_os("MAC_K3D_JENKINS_URL");
        std::env::remove_var("JENKINS_URL");
        std::env::remove_var("MAC_K3D_JENKINS_URL");
        assert_eq!(default_worker_jenkins_url(), DEFAULT_CONTROLLER_JENKINS_URL);
        std::env::set_var("JENKINS_URL", "http://192.0.2.9:17070");
        assert_eq!(default_worker_jenkins_url(), "http://192.0.2.9:17070");
        match prev_j {
            Some(v) => std::env::set_var("JENKINS_URL", v),
            None => std::env::remove_var("JENKINS_URL"),
        }
        match prev_m {
            Some(v) => std::env::set_var("MAC_K3D_JENKINS_URL", v),
            None => std::env::remove_var("MAC_K3D_JENKINS_URL"),
        }
    }

    #[test]
    fn blank_key_hint_names_the_keys_and_the_next_command() {
        let text = blank_api_keys_hint(Path::new("/home/toby/.config/mac-k3d/worker.yaml"));
        assert!(text.contains("jenkins_agent.api_user / api_token"));
        assert!(text.contains("mac-k3d config -c /home/toby/.config/mac-k3d/worker.yaml"));
    }
}
