use std::path::{Path, PathBuf};
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

/// Controller prepare hint (plugin is installed via Helm; capacity is created per worker).
pub fn ensure_cpu_cores_label_on_controller(jenkins_url: &str, label: &str) -> Result<()> {
    tracing::info!(%label, "controller: Lockable Resources plugin expected via Helm");
    println!(
        "\nController ({jenkins_url}): Lockable Resources plugin is installed with Jenkins.\n\
         Worker `prepare`/`config` will create '{label}' capacity automatically when API credentials are set.\n"
    );
    Ok(())
}

/// Create Lockable Resources on the controller totaling `cores` under `label` for this agent.
///
/// Uses Jenkins Script Console (`/scriptText`) + `LockableResourcesManager.createResourceWithLabel`
/// so it works across plugin versions. Requires API user/token with Overall/Administer (script) permission.
pub fn register_agent_cpu_cores(
    jenkins_url: &str,
    agent_name: &str,
    label: &str,
    cores: u32,
    api_user: Option<&str>,
    api_token: Option<&str>,
) -> Result<()> {
    tracing::info!(
        agent = agent_name,
        %label,
        cores,
        "worker: register CPU_CORES capacity"
    );

    let cores = cores.max(1);
    let (Some(user), Some(token)) = (api_user, api_token) else {
        println!(
            "\nNo Jenkins API token — skipping Lockable Resources create.\n\
             On {jenkins_url}, create {cores} resources named {agent_name}-core-1..{cores}\n\
             with labels '{label} {agent_name}'.\n\
             Or re-run prepare/config with api_user/api_token set.\n"
        );
        return Ok(());
    };

    println!(
        "Creating {cores} Lockable Resources on {jenkins_url} (label '{label}', agent '{agent_name}')…"
    );

    let script = groovy_create_cpu_cores(agent_name, label, cores);
    match run_script_text(jenkins_url, user, token, &script) {
        Ok(output) => {
            let created = output.lines().filter(|l| l.starts_with("created ")).count();
            let existed = output.lines().filter(|l| l.starts_with("exists ")).count();
            println!(
                "Lockable Resources: {created} created, {existed} already present (label '{label}')."
            );
            if !output.trim().is_empty() {
                for line in output.lines().take(12) {
                    println!("  {line}");
                }
            }
            Ok(())
        }
        Err(err) => {
            println!(
                "Warning: could not create Lockable Resources via API ({err}).\n\
                 Create manually: Manage Jenkins → Lockable Resources → {cores} × '{agent_name}-core-N' labels '{label} {agent_name}'."
            );
            Ok(())
        }
    }
}

/// Delete Lockable Resources `{agent}-core-*` created for this worker.
pub fn remove_agent_cpu_cores(
    jenkins_url: &str,
    agent_name: &str,
    label: &str,
    cores: u32,
    api_user: Option<&str>,
    api_token: Option<&str>,
) -> Result<()> {
    let cores = cores.max(1);
    let (Some(user), Some(token)) = (api_user, api_token) else {
        println!(
            "No Jenkins API token — skipping Lockable Resources delete for '{agent_name}'.\n\
             Remove '{agent_name}-core-*' (label '{label}') manually if needed."
        );
        return Ok(());
    };

    println!(
        "Removing Lockable Resources for '{agent_name}' on {jenkins_url}…"
    );
    let script = groovy_delete_cpu_cores(agent_name, cores);
    match run_script_text(jenkins_url, user, token, &script) {
        Ok(output) => {
            let deleted = output.lines().filter(|l| l.starts_with("deleted ")).count();
            println!("Lockable Resources: {deleted} deleted for '{agent_name}'.");
            Ok(())
        }
        Err(err) => {
            println!(
                "Warning: could not delete Lockable Resources via API ({err}).\n\
                 Remove '{agent_name}-core-1..{cores}' manually if they remain."
            );
            Ok(())
        }
    }
}

fn groovy_delete_cpu_cores(agent_name: &str, cores: u32) -> String {
    let agent = groovy_escape(agent_name);
    format!(
        r#"
import org.jenkins.plugins.lockableresources.LockableResourcesManager
def m = LockableResourcesManager.get()
def agent = '{agent}'
def cores = {cores}
def names = (1..cores).collect {{ i -> "${{agent}}-core-${{i}}" }}
names.each {{ name ->
  def r = m.fromName(name)
  if (r != null) {{
    m.resources.remove(r)
    println("deleted " + name)
  }} else {{
    println("missing " + name)
  }}
}}
m.save()
println("done")
"#
    )
}

