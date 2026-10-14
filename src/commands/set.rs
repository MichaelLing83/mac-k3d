use std::path::{Path, PathBuf};
use std::process::Command;

use clap::Args;
use dialoguer::{theme::ColorfulTheme, Input, Select};

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};
use crate::eval_catalog;
use crate::prepare::eval_assets;

#[derive(Debug, Args)]
pub struct SetArgs {
    /// Print allowed harness / LLM / benchmark values and exit
    #[arg(long)]
    pub list: bool,

    /// GET /models: fail if YAML / `--model` id is missing from the provider list
    #[arg(long)]
    pub check_models: bool,

    /// Harness id (catalog): the default HARNESS of deepswe_one_task. jiuwenswarm
    /// runs on DeepSWE only; every other job keeps icode
    #[arg(long)]
    pub harness: Option<String>,

    /// LLM family (catalog)
    #[arg(long)]
    pub llm: Option<String>,

    /// DeepSeek Chat Completions model id (catalog)
    #[arg(long)]
    pub model: Option<String>,

    /// Benchmark (`deepswe`, `lolbench`, or `swebenchpro`)
    #[arg(long)]
    pub benchmark: Option<String>,

    /// One question id
    #[arg(long)]
    pub task: Option<String>,

    /// First N sorted questions (clears TASK / TASKS)
    #[arg(long)]
    pub n_tasks: Option<u32>,

    /// Comma-separated question ids
    #[arg(long)]
    pub tasks: Option<String>,

    /// Persist iCode `*-full-*` drop path on this worker
    #[arg(long)]
    pub icode_release: Option<String>,

    /// Jenkins job UI: `user` (short parameter lists) or `developer` (also pins, canary, scheduling)
    #[arg(long)]
    pub ui_profile: Option<String>,

    /// Builds one dispatcher keeps in the Jenkins queue at once. 0 counts 2 x online executors
    #[arg(long)]
    pub max_in_flight: Option<u32>,

    /// Times an out-of-memory rollout is put back at the end of the queue
    #[arg(long)]
    pub oom_retries: Option<u32>,
}

#[derive(Debug, Default, Clone)]
pub struct SetPatch {
    pub harness: Option<String>,
    pub llm: Option<String>,
    pub model: Option<String>,
    pub benchmark: Option<String>,
    pub task: Option<String>,
    pub n_tasks: Option<u32>,
    pub tasks: Option<String>,
    pub ui_profile: Option<String>,
    pub max_in_flight: Option<u32>,
    pub oom_retries: Option<u32>,
}

/// Edit `jenkins_job` on a YAML file. Does not start Jenkins or upload secrets.
pub fn run(args: SetArgs, config_path: Option<&Path>) -> Result<()> {
    if args.list {
        eval_catalog::print_catalog();
        return Ok(());
    }

    let question_modes = usize::from(args.task.is_some())
        + usize::from(args.n_tasks.is_some())
        + usize::from(args.tasks.is_some());
    if question_modes > 1 {
        return Err(Error::Config(
            "--task, --n-tasks, and --tasks are mutually exclusive".into(),
        ));
    }

    let path = MacK3dConfig::resolve_config_path(config_path);
    let mut config = MacK3dConfig::load_file(&path)?;

    let mut patch = SetPatch {
        harness: args.harness,
        llm: args.llm,
        model: args.model,
        benchmark: args.benchmark,
        task: args.task,
        n_tasks: args.n_tasks,
        tasks: args.tasks,
        ui_profile: args.ui_profile,
        max_in_flight: args.max_in_flight,
        oom_retries: args.oom_retries,
    };

    let mut any_flag = patch.harness.is_some()
        || patch.llm.is_some()
        || patch.model.is_some()
        || patch.benchmark.is_some()
        || patch.task.is_some()
        || patch.n_tasks.is_some()
        || patch.tasks.is_some()
        || patch.ui_profile.is_some()
        || patch.max_in_flight.is_some()
        || patch.oom_retries.is_some();
    let path_flags = args.icode_release.is_some();

    if !any_flag && !args.check_models && !path_flags {
        if atty::is(atty::Stream::Stdin) {
            patch = prompt_patch(&config)?;
            any_flag = true;
        } else {
            return Err(Error::Config(
                "not a TTY: pass --harness / --llm / --model / --benchmark / --task / --n-tasks / --tasks / --ui-profile / --max-in-flight / --oom-retries / --icode-release, --check-models, or --list"
                    .into(),
            ));
        }
    }

    if any_flag {
        apply_set(&mut config, patch)?;
    }
    if args.check_models {
        check_model_on_provider(&config)?;
    }
    if let Some(rel) = args.icode_release.as_deref() {
        crate::prepare::icode_paths::set_release(rel)?;
    }
    if any_flag {
        config.save(Some(&path))?;
        print_set_summary(&config, &path);
    }
    Ok(())
}

