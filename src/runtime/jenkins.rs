use std::path::Path;

use crate::config::MacK3dConfig;
use crate::error::Result;
use crate::runtime::exec;
use crate::runtime::kubectl;

const HELM_REPO_NAME: &str = "jenkins";
const HELM_REPO_URL: &str = "https://charts.jenkins.io";
const HELM_CHART: &str = "jenkins/jenkins";

pub async fn install_or_upgrade(helm: &Path, config: &MacK3dConfig) -> Result<()> {
    println!("Adding Helm repo '{HELM_REPO_NAME}'…");
    exec::visible(
        helm,
        &[
            "repo",
            "add",
            HELM_REPO_NAME,
            HELM_REPO_URL,
            "--force-update",
        ],
    )
    .await?;

    println!("Updating Helm repo…");
    exec::visible(helm, &["repo", "update", HELM_REPO_NAME]).await?;

    println!(
        "Installing Jenkins release '{}' in namespace '{}'…",
        config.jenkins.release_name, config.jenkins.namespace
    );
    exec::visible(
        helm,
        &[
            "upgrade",
            "--install",
            &config.jenkins.release_name,
            HELM_CHART,
            "--namespace",
            &config.jenkins.namespace,
            "--create-namespace",
            "--set",
            "controller.serviceType=LoadBalancer",
            "--wait",
            "--timeout",
            "10m",
        ],
    )
    .await
}

pub async fn admin_password(kubectl: &Path, config: &MacK3dConfig) -> Result<String> {
    kubectl::get_secret_decoded(
        kubectl,
        &config.jenkins.namespace,
        &config.jenkins.release_name,
        "jenkins-admin-password",
    )
    .await
}

pub fn ui_url(config: &MacK3dConfig) -> String {
    format!("http://localhost:{}", config.jenkins.host_port)
}