fn groovy_create_cpu_cores(agent_name: &str, label: &str, cores: u32) -> String {
    // Escape for embedding in a Groovy single-quoted / GString-safe literals.
    let agent = groovy_escape(agent_name);
    let labels = groovy_escape(&format!("{label} {agent_name}"));
    format!(
        r#"
import org.jenkins.plugins.lockableresources.LockableResourcesManager
def m = LockableResourcesManager.get()
def agent = '{agent}'
def labels = '{labels}'
def cores = {cores}
(1..cores).each {{ i ->
  def name = "${{agent}}-core-${{i}}"
  if (m.fromName(name) == null) {{
    m.createResourceWithLabel(name, labels)
    println("created " + name)
  }} else {{
    println("exists " + name)
  }}
}}
m.save()
println("done")
"#
    )
}

fn groovy_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'")
}

fn run_script_text(base_url: &str, user: &str, token: &str, script: &str) -> Result<String> {
    let base = base_url.trim_end_matches('/');
    let auth = format!("{user}:{token}");
    let cookie_file = tempfile_path("mac-k3d-lr-cookies")?;
    let crumb = fetch_crumb(base, &auth, &cookie_file);

    let mut cmd = Command::new("curl");
    cmd.args([
        "-sS",
        "-b",
        &cookie_file.display().to_string(),
        "-c",
        &cookie_file.display().to_string(),
        "-u",
        &auth,
        "-X",
        "POST",
        &format!("{base}/scriptText"),
        "--data-urlencode",
        &format!("script={script}"),
    ]);
    if let Some((field, value)) = &crumb {
        cmd.args(["-H", &format!("{field}: {value}")]);
    }

    let output = cmd.output().map_err(|e| Error::CommandFailed {
        cmd: "curl scriptText".into(),
        source: e.into(),
    })?;
    let _ = std::fs::remove_file(&cookie_file);

    let body = String::from_utf8_lossy(&output.stdout).to_string();
    if !output.status.success() {
        return Err(Error::CommandFailed {
            cmd: "curl scriptText".into(),
            source: anyhow::anyhow!("exit {:?} body={}", output.status.code(), truncate(&body, 200)),
        });
    }
    if body.contains("No such property")
        || body.contains("unable to resolve class")
        || body.contains("Unauthorized")
        || body.contains("Forbidden")
    {
        return Err(Error::Config(truncate(&body, 300)));
    }
    Ok(body)
}

fn tempfile_path(prefix: &str) -> Result<PathBuf> {
    let path = std::env::temp_dir().join(format!("{prefix}-{}", std::process::id()));
    std::fs::File::create(&path).map_err(|e| Error::Config(e.to_string()))?;
    Ok(path)
}

fn fetch_crumb(base: &str, auth: &str, cookie_file: &Path) -> Option<(String, String)> {
    let output = Command::new("curl")
        .args([
            "-fsS",
            "-b",
            &cookie_file.display().to_string(),
            "-c",
            &cookie_file.display().to_string(),
            "-u",
            auth,
            &format!("{base}/crumbIssuer/api/json"),
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let field = extract_json_str(&text, "crumbRequestField")?;
    let crumb = extract_json_str(&text, "crumb")?;
    Some((field, crumb))
}

fn extract_json_str(json: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let idx = json.find(&needle)?;
    let after = &json[idx + needle.len()..];
    let colon = after.find(':')?;
    let rest = after[colon + 1..].trim_start();
    if !rest.starts_with('"') {
        return None;
    }
    let rest = &rest[1..];
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

fn truncate(s: &str, max: usize) -> String {
    let t: String = s.chars().take(max).collect();
    if s.chars().count() > max {
        format!("{t}…")
    } else {
        t
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn logical_cpu_cores_nonzero() {
        assert!(logical_cpu_cores() >= 1);
    }

    #[test]
    fn groovy_mentions_create_and_label() {
        let g = groovy_create_cpu_cores("mac-host", "CPU_CORES", 4);
        assert!(g.contains("createResourceWithLabel"));
        assert!(g.contains("mac-host"));
        assert!(g.contains("CPU_CORES mac-host"));
        assert!(g.contains("cores = 4"));
    }

    #[test]
    fn groovy_delete_mentions_remove() {
        let g = groovy_delete_cpu_cores("mac-host", 4);
        assert!(g.contains("resources.remove"));
        assert!(g.contains("mac-host"));
    }
}
