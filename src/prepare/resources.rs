use std::path::Path;
use std::process::Command;

use crate::error::{Error, Result};

const GB: u64 = 1024 * 1024 * 1024;

/// Logical CPU count for CPU_CORES registration.
pub fn logical_cpu_cores() -> u32 {
    Command::new("sysctl")
        .args(["-n", "hw.logicalcpu"])
        .output()
        .ok()
        .and_then(|o| {
            String::from_utf8_lossy(&o.stdout)
                .trim()
                .parse()
                .ok()
        })
        .or_else(|| {
            Command::new("sysctl")
                .args(["-n", "hw.ncpu"])
                .output()
                .ok()
                .and_then(|o| {
                    String::from_utf8_lossy(&o.stdout)
                        .trim()
                        .parse()
                        .ok()
                })
        })
        .unwrap_or(1)
}

/// Ensure free space on the volume containing `path` is at least `min_gb`.
pub fn ensure_disk_min(path: &Path, min_gb: u64) -> Result<()> {
    let check_path = if path.exists() {
        path
    } else {
        path.parent().unwrap_or(Path::new("/"))
    };
    let available = crate::prepare::volumes::available_bytes(check_path).unwrap_or(0);
    let need = min_gb.saturating_mul(GB);
    if available < need {
        return Err(Error::Validation(format!(
            "only {} free at {}; need at least {} GB",
            crate::prepare::volumes::format_bytes(available),
            check_path.display(),
            min_gb
        )));
    }
    tracing::info!(
        path = %check_path.display(),
        free = %crate::prepare::volumes::format_bytes(available),
        min_gb,
        "disk space check passed"
    );
    Ok(())
}

/// Controller: ensure Lockable Resources label exists (API when Jenkins reachable).
pub fn ensure_cpu_cores_label_on_controller(jenkins_url: &str, label: &str) -> Result<()> {
    tracing::info!(
        %label,
        "controller: ensure Jenkins Lockable Resources label"
    );
    println!(
        "\nOn the Jenkins controller ({jenkins_url}):\n\
         1. Confirm Lockable Resources plugin is installed (mac-k3d Helm install adds it).\n\
         2. Manage Jenkins → Lockable Resources → create capacity under label '{label}'.\n\
         Worker Macs register intended capacity under this label during prepare.\n"
    );
    Ok(())
}

/// Worker: register CPU_CORES quantity on controller for this agent.
///
/// Lockable Resources plugin REST is version-sensitive; print exact UI / Groovy steps.
pub fn register_agent_cpu_cores(
    jenkins_url: &str,
    agent_name: &str,
    label: &str,
    cores: u32,
) -> Result<()> {
    tracing::info!(
        agent = agent_name,
        %label,
        cores,
        "worker: register CPU_CORES capacity"
    );
    println!(
        "\nOn the Jenkins controller ({jenkins_url}), create Lockable Resources totaling {cores} under label '{label}' for agent '{agent_name}'.\n\
         Example: {cores} resources named {agent_name}-core-{{1..{cores}}} with labels '{label} {agent_name}',\n\
         or one resource with quantity={cores} if your plugin version supports it.\n\
         Jobs should lock(label: '{label}', quantity: N) for N cores.\n"
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logical_cpu_cores_nonzero() {
        assert!(logical_cpu_cores() >= 1);
    }
}
