use std::path::Path;

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};

const GB: u64 = 1024 * 1024 * 1024;

/// Logical CPU count for CPU_CORES registration. On macOS the task containers
/// run in Docker Desktop's VM, so this is capped at the CPUs it gives them.
pub fn logical_cpu_cores() -> u32 {
    let host = std::thread::available_parallelism()
        .map(|n| n.get() as u32)
        .unwrap_or(1)
        .max(1);
    #[cfg(target_os = "macos")]
    {
        if let Some((cpus, _)) = docker_vm_limits() {
            return host.min(cpus.max(1));
        }
    }
    host
}

/// Docker Desktop's VM on macOS: (CPUs, memory in kB) its containers get.
/// None when the daemon does not answer.
pub fn docker_vm_limits() -> Option<(u32, u64)> {
    let out = std::process::Command::new("docker")
        .args(["info", "--format", "{{.NCPU}} {{.MemTotal}}"])
        .stderr(std::process::Stdio::null())
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    parse_docker_vm_limits(&String::from_utf8_lossy(&out.stdout))
}

fn parse_docker_vm_limits(text: &str) -> Option<(u32, u64)> {
    let mut fields = text.split_whitespace();
    let cpus: u32 = fields.next()?.parse().ok()?;
    let bytes: u64 = fields.next()?.parse().ok()?;
    (cpus > 0 && bytes > 0).then_some((cpus, bytes / 1024))
}

/// macOS: `jenkins_agent.cpu_cores` above Docker Desktop's CPU limit would let
/// Jenkins start more trials than the VM can run.
pub fn cpu_cores_problem(cores: u32, docker_cpus: Option<u32>) -> Option<String> {
    let cpus = docker_cpus?;
    (cores > cpus).then(|| {
        format!(
            "jenkins_agent.cpu_cores is {cores} but Docker Desktop gives containers {cpus} CPUs. \
             Set cpu_cores to {cpus} or less, or raise Docker Desktop > Settings > Resources > CPU limit, \
             then run config again"
        )
    })
}

/// Cores a local eval (`eval --local`, `eval --stage`) plans with, and where the
/// number came from: `env`, `worker.yaml`, `config` or `nproc`.
///
/// worker.yaml's `jenkins_agent.cpu_cores` is what `config -c worker.yaml`
/// registers as `<agent>-core-1..N`, so it matches what a Jenkins build locks here.
pub fn eval_cpu_lock_qty(config: &MacK3dConfig) -> Result<(u32, &'static str)> {
    let env = std::env::var("CPU_LOCK_QTY").ok();
    let worker_path = MacK3dConfig::default_worker_path();
    let worker_cores = if worker_path.is_file() {
        MacK3dConfig::load_file(&worker_path)
            .ok()
            .map(|w| w.jenkins_agent.cpu_cores)
    } else {
        None
    };
    resolve_cpu_lock_qty(
        env.as_deref(),
        worker_cores,
        config.jenkins_agent.cpu_cores,
        logical_cpu_cores(),
    )
}

fn resolve_cpu_lock_qty(
    env: Option<&str>,
    worker_cores: Option<u32>,
    config_cores: u32,
    nproc: u32,
) -> Result<(u32, &'static str)> {
    if let Some(raw) = env.map(str::trim).filter(|s| !s.is_empty()) {
        return match raw.parse::<u32>() {
            Ok(n) if n >= 1 => Ok((n, "env")),
            _ => Err(Error::Validation(format!(
                "CPU_LOCK_QTY must be an integer >= 1 (got '{raw}')"
            ))),
        };
    }
    if let Some(n) = worker_cores.filter(|&n| n > 0) {
        return Ok((n, "worker.yaml"));
    }
    if config_cores > 0 {
        return Ok((config_cores, "config"));
    }
    Ok((nproc.max(1), "nproc"))
}

/// True when `kb` kilobytes is at least `min_gb` gigabytes.
pub fn ram_kb_meets(kb: u64, min_gb: u64) -> bool {
    kb / 1024 / 1024 >= min_gb
}

