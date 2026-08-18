use std::path::Path;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};
use crate::runtime::exec;

pub fn context_name(cluster: &str) -> String {
    format!("k3d-{cluster}")
}

pub async fn use_context(kubectl: &Path, cluster: &str) -> Result<()> {
    let context = context_name(cluster);
    println!("Selecting kubectl context '{context}'…");
    exec::visible(kubectl, &["config", "use-context", &context]).await
}

pub async fn current_context(kubectl: &Path) -> Option<String> {
    exec::capture_ok(kubectl, &["config", "current-context"])
        .await
        .map(|s| s.trim().to_string())
}

pub async fn wait_api(kubectl: &Path, timeout: Duration) -> Result<()> {
    let start = Instant::now();
    loop {
        if exec::capture(kubectl, &["cluster-info"]).await.is_ok() {
            return Ok(());
        }
        if start.elapsed() >= timeout {
            return Err(Error::Validation(format!(
                "Kubernetes API was not ready within {}s",
                timeout.as_secs()
            )));
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

pub async fn get_secret_decoded(
    kubectl: &Path,
    namespace: &str,
    secret: &str,
    key: &str,
) -> Result<String> {
    let template = format!("{{{{ index .data \"{key}\" | base64decode }}}}");
    let stdout = exec::capture(
        kubectl,
        &[
            "get",
            "secret",
            secret,
            "-n",
            namespace,
            "-o",
            &format!("go-template={template}"),
        ],
    )
    .await?;
    Ok(stdout.trim().to_string())
}

pub async fn jenkins_pod_phase(kubectl: &Path, namespace: &str) -> Option<String> {
    let stdout = exec::capture_ok(
        kubectl,
        &[
            "get",
            "pods",
            "-n",
            namespace,
            "-l",
            "app.kubernetes.io/component=jenkins-controller",
            "-o",
            "jsonpath={.items[0].status.phase}",
        ],
    )
    .await?;
    let phase = stdout.trim();
    if phase.is_empty() {
        None
    } else {
        Some(phase.to_string())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn context_name_prefixed() {
        assert_eq!(context_name("mac-k3d"), "k3d-mac-k3d");
    }
}
