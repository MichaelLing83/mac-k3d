use std::path::Path;
use std::time::{Duration, Instant};

use crate::error::{Error, Result};
use crate::platform;
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
    let app_present = platform::docker_app_default()
        .map(|p| p.exists())
        .unwrap_or(false);
    if app_present || docker.exists() {
        DockerStatus::Stopped
    } else {
        DockerStatus::Missing
    }
}

/// Ensure the container runtime is starting (Docker Desktop on macOS, Engine on Linux).
pub async fn open_desktop(app: &Path) -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        let app_str = app.display().to_string();
        let target = if app.exists() {
            app_str.as_str()
        } else {
            "Docker"
        };
        println!("Starting {}…", platform::docker_display_name());
        exec::visible(Path::new("open"), &["-a", target]).await
    }
    #[cfg(target_os = "linux")]
    {
        let _ = app;
        println!("Starting {}…", platform::docker_display_name());
        // Best-effort: start the docker systemd service.
        let _ = exec::visible(Path::new("sudo"), &["systemctl", "start", "docker"]).await;
        Ok(())
    }
}

pub async fn wait_ready(docker: &Path, timeout: Duration) -> Result<()> {
    let name = platform::docker_display_name();
    let start = Instant::now();
    loop {
        if exec::capture(docker, &["info"]).await.is_ok() {
            println!("{name} is ready.");
            return Ok(());
        }
        if start.elapsed() >= timeout {
            return Err(Error::Validation(format!(
                "{name} did not become ready within {}s. {}",
                timeout.as_secs(),
                platform::docker_not_ready_hint()
            )));
        }
        tokio::time::sleep(Duration::from_secs(2)).await;
    }
}

pub async fn quit() -> Result<()> {
    #[cfg(target_os = "macos")]
    {
        println!("Quitting {}…", platform::docker_display_name());
        exec::visible(Path::new("osascript"), &["-e", "quit app \"Docker\""]).await
    }
    #[cfg(target_os = "linux")]
    {
        println!("Stopping {}…", platform::docker_display_name());
        exec::visible(Path::new("sudo"), &["systemctl", "stop", "docker"]).await
    }
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