/// Eval hosts need MIN_RAM_GB (pipeline/config/toolchain.env); the env phase checks the same.
pub fn ensure_ram_min(min_gb: u64) -> Result<()> {
    let kb = mem_total_kb().unwrap_or(0);
    if kb == 0 {
        tracing::warn!("could not read MemTotal; skipping RAM preflight");
        return Ok(());
    }
    if !ram_kb_meets(kb, min_gb) {
        #[cfg(target_os = "macos")]
        let problem = format!(
            "Docker Desktop has only {} MB memory; need at least {min_gb} GB for eval. \
             Raise Docker Desktop > Settings > Resources > Memory",
            kb / 1024
        );
        #[cfg(not(target_os = "macos"))]
        let problem = format!(
            "only {} MB RAM; need at least {min_gb} GB for eval (keep N_TASKS=1)",
            kb / 1024
        );
        return Err(Error::Validation(problem));
    }
    tracing::info!(min_gb, "RAM check passed");
    Ok(())
}

fn mem_total_kb() -> Option<u64> {
    #[cfg(target_os = "linux")]
    {
        let text = std::fs::read_to_string("/proc/meminfo").ok()?;
        for line in text.lines() {
            if let Some(rest) = line.strip_prefix("MemTotal:") {
                return rest.split_whitespace().next()?.parse().ok();
            }
        }
        None
    }
    #[cfg(not(target_os = "linux"))]
    {
        if let Some((_, kb)) = docker_vm_limits() {
            return Some(kb);
        }
        let out = std::process::Command::new("sysctl")
            .args(["-n", "hw.memsize"])
            .output()
            .ok()?;
        let bytes: u64 = String::from_utf8_lossy(&out.stdout).trim().parse().ok()?;
        Some(bytes / 1024)
    }
}

/// Ensure free space on the volume containing `path` is at least `min_gb`.
pub fn ensure_disk_min(path: &Path, min_gb: u64) -> Result<()> {
    let check_path = path
        .ancestors()
        .find(|p| p.exists())
        .unwrap_or(Path::new("/"));
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
             with labels '{label} {agent_name}', and delete any {agent_name}-core-N above {cores}.\n\
             Or re-run prepare/config with api_user/api_token set.\n"
        );
        return Ok(());
    };

    println!(
        "Syncing {cores} Lockable Resources on {jenkins_url} (label '{label}', agent '{agent_name}')…"
    );

    let script = groovy_create_cpu_cores(agent_name, label, cores);
    match run_script_text(jenkins_url, user, token, &script) {
        Ok(output) => {
            println!("{}", cpu_cores_summary(&output, label));
            let busy = output.lines().filter(|l| l.starts_with("busy ")).count();
            if busy > 0 {
                println!(
                    "Warning: {busy} '{agent_name}-core-N' above {cores} are held by a running build, \
                     so Jenkins still locks more than {cores} there.\n\
                     Re-run `mac-k3d config -c worker.yaml` after the build finishes to prune them."
                );
            }
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
                 Create manually: Manage Jenkins → Lockable Resources → {cores} × '{agent_name}-core-N' labels '{label} {agent_name}',\n\
                 and delete any '{agent_name}-core-N' above {cores}."
            );
            Ok(())
        }
    }
}