fn yaml_model(config: &MacK3dConfig) -> String {
    nonempty_or(
        &config.jenkins_job.default_deepseek_model,
        eval_catalog::default_model(),
    )
    .to_string()
}

fn dotenv_line_value(line: &str, want_keys: &[&str]) -> Option<(String, String)> {
    let line = line.trim();
    if line.is_empty() || line.starts_with('#') {
        return None;
    }
    let (key, value) = line.split_once('=')?;
    let key = key.trim();
    if !want_keys.iter().any(|k| *k == key) {
        return None;
    }
    let mut value = value.trim().to_string();
    if value.len() >= 2
        && ((value.starts_with('"') && value.ends_with('"'))
            || (value.starts_with('\'') && value.ends_with('\'')))
    {
        value = value[1..value.len() - 1].to_string();
    }
    if value.is_empty() {
        return None;
    }
    Some((key.to_string(), value))
}

fn read_dotenv_key(path: &Path) -> Option<String> {
    let text = std::fs::read_to_string(path).ok()?;
    let mut primary = None;
    let mut alias = None;
    for line in text.lines() {
        match dotenv_line_value(line, &["DEEPSEEK_API_KEY", "MAC_K3D_DEEPSEEK_API_KEY"]) {
            Some((k, v)) if k == "DEEPSEEK_API_KEY" => primary = Some(v),
            Some((k, v)) if k == "MAC_K3D_DEEPSEEK_API_KEY" => alias = Some(v),
            _ => {}
        }
    }
    primary.or(alias)
}

fn load_deepseek_api_key() -> Option<String> {
    for var in ["DEEPSEEK_API_KEY", "MAC_K3D_DEEPSEEK_API_KEY"] {
        if let Ok(v) = std::env::var(var) {
            let t = v.trim();
            if !t.is_empty() {
                return Some(t.to_string());
            }
        }
    }
    let mut candidates = Vec::new();
    if let Ok(p) = std::env::var("MAC_K3D_ENV_FILE") {
        let t = p.trim();
        if !t.is_empty() {
            candidates.push(PathBuf::from(t));
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        candidates.push(cwd.join(".env"));
    }
    let config_home = std::env::var("XDG_CONFIG_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| {
            std::env::var_os("HOME")
                .map(PathBuf::from)
                .unwrap_or_else(|| PathBuf::from("/tmp"))
                .join(".config")
        });
    candidates.push(config_home.join("mac-k3d/.env"));
    for f in candidates {
        if let Some(v) = read_dotenv_key(&f) {
            return Some(v);
        }
    }
    None
}

fn openai_compat_script() -> Result<PathBuf> {
    if let Ok(root) = std::env::var("MAC_K3D_ROOT") {
        let p = PathBuf::from(root.trim()).join("pipeline/lib/openai_compat.py");
        if p.is_file() {
            return Ok(p);
        }
    }
    if let Ok(cwd) = std::env::current_dir() {
        let p = cwd.join("pipeline/lib/openai_compat.py");
        if p.is_file() {
            return Ok(p);
        }
    }
    let share = eval_assets::ensure_share_pipeline()?;
    let p = share.join("pipeline/lib/openai_compat.py");
    if p.is_file() {
        return Ok(p);
    }
    Err(Error::Config(format!(
        "missing pipeline/lib/openai_compat.py (looked under MAC_K3D_ROOT, cwd, {})",
        share.display()
    )))
}

