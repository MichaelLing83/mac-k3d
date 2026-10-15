//! Controller questions: the cluster that hosts Jenkins, the job defaults, and
//! the CI secrets. A controller never runs Harbor or an agent.

use dialoguer::{theme::ColorfulTheme, Input, Select};

use super::prompts;
use crate::config::{
    ClusterConfig, DependenciesConfig, JenkinsConfig, JenkinsJobConfig, MacK3dConfig, NodeRole,
};
use crate::error::{Error, Result};
use crate::prepare::discovery::DiscoveredDeps;
use crate::prepare::jenkins_credentials;

pub(super) const TOOLS: &[&str] = &["docker", "k3d", "kubectl", "helm"];

pub(super) fn ask(discovered: &DiscoveredDeps) -> Result<MacK3dConfig> {
    let docker = prompts::prompt_dependency(
        crate::platform::docker_display_name(),
        discovered.docker.as_ref(),
        true,
        true,
    )?;
    let k3d = prompts::prompt_dependency("k3d", discovered.k3d.as_ref(), true, false)?;
    let kubectl = prompts::prompt_dependency("kubectl", discovered.kubectl.as_ref(), true, false)?;
    let helm = prompts::prompt_dependency("helm", discovered.helm.as_ref(), true, false)?;

    let name = prompt_text("Cluster name", "ci-controller".into())?;
    let agents: u8 = prompt_text("Number of k3d agent nodes", 0)?;
    let host_port: u16 = prompt_text("Jenkins UI host port", 17070)?;
    let jenkins_job = prompt_jenkins_job_defaults()?;

    // CI secrets go to the pending file now and to Jenkins Credentials on `config`.
    let _ = jenkins_credentials::prompt_pending_credentials();

    Ok(MacK3dConfig {
        role: NodeRole::Controller,
        cluster: ClusterConfig {
            name,
            agents,
            ports: prompts::default_ports(),
        },
        jenkins: JenkinsConfig {
            enabled: true,
            namespace: "jenkins".into(),
            release_name: "jenkins".into(),
            host_port,
            remote_trigger_token: String::new(),
        },
        dependencies: DependenciesConfig {
            docker,
            k3d,
            kubectl,
            helm,
            harbor: prompts::not_needed(discovered.harbor.as_ref()),
            java: prompts::not_needed(discovered.java.as_ref()),
            git: prompts::not_needed(discovered.git.as_ref()),
        },
        jenkins_job,
        ..MacK3dConfig::default()
    })
}

fn prompt_text<T>(label: &str, default: T) -> Result<T>
where
    T: Clone + ToString + std::str::FromStr,
    <T as std::str::FromStr>::Err: ToString,
{
    Input::with_theme(&ColorfulTheme::default())
        .with_prompt(label)
        .default(default)
        .interact_text()
        .map_err(|_| Error::Cancelled)
}

fn prompt_jenkins_job_defaults() -> Result<JenkinsJobConfig> {
    println!(
        "\nJenkins one-task eval jobs: `deepswe_one_task`, `lolbench_one_task`, and `swebenchpro_one_task`.\n\
         All use iCode + DeepSeek catalog model (default {}). Default TASK for LoLBench is ruff_1.\n\
         See docs/user-guide.md.\n\
         Secrets (DeepSeek, GitCode PAT) go to Jenkins Credentials.\n",
        crate::eval_catalog::default_model()
    );

    let mode_options = [
        "release (downloaded *-full-* binary on the worker)",
        "git (clone branch / tag / commit / pull request from github.com or gitcode.com)",
    ];
    let mode_idx = Select::with_theme(&ColorfulTheme::default())
        .with_prompt("Default ICODE_MODE")
        .items(&mode_options)
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;
    let default_eval_mode = if mode_idx == 1 { "git" } else { "release" }.to_string();

    let default_task: String = prompt_text("Default TASK (exported as TASK / ICODE_TASK)", "ruff_1".into())?;
    let default_icode_release = prompt_optional(
        "Default ICODE_RELEASE for local eval (path/URL; Jenkins release uses UI upload)",
        "",
    )?;
    let default_icode_git_url = prompt_optional("Default ICODE_GIT_URL (git mode: iCode https URL)", "")?;
    let default_icode_git_ref = prompt_optional(
        "Default ICODE_GIT_REF (git: branch, tag, commit SHA, or PR number)",
        "main",
    )?;
    let default_icode_args = prompt_optional("Default ICODE_ARGS (argv after ./icode; smoke: --help)", "")?;

    Ok(JenkinsJobConfig {
        default_task,
        default_eval_mode,
        default_icode_release,
        default_icode_git_url,
        default_icode_git_ref: if default_icode_git_ref.trim().is_empty() {
            "main".into()
        } else {
            default_icode_git_ref
        },
        default_icode_args,
        ..JenkinsJobConfig::default()
    })
}

fn prompt_optional(label: &str, default: &str) -> Result<String> {
    Input::with_theme(&ColorfulTheme::default())
        .with_prompt(label)
        .default(default.to_string())
        .allow_empty(true)
        .interact_text()
        .map_err(|_| Error::Cancelled)
}