fn cpu_cores_summary(output: &str, label: &str) -> String {
    let count = |prefix: &str| output.lines().filter(|l| l.starts_with(prefix)).count();
    let (created, existed) = (count("created "), count("exists "));
    let (pruned, busy) = (count("pruned "), count("busy "));
    let mut line = format!("Lockable Resources: {created} created, {existed} already present");
    if pruned > 0 || busy > 0 {
        line.push_str(&format!(", {pruned} pruned, {busy} busy"));
    }
    line.push_str(&format!(" (label '{label}')."));
    line
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

    println!("Removing Lockable Resources for '{agent_name}' on {jenkins_url}…");
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
def prefix = "${{agent}}-core-"
def extra = m.resources.findAll {{ r ->
  def n = r.name.startsWith(prefix) ? r.name.substring(prefix.length()) : ''
  n ==~ /\d+/ && n.toInteger() > cores
}}
extra.each {{ r ->
  if (r.isLocked() || r.isReserved()) {{
    println("busy " + r.name)
  }} else {{
    m.resources.remove(r)
    println("pruned " + r.name)
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
    let output = crate::jenkins_curl::Session::open(base_url, user, token)?.script_text(script)?;
    let body = String::from_utf8_lossy(&output.stdout).to_string();
    if !output.status.success() {
        return Err(Error::CommandFailed {
            cmd: "curl scriptText".into(),
            source: anyhow::anyhow!(
                "exit {:?} body={}",
                output.status.code(),
                truncate(&body, 200)
            ),
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
    fn ram_kb_meets_thresholds() {
        assert!(ram_kb_meets(8 * 1024 * 1024, 8));
        assert!(!ram_kb_meets(7 * 1024 * 1024, 8));
    }

    #[test]
    fn docker_vm_limits_parse_cpus_and_memory() {
        assert_eq!(
            parse_docker_vm_limits("8 16729407488\n"),
            Some((8, 16_337_312))
        );
        assert_eq!(parse_docker_vm_limits(""), None);
        assert_eq!(parse_docker_vm_limits("0 1024"), None);
        assert_eq!(parse_docker_vm_limits("eight 1024"), None);
    }

    #[test]
    fn cpu_cores_above_the_docker_vm_are_refused() {
        assert_eq!(cpu_cores_problem(8, Some(8)), None);
        assert_eq!(cpu_cores_problem(16, None), None);
        let problem = cpu_cores_problem(10, Some(8)).unwrap();
        assert!(problem.contains("cpu_cores is 10"), "{problem}");
        assert!(problem.contains("Resources > CPU limit"), "{problem}");
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
    fn cpu_lock_qty_precedence_env_worker_config_nproc() {
        let r = |env, worker, config| resolve_cpu_lock_qty(env, worker, config, 12).unwrap();
        assert_eq!(r(Some("4"), Some(16), 8), (4, "env"));
        assert_eq!(r(Some(" 4 "), None, 0), (4, "env"));
        assert_eq!(r(None, Some(16), 8), (16, "worker.yaml"));
        assert_eq!(r(Some(""), Some(16), 8), (16, "worker.yaml"));
        assert_eq!(r(None, Some(0), 8), (8, "config"));
        assert_eq!(r(None, None, 8), (8, "config"));
        assert_eq!(r(None, Some(0), 0), (12, "nproc"));
        assert_eq!(
            resolve_cpu_lock_qty(None, None, 0, 0).unwrap(),
            (1, "nproc")
        );
    }

    #[test]
    fn cpu_lock_qty_rejects_bad_env() {
        for bad in ["0", "-2", "abc", "1.5"] {
            let err = resolve_cpu_lock_qty(Some(bad), Some(16), 8, 12)
                .unwrap_err()
                .to_string();
            assert!(
                err.contains("CPU_LOCK_QTY must be an integer >= 1"),
                "{err}"
            );
        }
    }

    #[test]
    fn groovy_create_prunes_cores_above_count() {
        let g = groovy_create_cpu_cores("mac-host", "CPU_CORES", 16);
        assert!(g.contains("def prefix = \"${agent}-core-\""));
        assert!(g.contains("n ==~ /\\d+/ && n.toInteger() > cores"));
        assert!(g.contains("r.isLocked() || r.isReserved()"));
        assert!(g.contains("println(\"busy \" + r.name)"));
        assert!(g.contains("m.resources.remove(r)"));
        assert!(g.contains("println(\"pruned \" + r.name)"));
        let prune = g.find("pruned ").unwrap();
        let create = g.find("createResourceWithLabel").unwrap();
        let save = g.rfind("m.save()").unwrap();
        assert!(
            create < prune && prune < save,
            "prune runs after create, before save"
        );
    }

    #[test]
    fn cpu_cores_summary_counts_pruned_and_busy() {
        let out = "exists a-core-1\nexists a-core-2\ncreated a-core-3\npruned a-core-4\nbusy a-core-5\ndone\n";
        assert_eq!(
            cpu_cores_summary(out, "CPU_CORES"),
            "Lockable Resources: 1 created, 2 already present, 1 pruned, 1 busy (label 'CPU_CORES')."
        );
        assert_eq!(
            cpu_cores_summary("exists a-core-1\ndone\n", "CPU_CORES"),
            "Lockable Resources: 0 created, 1 already present (label 'CPU_CORES')."
        );
    }

    #[test]
    fn groovy_delete_mentions_remove() {
        let g = groovy_delete_cpu_cores("mac-host", 4);
        assert!(g.contains("resources.remove"));
        assert!(g.contains("mac-host"));
    }
}