/// GET `{ICODE_API_BASE or https://api.deepseek.com}/models` and require YAML / catalog id.
fn check_model_on_provider(config: &MacK3dConfig) -> Result<()> {
    let model = yaml_model(config);
    let key = load_deepseek_api_key().ok_or_else(|| {
        Error::Config(
            "DEEPSEEK_API_KEY missing. Run --check-models on a worker/local machine with .env (cloud set often has no key)".into(),
        )
    })?;
    let script = openai_compat_script()?;
    let output = Command::new("python3")
        .arg(&script)
        .arg("--check-model")
        .arg(&model)
        .env("DEEPSEEK_API_KEY", &key)
        .output()
        .map_err(|e| Error::Config(format!("python3 required for --check-models: {e}")))?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);
    if !output.status.success() {
        let detail = if stderr.trim().is_empty() {
            stdout.trim().to_string()
        } else {
            stderr.trim().to_string()
        };
        let detail = detail
            .strip_prefix("ERROR: ")
            .unwrap_or(detail.as_str())
            .to_string();
        return Err(Error::Config(detail));
    }
    let line = stdout.trim();
    if !line.is_empty() {
        println!("{line}");
    }
    Ok(())
}

pub fn apply_set(config: &mut MacK3dConfig, patch: SetPatch) -> Result<()> {
    let job = &mut config.jenkins_job;
    if let Some(h) = patch.harness {
        job.default_harness = eval_catalog::require_harness(&h)?;
    }
    if let Some(l) = patch.llm {
        job.default_llm = eval_catalog::require_llm(&l)?;
    }
    if let Some(m) = patch.model {
        job.default_deepseek_model = eval_catalog::require_model(&m)?;
    }
    if let Some(b) = patch.benchmark {
        job.default_benchmark = eval_catalog::require_benchmark(&b)?;
    }
    if let Some(p) = patch.ui_profile {
        let p = p.trim().to_ascii_lowercase();
        if !crate::config::UI_PROFILES.contains(&p.as_str()) {
            return Err(Error::Config(format!(
                "--ui-profile '{p}' not allowed: {}",
                crate::config::UI_PROFILES.join(", ")
            )));
        }
        job.ui_profile = p;
    }
    if let Some(n) = patch.max_in_flight {
        job.max_in_flight = n;
    }
    if let Some(n) = patch.oom_retries {
        job.oom_retries = n;
    }

    let q = usize::from(patch.task.is_some())
        + usize::from(patch.n_tasks.is_some())
        + usize::from(patch.tasks.is_some());
    if q > 1 {
        return Err(Error::Config(
            "--task, --n-tasks, and --tasks are mutually exclusive".into(),
        ));
    }

    if let Some(task) = patch.task {
        let t = task.trim();
        if t.is_empty() {
            return Err(Error::Config("--task must not be empty".into()));
        }
        if t.contains(',') {
            return Err(Error::Config(
                "for several ids use --tasks a,b (not --task)".into(),
            ));
        }
        job.default_task = t.to_string();
        job.default_tasks.clear();
        job.default_n_tasks = 1;
    } else if let Some(n) = patch.n_tasks {
        if n == 0 {
            return Err(Error::Config("--n-tasks must be >= 1".into()));
        }
        job.default_task.clear();
        job.default_tasks.clear();
        job.default_n_tasks = n;
    } else if let Some(raw) = patch.tasks {
        let ids = eval_catalog::parse_task_list(&raw);
        if ids.is_empty() {
            return Err(Error::Config("--tasks must list at least one id".into()));
        }
        job.default_tasks = ids;
        job.default_n_tasks = job.default_tasks.len() as u32;
        job.default_task.clear();
    }
    Ok(())
}

