use std::path::{Path, PathBuf};

use clap::Args;

use crate::config::{MacK3dConfig, NodeRole};
use crate::error::{Error, Result};

const EXPORT_SAME_PATH_MSG: &str = "export -o would overwrite the source config; pick another path";

#[derive(Debug, Args)]
pub struct ExportArgs {
    /// Destination YAML (sanitized; no API tokens or host-local paths)
    #[arg(short, long)]
    pub output: PathBuf,
}

#[derive(Debug, Args)]
pub struct ImportArgs {
    /// Sanitized YAML copied from another machine
    pub source: PathBuf,

    /// Overwrite the destination if it already exists
    #[arg(long)]
    pub force: bool,
}

/// Copy a live config to a shareable YAML. Does not read `credentials.pending.yaml`.
pub fn run_export(args: ExportArgs, config_path: Option<&Path>) -> Result<()> {
    let source = MacK3dConfig::resolve_config_path(config_path);
    let config = MacK3dConfig::load_file(&source)?;
    if paths_equal(&source, &args.output) {
        return Err(Error::Config(EXPORT_SAME_PATH_MSG.into()));
    }
    config.save_sanitized_export(&args.output)?;
    println!(
        "Exported sanitized {} config to {}.\n\
         Secrets and host paths were stripped. Edit jenkins_job / labels if this lab differs, then:\n\
           mac-k3d import {}",
        role_name(config.role),
        args.output.display(),
        args.output.display()
    );
    Ok(())
}

/// Write a sanitized YAML onto this machine. Does not start Docker, k3d, or Jenkins.
pub fn run_import(args: ImportArgs, dest_override: Option<&Path>) -> Result<()> {
    let incoming = MacK3dConfig::load_file(&args.source)?;
    let dest = dest_override
        .map(PathBuf::from)
        .unwrap_or_else(|| MacK3dConfig::default_path_for_role(incoming.role));
    write_imported_config(incoming, &dest, args.force)?;
    Ok(())
}

fn write_imported_config(incoming: MacK3dConfig, dest: &Path, force: bool) -> Result<MacK3dConfig> {
    if dest.exists() && !force {
        return Err(Error::Config(format!(
            "{} already exists; pass --force to overwrite",
            dest.display()
        )));
    }
    let mut cfg = incoming.for_export();
    cfg.apply_host_platform();
    cfg.save(Some(dest))?;
    print_import_next_steps(&cfg, dest);
    Ok(cfg)
}

fn print_import_next_steps(config: &MacK3dConfig, dest: &Path) {
    let c = dest.display();
    println!(
        "Imported {} config to {} (write only; Docker/k3d/Jenkins were not started).",
        role_name(config.role),
        c
    );
    match config.role {
        NodeRole::Worker => {
            if config.jenkins_agent.api_credentials().is_none() {
                println!(
                    "Put jenkins_agent.api_user / api_token in {c} then:\n  mac-k3d config -c {c}"
                );
            } else {
                println!("Next (worker — do not run start on this file):\n  mac-k3d config -c {c}");
            }
        }
        NodeRole::Controller => {
            println!(
                "Next (controller):\n  mac-k3d setup -c {c}\n\
                 Or if Docker/k3d already exist:\n  mac-k3d start -c {c}\n  mac-k3d config -c {c} --skip-secrets"
            );
        }
        NodeRole::Standalone => {
            println!("Next:\n  mac-k3d setup -c {c}");
        }
    }
}

fn role_name(role: NodeRole) -> &'static str {
    match role {
        NodeRole::Standalone => "standalone",
        NodeRole::Controller => "controller",
        NodeRole::Worker => "worker",
    }
}

