use std::path::Path;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};
use crate::runtime::exec;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DockerStatus {
    Running,
    Stopped,
    Missing,
}

pub async fn status(docker: &Path) -> DockerStatus {
    if exec::capture(docker, &["info"]).await.is_ok() {
        return DockerStatus::Running;
    }
    if Path::new("/Applications/Docker.app").exists() || docker.exists() {
        DockerStatus::Stopped
    } else {
        DockerStatus::Missing
    }
}

pub async fn open_desktop(app: &Path) -> Result<()> {
    let app_str = app.display().to_string();
    let target = if app.exists() {
        app_str.as_str()
    } else {
        "Docker"
    };
    println!("Starting Docker Desktop…");
    exec::visible(Path::new("open"), &["-a", target]).await
}

pub async fn wait_ready(docker: &Path, timeout: Duration) -> Result<()> {
    let start = Instant::now();
    loop {
        if exec::capture(docker, &["info"]).await.is_ok() {
            println!("Docker Desktop is ready.");
            return Ok(());
        }
        if start.elapsed() >= timeout {
            return Err(Error::Validation(format!(
                "Docker Desktop did not become ready within {}s",
                timeout.as_secs()
            )));
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

pub async fn quit() -> Result<()> {
    println!("Quitting Docker Desktop…");
    exec::visible(
        Path::new("osascript"),
        &["-e", "quit app \"Docker\""],
    )
    .await
}

impl std::fmt::Display for DockerStatus {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            Self::Running => write!(f, "running"),
            Self::Stopped => write!(f, "stopped"),
            Self::Missing => write!(f, "not installed"),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn display_status() {
        assert_eq!(DockerStatus::Running.to_string(), "running");
        assert_eq!(DockerStatus::Stopped.to_string(), "stopped");
    }
}