fn prompt_patch(config: &MacK3dConfig) -> Result<SetPatch> {
    let theme = ColorfulTheme::default();
    let h_idx = index_of(
        eval_catalog::HARNESSES,
        nonempty_or(&config.jenkins_job.default_harness, "icode"),
    );
    let l_idx = index_of(
        eval_catalog::LLMS,
        nonempty_or(
            &config.jenkins_job.default_llm,
            nonempty_or(&config.jenkins_job.default_model, "deepseek"),
        ),
    );
    let m_idx = index_of(
        eval_catalog::MODELS,
        nonempty_or(
            &config.jenkins_job.default_deepseek_model,
            eval_catalog::default_model(),
        ),
    );
    let b_idx = index_of(
        eval_catalog::BENCHMARKS,
        nonempty_or(&config.jenkins_job.default_benchmark, "deepswe"),
    );

    let harness = eval_catalog::HARNESSES[Select::with_theme(&theme)
        .with_prompt("Harness")
        .items(eval_catalog::HARNESSES)
        .default(h_idx)
        .interact()
        .map_err(|_| Error::Cancelled)?]
    .to_string();
    let llm = eval_catalog::LLMS[Select::with_theme(&theme)
        .with_prompt("LLM family")
        .items(eval_catalog::LLMS)
        .default(l_idx)
        .interact()
        .map_err(|_| Error::Cancelled)?]
    .to_string();
    let model = eval_catalog::MODELS[Select::with_theme(&theme)
        .with_prompt("DeepSeek model")
        .items(eval_catalog::MODELS)
        .default(m_idx)
        .interact()
        .map_err(|_| Error::Cancelled)?]
    .to_string();
    let benchmark = eval_catalog::BENCHMARKS[Select::with_theme(&theme)
        .with_prompt("Benchmark")
        .items(eval_catalog::BENCHMARKS)
        .default(b_idx)
        .interact()
        .map_err(|_| Error::Cancelled)?]
    .to_string();

    let mode = Select::with_theme(&theme)
        .with_prompt("Questions")
        .items(&[
            "one specific id (--task)",
            "first N sorted (--n-tasks)",
            "explicit list (--tasks)",
        ])
        .default(0)
        .interact()
        .map_err(|_| Error::Cancelled)?;

    let mut patch = SetPatch {
        harness: Some(harness),
        llm: Some(llm),
        model: Some(model),
        benchmark: Some(benchmark),
        ..SetPatch::default()
    };
    match mode {
        0 => {
            patch.task = Some(
                Input::with_theme(&theme)
                    .with_prompt("TASK id")
                    .default(config.jenkins_job.default_task.clone())
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?,
            );
        }
        1 => {
            let n = config.jenkins_job.default_n_tasks.max(1);
            patch.n_tasks = Some(
                Input::with_theme(&theme)
                    .with_prompt("N_TASKS (first N sorted; paid if N>1)")
                    .default(n)
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?,
            );
        }
        _ => {
            let default = if config.jenkins_job.default_tasks.is_empty() {
                config.jenkins_job.default_task.clone()
            } else {
                config.jenkins_job.default_tasks.join(",")
            };
            patch.tasks = Some(
                Input::with_theme(&theme)
                    .with_prompt("TASKS (comma-separated ids)")
                    .default(default)
                    .interact_text()
                    .map_err(|_| Error::Cancelled)?,
            );
        }
    }
    Ok(patch)
}

fn nonempty_or<'a>(value: &'a str, fallback: &'a str) -> &'a str {
    if value.trim().is_empty() {
        fallback
    } else {
        value.trim()
    }
}

fn index_of(items: &[&str], value: &str) -> usize {
    items.iter().position(|a| *a == value).unwrap_or(0)
}