fn paths_equal(a: &Path, b: &Path) -> bool {
    if a == b {
        return true;
    }
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(aa), Ok(bb)) => aa == bb,
        _ => false,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::{DependencySource, JenkinsJobConfig, LolbenchSource};

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mac-k3d-xfer-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// Worker template as in docs/export-import.md (URL, labels, name, Harbor skip, token).
    fn worker_with_token() -> MacK3dConfig {
        let mut cfg = MacK3dConfig::default();
        cfg.role = NodeRole::Worker;
        cfg.platform = Some("linux".into());
        cfg.storage.base_dir = Some(PathBuf::from("/home/src/mac-k3d"));
        cfg.dependencies.harbor.source = DependencySource::Skip;
        cfg.lolbench.source = LolbenchSource::Skip;
        cfg.lolbench.path = Some(PathBuf::from("/home/src/lolbench"));
        cfg.jenkins_agent.controller_url = Some("http://43.107.42.252:17070".into());
        cfg.jenkins_agent.name = Some("linux-eval-1".into());
        cfg.jenkins_agent.labels = vec!["linux".into(), "docker".into()];
        cfg.jenkins_agent.remote_fs = Some(PathBuf::from("/home/src/jenkins-agent"));
        cfg.jenkins_agent.agent_jar = Some(PathBuf::from("/tmp/agent.jar"));
        cfg.jenkins_agent.cpu_cores = 8;
        cfg.jenkins_agent.api_user = Some("admin".into());
        cfg.jenkins_agent.api_token = Some("test-jenkins-token".into());
        cfg.jenkins_job = JenkinsJobConfig {
            default_task: "ruff_1".into(),
            ..JenkinsJobConfig::default()
        };
        cfg.resources.cpu_cores_label = "CPU_CORES".into();
        cfg
    }

    fn assert_worker_template_kept(cfg: &MacK3dConfig) {
        assert_eq!(cfg.role, NodeRole::Worker);
        assert_eq!(
            cfg.jenkins_agent.controller_url.as_deref(),
            Some("http://43.107.42.252:17070")
        );
        assert_eq!(cfg.jenkins_agent.name.as_deref(), Some("linux-eval-1"));
        assert_eq!(
            cfg.jenkins_agent.labels,
            vec!["linux".to_string(), "docker".to_string()]
        );
        assert_eq!(cfg.jenkins_agent.api_user.as_deref(), Some("admin"));
        assert_eq!(cfg.dependencies.harbor.source, DependencySource::Skip);
        assert_eq!(cfg.lolbench.source, LolbenchSource::Skip);
        assert_eq!(cfg.resources.cpu_cores_label, "CPU_CORES");
    }

    fn assert_worker_secrets_and_hosts_stripped(cfg: &MacK3dConfig) {
        assert!(cfg.jenkins_agent.api_token.is_none());
        assert_eq!(cfg.jenkins_agent.cpu_cores, 0);
        assert!(cfg.jenkins_agent.remote_fs.is_none());
        assert!(cfg.jenkins_agent.agent_jar.is_none());
        assert!(cfg.storage.base_dir.is_none());
        assert!(cfg.lolbench.path.is_none());
    }

    #[test]
    fn import_infers_worker_dest_when_no_override() {
        let incoming = worker_with_token();
        assert_eq!(
            MacK3dConfig::default_path_for_role(incoming.role),
            MacK3dConfig::default_worker_path()
        );
    }

    #[test]
    fn import_refuses_overwrite_without_force() {
        let dir = temp_dir("ow");
        let dest = dir.join("worker.yaml");
        std::fs::write(&dest, "role: worker\n").unwrap();
        let err = write_imported_config(worker_with_token(), &dest, false).unwrap_err();
        let msg = err.to_string();
        assert!(msg.contains("already exists"), "{msg}");
        assert!(msg.contains("--force"), "{msg}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn import_force_overwrites_and_strips_token() {
        let dir = temp_dir("force");
        let dest = dir.join("worker.yaml");
        std::fs::write(&dest, "role: worker\napi_token: leftover\n").unwrap();
        let written = write_imported_config(worker_with_token(), &dest, true).unwrap();
        assert_worker_template_kept(&written);
        assert_worker_secrets_and_hosts_stripped(&written);
        assert_eq!(written.jenkins_job.default_task, "ruff_1");
        assert_eq!(
            written.platform.as_deref(),
            Some(crate::platform::host_os().as_str())
        );
        let text = std::fs::read_to_string(&dest).unwrap();
        assert!(!text.contains("test-jenkins-token"), "{text}");
        assert!(!text.contains("leftover"), "{text}");
        assert!(
            text.contains("api_token: ''"),
            "dest YAML must show a blank api_token after --force:\n{text}"
        );
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn export_then_scratch_import_keeps_worker_template() {
        let dir = temp_dir("worker-doc");
        let src = dir.join("worker.yaml");
        let portable = dir.join("portable.yaml");
        let scratch = dir.join("imported-worker.yaml");
        worker_with_token().save(Some(&src)).unwrap();

        run_export(
            ExportArgs {
                output: portable.clone(),
            },
            Some(&src),
        )
        .unwrap();
        let portable_text = std::fs::read_to_string(&portable).unwrap();
        assert!(
            !portable_text.contains("test-jenkins-token"),
            "{portable_text}"
        );
        assert!(portable_text.contains("api_token: ''"), "{portable_text}");
        assert!(portable_text.contains("http://43.107.42.252:17070"));
        assert!(portable_text.contains("linux-eval-1"));

        run_import(
            ImportArgs {
                source: portable,
                force: false,
            },
            Some(&scratch),
        )
        .unwrap();
        let loaded = MacK3dConfig::load_file(&scratch).unwrap();
        assert_worker_template_kept(&loaded);
        assert_worker_secrets_and_hosts_stripped(&loaded);
        assert_eq!(
            loaded.platform.as_deref(),
            Some(crate::platform::host_os().as_str())
        );
        let scratch_text = std::fs::read_to_string(&scratch).unwrap();
        assert!(scratch_text.contains("api_token: ''"), "{scratch_text}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn export_roundtrip_preserves_task_not_token() {
        let dir = temp_dir("rt");
        let src = dir.join("live.yaml");
        let out = dir.join("portable.yaml");
        worker_with_token().save(Some(&src)).unwrap();
        run_export(
            ExportArgs {
                output: out.clone(),
            },
            Some(&src),
        )
        .unwrap();
        let text = std::fs::read_to_string(&out).unwrap();
        assert!(!text.contains("test-jenkins-token"));
        let imported = MacK3dConfig::load_file(&out).unwrap();
        assert_eq!(imported.jenkins_job.default_task, "ruff_1");
        assert_worker_template_kept(&imported);
        assert_worker_secrets_and_hosts_stripped(&imported);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn export_refuses_same_path() {
        let dir = temp_dir("same");
        let src = dir.join("live.yaml");
        worker_with_token().save(Some(&src)).unwrap();
        let err = run_export(
            ExportArgs {
                output: src.clone(),
            },
            Some(&src),
        )
        .unwrap_err();
        assert!(err.to_string().contains("overwrite the source config"));
        let _ = std::fs::remove_dir_all(&dir);
    }
}