fn print_set_summary(config: &MacK3dConfig, path: &Path) {
    let job = &config.jenkins_job;
    println!("Updated {} (YAML only; secrets unchanged).", path.display());
    println!(
        "  harness={}  llm={}  model={}  benchmark={}",
        nonempty_or(&job.default_harness, "(unset)"),
        nonempty_or(&job.default_llm, nonempty_or(&job.default_model, "(unset)")),
        nonempty_or(&job.default_deepseek_model, eval_catalog::default_model()),
        nonempty_or(&job.default_benchmark, "(unset)")
    );
    if job.default_harness == "jiuwenswarm" {
        println!(
            "  jiuwenswarm is the default HARNESS of {} only; {} runs it on DeepSWE, and every other job keeps icode.",
            crate::prepare::jenkins_job::DEEPSWE_ONE_TASK,
            crate::prepare::jenkins_job::JIUWENSWARM_HEADLESS
        );
    }
    if !job.default_tasks.is_empty() {
        println!("  tasks={}", job.default_tasks.join(","));
    } else if !job.default_task.is_empty() {
        println!("  task={}", job.default_task);
    } else {
        println!("  n_tasks={} (first N sorted)", job.default_n_tasks.max(1));
    }
    println!(
        "  ui_profile={}  max_in_flight={}  oom_retries={}",
        job.ui_profile,
        job.max_in_flight,
        job.oom_retries
    );
    println!(
        "Controller: mac-k3d config -c {} --skip-secrets",
        path.display()
    );
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::JenkinsJobConfig;

    fn cfg() -> MacK3dConfig {
        MacK3dConfig {
            jenkins_job: JenkinsJobConfig::default(),
            ..MacK3dConfig::default()
        }
    }

    #[test]
    fn set_task_clears_list_and_n() {
        let mut c = cfg();
        c.jenkins_job.default_tasks = vec!["a".into(), "b".into()];
        c.jenkins_job.default_n_tasks = 5;
        apply_set(
            &mut c,
            SetPatch {
                task: Some("abs-stepped-slices".into()),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert_eq!(c.jenkins_job.default_task, "abs-stepped-slices");
        assert!(c.jenkins_job.default_tasks.is_empty());
        assert_eq!(c.jenkins_job.default_n_tasks, 1);
    }

    #[test]
    fn set_n_tasks_clears_task() {
        let mut c = cfg();
        apply_set(
            &mut c,
            SetPatch {
                n_tasks: Some(2),
                benchmark: Some("deepswe".into()),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert!(c.jenkins_job.default_task.is_empty());
        assert_eq!(c.jenkins_job.default_n_tasks, 2);
        assert_eq!(c.jenkins_job.default_benchmark, "deepswe");
    }

    #[test]
    fn set_tasks_list() {
        let mut c = cfg();
        apply_set(
            &mut c,
            SetPatch {
                tasks: Some("abs-module-cache-flags, abs-stepped-slices".into()),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert_eq!(
            c.jenkins_job.default_tasks,
            vec!["abs-module-cache-flags", "abs-stepped-slices"]
        );
        assert!(c.jenkins_job.default_task.is_empty());
        assert_eq!(c.jenkins_job.default_n_tasks, 2);
    }

    #[test]
    fn exclusive_question_modes() {
        let mut c = cfg();
        let err = apply_set(
            &mut c,
            SetPatch {
                task: Some("a".into()),
                n_tasks: Some(2),
                ..SetPatch::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("mutually exclusive"), "{err}");
    }

    #[test]
    fn reject_unknown_llm() {
        let mut c = cfg();
        let err = apply_set(
            &mut c,
            SetPatch {
                llm: Some("gpt".into()),
                ..SetPatch::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("allowed: deepseek"), "{err}");
    }

    #[test]
    fn set_model_flash() {
        let mut c = cfg();
        apply_set(
            &mut c,
            SetPatch {
                model: Some("deepseek-flash".into()),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert_eq!(c.jenkins_job.default_deepseek_model, "deepseek-flash");
    }

    #[test]
    fn reject_unknown_model() {
        let mut c = cfg();
        let err = apply_set(
            &mut c,
            SetPatch {
                model: Some("gpt-4".into()),
                ..SetPatch::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(
            err.contains("allowed: deepseek-flash, deepseek-v4-pro"),
            "{err}"
        );
    }

    #[test]
    fn set_ui_profile_and_dispatch_limits() {
        let mut c = cfg();
        apply_set(
            &mut c,
            SetPatch {
                ui_profile: Some("Developer".into()),
                max_in_flight: Some(30),
                oom_retries: Some(2),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert_eq!(c.jenkins_job.ui_profile, "developer");
        assert_eq!(c.jenkins_job.max_in_flight, 30);
        assert_eq!(c.jenkins_job.oom_retries, 2);
        apply_set(
            &mut c,
            SetPatch {
                max_in_flight: Some(0),
                ..SetPatch::default()
            },
        )
        .unwrap();
        assert_eq!(c.jenkins_job.max_in_flight, 0, "0 means count 2 x executors");

        let err = apply_set(
            &mut c,
            SetPatch {
                ui_profile: Some("admin".into()),
                ..SetPatch::default()
            },
        )
        .unwrap_err()
        .to_string();
        assert!(err.contains("user, developer"), "{err}");
    }

    #[test]
    fn dotenv_parses_quoted_key() {
        let got = dotenv_line_value(r#"DEEPSEEK_API_KEY="sk-test""#, &["DEEPSEEK_API_KEY"]);
        assert_eq!(got, Some(("DEEPSEEK_API_KEY".into(), "sk-test".into())));
        assert!(dotenv_line_value("OTHER=x", &["DEEPSEEK_API_KEY"]).is_none());
        assert!(dotenv_line_value("# DEEPSEEK_API_KEY=x", &["DEEPSEEK_API_KEY"]).is_none());
    }

    #[test]
    fn yaml_model_falls_back_to_catalog_default() {
        let c = cfg();
        assert_eq!(yaml_model(&c), eval_catalog::default_model());
    }
}
