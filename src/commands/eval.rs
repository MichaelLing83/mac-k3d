use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::Command;

use clap::{Args, Subcommand};
use dialoguer::{theme::ColorfulTheme, Confirm, Input, Password, Select};

use super::eval_queue::{self, QueueRequest};
use super::eval_record::{self, LocalOutcome, RecordArgs};
use crate::config::MacK3dConfig;
use crate::error::{Error, Result};
use crate::eval_catalog;
use crate::prepare::jenkins_job::{self, JobShape};

#[derive(Debug, Subcommand)]
pub enum EvalAction {
    /// Write one row per question of a finished run into docs/testing/question-log.md
    /// (--job/--build for a Jenkins build; no flags for the last local run)
    Record(RecordArgs),
    /// Prepare this worker's DeepSWE images before any eval: on an arm64 host (Apple
    /// silicon) build each question natively, prove the build once with Harbor's oracle
    /// and nop agents, and tag it with the official image name. Nothing to do on x86-64
    Prepare(EvalPrepareArgs),
}

/// `mac-k3d eval prepare`: runs pipeline/tools/prewarm_task_images.sh.
#[derive(Debug, Args)]
pub struct EvalPrepareArgs {
    /// Benchmark to prepare: deepswe
    #[arg(long)]
    pub benchmark: String,

    /// Comma-separated question ids (default: every question at DEEPSWE_REF)
    #[arg(long)]
    pub tasks: Option<String>,

    /// Build again even when a native build is current, then check it again
    #[arg(long)]
    pub rebuild: bool,
}

const PREPARE_SCRIPT: &str = "pipeline/tools/prewarm_task_images.sh";

/// Env for the prepare engine. A question list comes only from --tasks.
fn prepare_env(args: &EvalPrepareArgs, repo: &Path, workdir: &Path) -> Vec<(&'static str, OsString)> {
    let mut env: Vec<(&'static str, OsString)> = vec![
        ("MAC_K3D_ROOT", repo.into()),
        ("MAC_K3D_EVAL_WORKDIR", workdir.into()),
        ("BENCHMARK", "deepswe".into()),
    ];
    if let Some(tasks) = args.tasks.as_deref().map(str::trim).filter(|t| !t.is_empty()) {
        env.push(("TASKS", tasks.into()));
    }
    if args.rebuild {
        env.push(("DEEPSWE_REBUILD", "1".into()));
    }
    env
}

fn run_prepare(args: EvalPrepareArgs) -> Result<()> {
    if normalize_benchmark(&args.benchmark)? != "deepswe" {
        return Err(Error::Config(format!(
            "eval prepare covers --benchmark deepswe (got {}); LoLBench builds its images in each eval and \
             Harbor pulls the SWE-bench Pro images",
            args.benchmark
        )));
    }
    if let Err(err) = crate::prepare::eval_assets::ensure_share_pipeline_reported() {
        println!("Warning: could not extract pipeline ({err}).");
    }
    let repo = discover_repo_root()?;
    let script = repo.join(PREPARE_SCRIPT);
    if !script.is_file() {
        return Err(Error::Config(format!("missing {}", script.display())));
    }
    let workdir = crate::prepare::eval_assets::share_dir().join("prepare");
    std::fs::create_dir_all(&workdir)
        .map_err(|e| Error::Config(format!("mkdir {}: {e}", workdir.display())))?;
    let mut bash = Command::new("bash");
    bash.arg(&script).current_dir(&repo);
    for name in ["TASK", "TASKS", "N_TASKS", "TASK_OFFSET", "DEEPSWE_REBUILD", "MAC_K3D_DEEPSWE_PREPARE"] {
        bash.env_remove(name);
    }
    let status = bash
        .envs(prepare_env(&args, &repo, &workdir))
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: format!("bash {}", script.display()),
            source: e.into(),
        })?;
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("bash {}", script.display()),
            source: anyhow::anyhow!("exit {:?}", status.code()),
        });
    }
    Ok(())
}

#[derive(Debug, Default, Args)]
pub struct EvalArgs {
    #[command(subcommand)]
    pub action: Option<EvalAction>,

    /// Run one phase locally: env, tasks, evaluate, anticheat, score, report,
    /// archive, all, or baseline (the manual LLM-only arm). Omit for the full flow
    #[arg(long)]
    pub stage: Option<String>,

    /// Run the full pipeline locally (pipeline/stages/run_all.sh) instead of Jenkins
    #[arg(long)]
    pub local: bool,

    /// Queue a Jenkins job: one (<benchmark>_one_task), some (_some_task) or full
    /// (_full_suite_task). Sends only the fields given as flags; every other field
    /// keeps the job's default, as pressing Build does
    #[arg(long, value_name = "one|some|full", conflicts_with_all = ["local", "stage", "icode_release"])]
    pub job: Option<String>,

    /// With --job some: comma-separated question ids (TASKS); wins over --n-tasks
    #[arg(long, requires = "job")]
    pub tasks: Option<String>,

    /// With --job some|full: attempts per question (N_ROLLOUTS). one_task is one rollout
    #[arg(long, requires = "job")]
    pub n_rollouts: Option<u32>,

    /// With --job: any other field of the job page, e.g. --param CANARY=on or
    /// --param AGENT_LABEL=mac-Michael-Ubuntu (repeatable)
    #[arg(long = "param", value_name = "NAME=VALUE", requires = "job")]
    pub param: Vec<String>,

    /// With --job: print the job and the fields it would send; queue nothing
    #[arg(long, requires = "job")]
    pub dry_run: bool,

    /// Number of tasks when TASK is empty (default 1 locally; with --job some or
    /// full, N_TASKS)
    #[arg(long)]
    pub n_tasks: Option<u32>,

    /// Benchmark: deepswe | lolbench | swebenchpro
    #[arg(long)]
    pub benchmark: Option<String>,

    /// One question id (DeepSWE/SWE-bench Pro task dir or LoLBench harbor_tasks id)
    #[arg(long)]
    pub task: Option<String>,

    /// iCode delivery: release | git (`binary` is an alias of release). Default
    /// release locally; with --job, the job's default
    #[arg(long)]
    pub icode_mode: Option<String>,

    /// Official iCode *-full-* path/URL for `--local` / `--stage` (empty = persist then ~/.local/share/mac-k3d/).
    /// Jenkins release mode uses UI upload `ICODE_RELEASE_FILE`, not this flag.
    #[arg(long)]
    pub icode_release: Option<String>,

    /// Git URL for ICODE_MODE=git (https, allow-listed host)
    #[arg(long)]
    pub icode_git_url: Option<String>,

    /// Git tag, commit SHA, branch, or pull-request number (ICODE_MODE=git; empty = main)
    #[arg(long)]
    pub icode_git_ref: Option<String>,

    /// How to check out ICODE_GIT_REF: branch | tag | commit | pr
    #[arg(long)]
    pub icode_git_ref_kind: Option<String>,

    /// Eval workdir (default: ./eval-runs)
    #[arg(long)]
    pub workdir: Option<PathBuf>,

    /// DeepSeek Chat Completions model id (catalog; env DEEPSEEK_MODEL)
    #[arg(long)]
    pub model: Option<String>,

    /// Skip interactive prompts (use flags / env only). Without --local or
    /// --stage this queues <benchmark>_one_task, as --job one does
    #[arg(long)]
    pub yes: bool,
}

/// Interactive or staged iCode / DeepSeek / DeepSWE evaluation.
pub async fn run(args: EvalArgs, config: &MacK3dConfig) -> Result<()> {
    match args.action {
        Some(EvalAction::Record(record)) => return eval_record::run(record, config),
        Some(EvalAction::Prepare(prepare)) => return run_prepare(prepare),
        None => {}
    }
    if let Some(job) = args.job.as_deref() {
        let req = QueueRequest::from_args(&args, eval_queue::parse_shape(job)?)?;
        return eval_queue::queue(config, &req, args.dry_run);
    }
    if args.yes && !args.local && args.stage.is_none() {
        let req = QueueRequest::from_args(&args, JobShape::One)?;
        return eval_queue::queue(config, &req, false);
    }
    if let Err(err) = crate::prepare::eval_assets::ensure_share_pipeline_reported() {
        println!("Warning: could not extract pipeline ({err}).");
    }
    let repo = discover_repo_root()?;
    let mut n_tasks = args.n_tasks.unwrap_or(1).max(1);
    let env_bench = std::env::var("BENCHMARK").ok();
    let mut benchmark = normalize_benchmark(
        args.benchmark
            .as_deref()
            .or(env_bench.as_deref())
            .unwrap_or("deepswe"),
    )?;
    let mut task = inherit_eval_task(
        args.task.clone(),
        args.benchmark.as_deref(),
        std::env::var("TASK").ok(),
    );
    let mut icode_mode =
        eval_catalog::normalize_icode_mode(args.icode_mode.as_deref().unwrap_or("release"))?;
    let stored_paths = crate::prepare::icode_paths::load();
    let mut icode_release = args
        .icode_release
        .or_else(|| {
            std::env::var("ICODE_RELEASE")
                .ok()
                .filter(|s| !s.trim().is_empty())
        })
        .or_else(|| {
            let s = stored_paths.release.trim();
            if s.is_empty() {
                None
            } else {
                Some(s.to_string())
            }
        })
        .unwrap_or_else(discover_icode_release);
    let mut icode_git_url = args
        .icode_git_url
        .or_else(|| {
            std::env::var("ICODE_GIT_URL")
                .ok()
                .filter(|s| !s.trim().is_empty())
        })
        .or_else(|| {
            let d = config.jenkins_job.default_icode_git_url.trim();
            if d.is_empty() {
                None
            } else {
                Some(d.to_string())
            }
        })
        .unwrap_or_default();
    let mut icode_git_ref = args
        .icode_git_ref
        .or_else(|| {
            std::env::var("ICODE_GIT_REF")
                .ok()
                .filter(|s| !s.trim().is_empty())
        })
        .or_else(|| {
            let d = config.jenkins_job.default_icode_git_ref.trim();
            if d.is_empty() {
                None
            } else {
                Some(d.to_string())
            }
        })
        .unwrap_or_else(|| "main".into());
    let mut icode_git_ref_kind = args
        .icode_git_ref_kind
        .or_else(|| {
            std::env::var("ICODE_GIT_REF_KIND")
                .ok()
                .filter(|s| !s.trim().is_empty())
        })
        .unwrap_or_else(|| "branch".into());
    let workdir = args
        .workdir
        .or_else(|| {
            std::env::var("MAC_K3D_EVAL_WORKDIR")
                .ok()
                .map(PathBuf::from)
        })
        .unwrap_or_else(|| PathBuf::from("eval-runs"));
    let mut model = args
        .model
        .or_else(|| std::env::var("DEEPSEEK_MODEL").ok())
        .or_else(|| {
            let d = config.jenkins_job.default_deepseek_model.trim();
            if d.is_empty() {
                None
            } else {
                Some(d.to_string())
            }
        })
        .filter(|s| !s.trim().is_empty())
        .unwrap_or_else(|| eval_catalog::default_model().into());
    let local = args.local;

    if args.stage.is_none() && !args.yes && atty::is(atty::Stream::Stdin) {
        println!("mac-k3d eval — iCode harness vs DeepSeek baseline (DeepSWE or LoLBench)\n");
        let theme = ColorfulTheme::default();
        let _h = Select::with_theme(&theme)
            .with_prompt("Harness")
            .items(&["icode"])
            .default(0)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        let _l = Select::with_theme(&theme)
            .with_prompt("LLM")
            .items(&["deepseek"])
            .default(0)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        let benches: Vec<&str> = eval_catalog::BENCHMARKS.to_vec();
        let default_b = benches
            .iter()
            .position(|b| *b == benchmark.as_str())
            .unwrap_or(0);
        let b_idx = Select::with_theme(&theme)
            .with_prompt("Benchmark")
            .items(&benches)
            .default(default_b)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        benchmark = benches[b_idx].to_string();
        if benchmark == "lolbench" && task.is_empty() {
            task = "ruff_1".into();
        }
        task = Input::with_theme(&theme)
            .with_prompt("TASK (one question id; empty DeepSWE/SWE-bench Pro = first alphabetical)")
            .default(task)
            .allow_empty(true)
            .interact_text()
            .map_err(|_| Error::Cancelled)?;
        n_tasks = Input::with_theme(&theme)
            .with_prompt("Number of questions (used only when TASK is empty)")
            .default(n_tasks)
            .interact_text()
            .map_err(|_| Error::Cancelled)?;
        let mode_idx = Select::with_theme(&theme)
            .with_prompt("iCode delivery")
            .items(&[
                "release (official *-full-* or icode under ~/.local/share/mac-k3d/)",
                "git (clone allow-listed URL at tag / commit / branch / pull request)",
            ])
            .default(if icode_mode == "git" { 1 } else { 0 })
            .interact()
            .map_err(|_| Error::Cancelled)?;
        icode_mode = if mode_idx == 1 {
            "git".into()
        } else {
            "release".into()
        };
        if icode_mode == "git" {
            icode_git_url = Input::with_theme(&theme)
                .with_prompt("ICODE_GIT_URL (https://github.com/… or gitcode.com)")
                .default(icode_git_url)
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
            let kind_items = [
                "branch (checkout the tip of this branch)",
                "tag (release or lightweight tag)",
                "commit (SHA, including a pull-request commit to score before merge)",
                "pr (pull-request number; checks out that PR tip)",
            ];
            let kind_default = match icode_git_ref_kind.as_str() {
                "tag" => 1,
                "commit" => 2,
                "pr" => 3,
                "auto" if eval_catalog::looks_like_git_commit(&icode_git_ref) => 2,
                _ => 0,
            };
            let kind_idx = Select::with_theme(&theme)
                .with_prompt("ICODE_GIT_REF kind")
                .items(&kind_items)
                .default(kind_default)
                .interact()
                .map_err(|_| Error::Cancelled)?;
            icode_git_ref_kind = match kind_idx {
                1 => "tag".into(),
                2 => "commit".into(),
                3 => "pr".into(),
                _ => "branch".into(),
            };
            let ref_prompt = match icode_git_ref_kind.as_str() {
                "tag" => "ICODE_GIT_REF (tag name)",
                "commit" => {
                    "ICODE_GIT_REF (commit SHA; use the PR commit you want to evaluate, 7–40 hex)"
                }
                "pr" => "ICODE_GIT_REF (pull-request number)",
                _ => "ICODE_GIT_REF (branch name; tip is checked out)",
            };
            icode_git_ref = Input::with_theme(&theme)
                .with_prompt(ref_prompt)
                .default(icode_git_ref)
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
        } else {
            icode_release = Input::with_theme(&theme)
                .with_prompt(
                    "ICODE_RELEASE (local path; empty = persist file or ~/.local/share/mac-k3d/)",
                )
                .default(icode_release)
                .allow_empty(true)
                .interact_text()
                .map_err(|_| Error::Cancelled)?;
        }
        if icode_mode == "release" && !icode_release.trim().is_empty() {
            let rel = icode_release.trim();
            if !rel.starts_with("http://") && !rel.starts_with("https://") {
                crate::prepare::icode_paths::set_release(rel)?;
            }
        }
        let m_idx = eval_catalog::MODELS
            .iter()
            .position(|m| *m == model)
            .unwrap_or(0);
        model = eval_catalog::MODELS[Select::with_theme(&theme)
            .with_prompt("DeepSeek model")
            .items(eval_catalog::MODELS)
            .default(m_idx)
            .interact()
            .map_err(|_| Error::Cancelled)?]
        .to_string();
    }

    if args.stage.is_none() && !args.yes && !atty::is(atty::Stream::Stdin) {
        return Err(Error::Config(
            "not a TTY: pass --local, --stage <phase>, or --job one|some|full (Jenkins)".into(),
        ));
    }

    model = eval_catalog::require_model(&model)?;
    if icode_mode == "git" {
        icode_git_url = eval_catalog::require_icode_git_url(&icode_git_url)?;
        let (kind, git_ref) =
            eval_catalog::require_icode_git_ref_for_kind(&icode_git_ref_kind, &icode_git_ref)?;
        icode_git_ref_kind = kind;
        icode_git_ref = git_ref;
        let local_git =
            args.stage.is_some() || args.local || (!args.yes && atty::is(atty::Stream::Stdin));
        if local_git {
            ensure_git_clone_token(&repo, &icode_git_url, args.yes)?;
        }
    }

    let local_run = LocalRun {
        repo: &repo,
        workdir: &workdir,
        n_tasks,
        task: &task,
        benchmark: &benchmark,
        model: &model,
        icode_mode: &icode_mode,
        icode_release: &icode_release,
        icode_git_url: &icode_git_url,
        icode_git_ref: &icode_git_ref,
        icode_git_ref_kind: &icode_git_ref_kind,
    };

    if let Some(stage) = args.stage.as_deref() {
        return run_stage(&local_run, stage, config);
    }

    let run_local = local
        || Confirm::with_theme(&ColorfulTheme::default())
            .with_prompt(format!(
                "Run locally now (--local)? No = queue Jenkins job {}",
                jenkins_job::eval_job_name(&benchmark, JobShape::One)
            ))
            .default(true)
            .interact()
            .map_err(|_| Error::Cancelled)?;

    if run_local {
        return run_stage(&local_run, "all", config);
    }

    let git = icode_mode == "git";
    let req = QueueRequest {
        benchmark,
        shape: JobShape::One,
        task: Some(task),
        n_tasks: Some(n_tasks),
        icode_mode: Some(icode_mode),
        icode_git_url: git.then_some(icode_git_url),
        icode_git_ref: git.then_some(icode_git_ref),
        icode_git_ref_kind: git.then_some(icode_git_ref_kind),
        model: Some(model),
        ..Default::default()
    };
    eval_queue::queue(config, &req, false)
}

/// `--task` wins. If `--benchmark` is set, do not steal a leftover shell `TASK`
/// (e.g. ruff_1 after a LoLBench run). Jenkins still gets TASK from `--task` / prompts.
/// Prompt for a forge PAT when cloning a private repo. Writes gitignored `.env`
/// (mode 600). Never prints the value. `--yes` skips the prompt (`.env` / Jenkins).
fn ensure_git_clone_token(repo: &Path, git_url: &str, skip_prompt: bool) -> Result<()> {
    let spec = eval_catalog::git_pat_spec(git_url)?;
    if let Some(token) = load_git_pat(&spec, repo) {
        std::env::set_var(spec.env_var, &token);
        println!(
            "Using {} from env or .env for git clone (value not printed).",
            spec.env_var
        );
        return Ok(());
    }
    if skip_prompt || !atty::is(atty::Stream::Stdin) {
        println!(
            "No {} yet. Public clone will be tried; private repos need that key in gitignored .env \
             (chmod 600) or Jenkins credential {}.",
            spec.env_var, spec.jenkins_id
        );
        return Ok(());
    }
    let token: String = Password::with_theme(&ColorfulTheme::default())
        .with_prompt(spec.prompt)
        .allow_empty_password(true)
        .interact()
        .map_err(|_| Error::Cancelled)?;
    let token = token.trim().to_string();
    if token.is_empty() {
        println!(
            "Empty PAT: clone will fail fast if the repo is private. Add {} to .env later if needed.",
            spec.env_var
        );
        return Ok(());
    }
    std::env::set_var(spec.env_var, &token);
    let path = dotenv_write_path(repo);
    upsert_dotenv_key(&path, spec.env_var, &token)?;
    println!(
        "Saved {} to {} (mode 600). Value not printed.",
        spec.env_var,
        path.display()
    );
    Ok(())
}

fn load_git_pat(spec: &eval_catalog::GitPatSpec, repo: &Path) -> Option<String> {
    for var in [spec.env_var, spec.env_alias] {
        if let Ok(v) = std::env::var(var) {
            let t = v.trim();
            if !t.is_empty() {
                return Some(t.to_string());
            }
        }
    }
    for f in dotenv_candidate_files(Some(repo)) {
        if let Some(v) = read_dotenv_keys(&f, &[spec.env_var, spec.env_alias]) {
            return Some(v);
        }
    }
    None
}

fn dotenv_candidate_files(repo: Option<&Path>) -> Vec<PathBuf> {
    let mut candidates = Vec::new();
    if let Ok(p) = std::env::var("MAC_K3D_ENV_FILE") {
        let t = p.trim();
        if !t.is_empty() {
            candidates.push(PathBuf::from(t));
        }
    }
    if let Some(r) = repo {
        candidates.push(r.join(".env"));
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
    candidates
}

fn dotenv_write_path(repo: &Path) -> PathBuf {
    if let Ok(p) = std::env::var("MAC_K3D_ENV_FILE") {
        let t = p.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    let repo_env = repo.join(".env");
    if repo_env.is_file() {
        return repo_env;
    }
    for f in dotenv_candidate_files(Some(repo)) {
        if f.is_file() {
            return f;
        }
    }
    repo_env
}

fn read_dotenv_keys(path: &Path, want_keys: &[&str]) -> Option<String> {
    let text = std::fs::read_to_string(path).ok()?;
    let mut found: Option<String> = None;
    for line in text.lines() {
        if let Some((k, v)) = dotenv_line_value(line, want_keys) {
            if k == want_keys[0] {
                return Some(v);
            }
            if found.is_none() {
                found = Some(v);
            }
        }
    }
    found
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

fn dotenv_encode(value: &str) -> String {
    if value
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '-' | '/'))
    {
        return value.to_string();
    }
    format!("\"{}\"", value.replace('\\', "\\\\").replace('"', "\\\""))
}

fn upsert_dotenv_key(path: &Path, key: &str, value: &str) -> Result<()> {
    let mut lines: Vec<String> = if path.is_file() {
        std::fs::read_to_string(path)
            .map_err(|e| Error::Config(format!("read {}: {e}", path.display())))?
            .lines()
            .map(|s| s.to_string())
            .collect()
    } else {
        Vec::new()
    };
    let prefix = format!("{key}=");
    let new_line = format!("{key}={}", dotenv_encode(value));
    let mut found = false;
    for line in &mut lines {
        let t = line.trim_start();
        if t.starts_with('#') {
            continue;
        }
        if t.starts_with(&prefix) {
            *line = new_line.clone();
            found = true;
            break;
        }
    }
    if !found {
        if lines.last().is_some_and(|l| !l.is_empty()) {
            lines.push(String::new());
        }
        lines.push(new_line);
    }
    if let Some(parent) = path.parent() {
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)
                .map_err(|e| Error::Config(format!("mkdir {}: {e}", parent.display())))?;
        }
    }
    let tmp = path.with_file_name(format!(
        ".{}.tmp",
        path.file_name().and_then(|s| s.to_str()).unwrap_or("env")
    ));
    let body = if lines.is_empty() {
        String::new()
    } else {
        format!("{}\n", lines.join("\n"))
    };
    std::fs::write(&tmp, body)
        .map_err(|e| Error::Config(format!("write {}: {e}", tmp.display())))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(0o600));
    }
    std::fs::rename(&tmp, path)
        .map_err(|e| Error::Config(format!("replace {}: {e}", path.display())))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn inherit_eval_task(
    cli_task: Option<String>,
    cli_benchmark: Option<&str>,
    env_task: Option<String>,
) -> String {
    if let Some(t) = cli_task.filter(|s| !s.trim().is_empty()) {
        return t;
    }
    if cli_benchmark.is_some() {
        return String::new();
    }
    env_task
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .unwrap_or_default()
}

fn normalize_benchmark(s: &str) -> Result<String> {
    eval_catalog::require_benchmark(s)
}

fn discover_icode_release() -> String {
    crate::prepare::eval_assets::discover_icode_release()
        .map(|p| p.display().to_string())
        .unwrap_or_default()
}

pub(super) fn discover_repo_root() -> Result<PathBuf> {
    if let Ok(p) = std::env::var("MAC_K3D_ROOT") {
        let root = PathBuf::from(p);
        if crate::prepare::eval_assets::looks_like_root(&root) {
            return Ok(root);
        }
    }
    let cwd = std::env::current_dir().map_err(|e| Error::Config(e.to_string()))?;
    if crate::prepare::eval_assets::looks_like_root(&cwd) {
        return Ok(cwd);
    }
    if let Ok(exe) = std::env::current_exe() {
        let mut cur = exe.parent().map(Path::to_path_buf);
        while let Some(dir) = cur {
            if crate::prepare::eval_assets::looks_like_root(&dir) {
                return Ok(dir);
            }
            cur = dir.parent().map(Path::to_path_buf);
        }
    }
    crate::prepare::eval_assets::ensure_share_pipeline()
}

/// The script `--stage` runs, and the MAC_K3D_PHASE it runs with. A phase (or
/// `all`) goes through run_all.sh; `baseline` is the manual LLM-only arm.
fn stage_script(stage: &str) -> Result<(&'static str, Option<String>)> {
    use crate::prepare::eval_assets::{run_all_rel, PHASES};
    let stage = stage.trim().to_ascii_lowercase();
    if stage == "baseline" {
        return Ok(("pipeline/tools/baseline.sh", None));
    }
    if stage == "all" || PHASES.contains(&stage.as_str()) {
        return Ok((run_all_rel(), Some(stage)));
    }
    let phases = PHASES.join(", ");
    let now = match stage.as_str() {
        "p0" | "p1" => "--stage env",
        "p2" | "p3" | "p4" => "--stage tasks",
        "p5" => "--stage evaluate",
        "p6" => "--stage baseline",
        "p7" => "--stage anticheat, then --stage score",
        "p8" => "--stage report, then --stage archive",
        other => {
            return Err(Error::Config(format!(
                "unknown --stage {other}; use one of {phases}, all or baseline"
            )))
        }
    };
    Err(Error::Config(format!(
        "--stage {stage} is gone; the pipeline runs in phases ({phases}). Use {now}"
    )))
}

/// What a local `--stage` / `--local` run evaluates.
struct LocalRun<'a> {
    repo: &'a Path,
    workdir: &'a Path,
    n_tasks: u32,
    task: &'a str,
    benchmark: &'a str,
    model: &'a str,
    icode_mode: &'a str,
    icode_release: &'a str,
    icode_git_url: &'a str,
    icode_git_ref: &'a str,
    icode_git_ref_kind: &'a str,
}

impl LocalRun<'_> {
    /// Env for the stage's bash. `cpu_lock_qty` is None when the caller already
    /// exported CPU_LOCK_QTY, which bash then inherits unchanged.
    fn env(&self, phase: Option<&str>, cpu_lock_qty: Option<u32>) -> Vec<(&'static str, OsString)> {
        let mut env: Vec<(&'static str, OsString)> = Vec::new();
        if let Some(phase) = phase {
            env.push(("MAC_K3D_PHASE", phase.into()));
        }
        env.extend([
            ("MAC_K3D_ROOT", self.repo.into()),
            ("MAC_K3D_EVAL_WORKDIR", self.workdir.into()),
            ("N_TASKS", self.n_tasks.to_string().into()),
            ("TASK", self.task.into()),
            ("ICODE_MODE", self.icode_mode.into()),
            ("ICODE_RELEASE", self.icode_release.into()),
            ("ICODE_GIT_URL", self.icode_git_url.into()),
            ("ICODE_GIT_REF", self.icode_git_ref.into()),
            ("ICODE_GIT_REF_KIND", self.icode_git_ref_kind.into()),
            ("HARNESS", "icode".into()),
            ("LLM", "deepseek".into()),
            ("BENCHMARK", self.benchmark.into()),
            ("DEEPSEEK_MODEL", self.model.into()),
        ]);
        if let Some(qty) = cpu_lock_qty {
            env.push(("CPU_LOCK_QTY", qty.to_string().into()));
        }
        env
    }
}

/// One line saying how many cores a local run plans with and why.
fn cpu_lock_note(qty: u32, source: &str) -> String {
    let why = match source {
        "env" => "from the CPU_LOCK_QTY environment variable".to_string(),
        "worker.yaml" => format!(
            "from {} jenkins_agent.cpu_cores; Jenkins locks the same on this node",
            MacK3dConfig::default_worker_path().display()
        ),
        "config" => "from jenkins_agent.cpu_cores in the loaded config".to_string(),
        _ => "this host's logical CPUs; no worker.yaml sets jenkins_agent.cpu_cores".to_string(),
    };
    format!("CPU_LOCK_QTY={qty} ({why})")
}

fn run_stage(run: &LocalRun<'_>, stage: &str, config: &MacK3dConfig) -> Result<()> {
    let (script, phase) = stage_script(stage)?;
    let path = run.repo.join(script);
    if !path.is_file() {
        return Err(Error::Config(format!("missing {}", path.display())));
    }

    let (qty, source) = crate::prepare::resources::eval_cpu_lock_qty(config)?;
    println!("{}", cpu_lock_note(qty, source));
    let export_qty = (source != "env").then_some(qty);

    // A full run keeps its console for `eval record` and records itself, pass or fail.
    let records = phase.as_deref() == Some("all");
    let workdir = if run.workdir.is_absolute() {
        run.workdir.to_path_buf()
    } else {
        run.repo.join(run.workdir)
    };
    let started_epoch = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0);
    let mut bash = Command::new("bash");
    if records {
        std::fs::create_dir_all(&workdir)
            .map_err(|e| Error::Config(format!("mkdir {}: {e}", workdir.display())))?;
        bash.args(tee_args(&path, &workdir.join(eval_record::CONSOLE_LOG)));
    } else {
        bash.arg(&path);
    }
    let status = bash
        .current_dir(run.repo)
        .envs(run.env(phase.as_deref(), export_qty))
        .status()
        .map_err(|e| Error::CommandFailed {
            cmd: format!("bash {}", path.display()),
            source: e.into(),
        })?;
    if records {
        record_local_run(run.repo, &workdir, status.code(), started_epoch);
    }
    if !status.success() {
        return Err(Error::CommandFailed {
            cmd: format!("bash {}", path.display()),
            source: anyhow::anyhow!("exit {:?}", status.code()),
        });
    }
    Ok(())
}

/// `bash -c` arguments that run `script` with its output also copied to `log`,
/// keeping the script's exit status.
fn tee_args(script: &Path, log: &Path) -> Vec<OsString> {
    vec![
        "-c".into(),
        "set -o pipefail; bash \"$1\" 2>&1 | tee \"$2\"".into(),
        "bash".into(),
        script.into(),
        log.into(),
    ]
}

fn record_local_run(root: &Path, workdir: &Path, exit_code: Option<i32>, started_epoch: u64) {
    let repo = match eval_record::docs_repo(root) {
        Ok(repo) => repo,
        Err(_) => {
            println!(
                "Note: {} is not a mac-k3d checkout, so this run is not in docs/testing/question-log.md.",
                root.display()
            );
            return;
        }
    };
    let outcome = LocalOutcome {
        exit_code,
        started_epoch,
    };
    if let Err(err) = eval_record::record_local(&repo, workdir, None, Some(&outcome)) {
        println!("Warning: could not record this run in docs/testing/question-log.md ({err}).");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tee_keeps_the_stage_exit_code_and_copies_the_console() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-tee-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let script = dir.join("stage.sh");
        std::fs::write(&script, "echo out\necho err >&2\nexit 7\n").unwrap();
        let log = dir.join(eval_record::CONSOLE_LOG);
        let out = Command::new("bash")
            .args(tee_args(&script, &log))
            .output()
            .unwrap();
        let console = std::fs::read_to_string(&log).unwrap();
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(out.status.code(), Some(7));
        assert!(console.contains("out") && console.contains("err"), "{console}");
    }

    #[test]
    fn icode_paths_load_empty_without_file() {
        let _serial = crate::test_support::global_state();
        let prev = std::env::var_os("MAC_K3D_ICODE_PATHS");
        let missing =
            std::env::temp_dir().join(format!("mac-k3d-no-icode-paths-{}", std::process::id()));
        let _ = std::fs::remove_file(&missing);
        std::env::set_var("MAC_K3D_ICODE_PATHS", &missing);
        let got = crate::prepare::icode_paths::load();
        match prev {
            Some(v) => std::env::set_var("MAC_K3D_ICODE_PATHS", v),
            None => std::env::remove_var("MAC_K3D_ICODE_PATHS"),
        }
        assert!(got.release.is_empty());
    }

    #[test]
    fn discover_icode_release_empty_without_drop() {
        let _serial = crate::test_support::global_state();
        let prev = std::env::var_os("MAC_K3D_SHARE");
        std::env::set_var("MAC_K3D_SHARE", "/tmp/mac-k3d-no-icode-drop-xyz");
        let got = discover_icode_release();
        match prev {
            Some(v) => std::env::set_var("MAC_K3D_SHARE", v),
            None => std::env::remove_var("MAC_K3D_SHARE"),
        }
        assert!(got.is_empty() || !got.contains("Documents/Toby/mac-k3d"));
    }

    #[test]
    fn discover_icode_release_finds_full_tarball_in_share() {
        let _serial = crate::test_support::global_state();
        let dir = std::env::temp_dir().join(format!("mac-k3d-eval-full-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let tar = dir.join("icode-linux-x86_64-full-v0.1.41.tar.gz");
        std::fs::write(&tar, b"stub").unwrap();
        let prev = std::env::var_os("MAC_K3D_SHARE");
        std::env::set_var("MAC_K3D_SHARE", &dir);
        let got = discover_icode_release();
        match prev {
            Some(v) => std::env::set_var("MAC_K3D_SHARE", v),
            None => std::env::remove_var("MAC_K3D_SHARE"),
        }
        let _ = std::fs::remove_dir_all(&dir);
        assert_eq!(got, tar.display().to_string());
    }

    #[test]
    fn stage_takes_phase_names() {
        for phase in crate::prepare::eval_assets::PHASES {
            assert_eq!(
                stage_script(phase).unwrap(),
                ("pipeline/stages/run_all.sh", Some(phase.to_string()))
            );
        }
        assert_eq!(stage_script("ALL").unwrap().1.as_deref(), Some("all"));
        assert_eq!(
            stage_script("baseline").unwrap(),
            ("pipeline/tools/baseline.sh", None)
        );
    }

    #[test]
    fn old_stage_names_point_at_their_phase() {
        let cases = [
            ("p0", "--stage env"),
            ("p1", "--stage env"),
            ("p2", "--stage tasks"),
            ("p4", "--stage tasks"),
            ("p5", "--stage evaluate"),
            ("p6", "--stage baseline"),
            ("p7", "--stage anticheat, then --stage score"),
            ("p8", "--stage report, then --stage archive"),
        ];
        for (old, now) in cases {
            let err = stage_script(old).unwrap_err().to_string();
            assert!(err.contains(&format!("--stage {old} is gone")), "{err}");
            assert!(err.ends_with(&format!("Use {now}")), "{err}");
        }
        let err = stage_script("p9").unwrap_err().to_string();
        assert!(err.contains("unknown --stage p9"), "{err}");
        assert!(err.contains("env, tasks, evaluate, anticheat, score, report, archive"), "{err}");
    }

    #[test]
    fn looks_like_root_needs_pipeline_stages() {
        assert!(!crate::prepare::eval_assets::looks_like_root(Path::new(
            "/tmp"
        )));
    }

    #[test]
    fn normalize_benchmark_accepts_lolbench() {
        assert_eq!(normalize_benchmark("lolbench").unwrap(), "lolbench");
        assert_eq!(normalize_benchmark("LOLBENCH").unwrap(), "lolbench");
        assert_eq!(normalize_benchmark("deepswe").unwrap(), "deepswe");
        assert_eq!(normalize_benchmark("swebenchpro").unwrap(), "swebenchpro");
        assert!(normalize_benchmark("other").is_err());
    }

    fn sample_local_run() -> LocalRun<'static> {
        LocalRun {
            repo: Path::new("/repo"),
            workdir: Path::new("/work"),
            n_tasks: 1,
            task: "",
            benchmark: "deepswe",
            model: "deepseek-flash",
            icode_mode: "git",
            icode_release: "",
            icode_git_url: "https://gitcode.com/michaelling/jiuwenicode",
            icode_git_ref: "2",
            icode_git_ref_kind: "pr",
        }
    }

    fn env_value<'a>(env: &'a [(&'static str, OsString)], key: &str) -> Option<&'a str> {
        env.iter()
            .find(|(k, _)| *k == key)
            .and_then(|(_, v)| v.to_str())
    }

    #[test]
    fn local_run_env_exports_cpu_lock_qty_unless_caller_set_it() {
        let run = sample_local_run();
        let env = run.env(Some("evaluate"), Some(16));
        assert_eq!(env_value(&env, "CPU_LOCK_QTY"), Some("16"));
        assert_eq!(env_value(&env, "MAC_K3D_PHASE"), Some("evaluate"));
        assert_eq!(env_value(&env, "MAC_K3D_ROOT"), Some("/repo"));
        assert_eq!(env_value(&env, "MAC_K3D_EVAL_WORKDIR"), Some("/work"));
        assert_eq!(env_value(&env, "BENCHMARK"), Some("deepswe"));
        assert_eq!(env_value(&env, "DEEPSEEK_MODEL"), Some("deepseek-flash"));
        assert_eq!(env_value(&env, "ICODE_GIT_REF_KIND"), Some("pr"));

        let env = run.env(None, None);
        assert_eq!(env_value(&env, "CPU_LOCK_QTY"), None);
        assert_eq!(env_value(&env, "MAC_K3D_PHASE"), None);
        assert_eq!(env_value(&env, "N_TASKS"), Some("1"));
    }

    #[test]
    fn cpu_lock_note_names_its_source() {
        let note = cpu_lock_note(16, "worker.yaml");
        assert!(note.starts_with("CPU_LOCK_QTY=16 (from "), "{note}");
        assert!(
            note.ends_with(
                "worker.yaml jenkins_agent.cpu_cores; Jenkins locks the same on this node)"
            ),
            "{note}"
        );
        assert_eq!(
            cpu_lock_note(4, "env"),
            "CPU_LOCK_QTY=4 (from the CPU_LOCK_QTY environment variable)"
        );
        assert!(cpu_lock_note(12, "nproc").contains("logical CPUs"));
    }

    #[derive(clap::Parser)]
    struct EvalCli {
        #[command(subcommand)]
        action: EvalAction,
    }

    fn parse_prepare(argv: &[&str]) -> EvalPrepareArgs {
        use clap::Parser;
        match EvalCli::try_parse_from(argv).unwrap().action {
            EvalAction::Prepare(args) => args,
            other => panic!("not prepare: {other:?}"),
        }
    }

    #[test]
    fn eval_prepare_parses_its_flags() {
        let args = parse_prepare(&["eval", "prepare", "--benchmark", "deepswe"]);
        assert_eq!(args.benchmark, "deepswe");
        assert!(args.tasks.is_none() && !args.rebuild);
        let args = parse_prepare(&["eval", "prepare", "--benchmark", "deepswe", "--tasks", "a,b", "--rebuild"]);
        assert_eq!(args.tasks.as_deref(), Some("a,b"));
        assert!(args.rebuild);
        use clap::Parser;
        assert!(EvalCli::try_parse_from(["eval", "prepare"]).is_err(), "--benchmark is required");
    }

    #[test]
    fn eval_prepare_env_passes_only_its_flags() {
        let args = parse_prepare(&["eval", "prepare", "--benchmark", "deepswe"]);
        let env = prepare_env(&args, Path::new("/repo"), Path::new("/share/prepare"));
        assert_eq!(env_value(&env, "MAC_K3D_ROOT"), Some("/repo"));
        assert_eq!(env_value(&env, "MAC_K3D_EVAL_WORKDIR"), Some("/share/prepare"));
        assert_eq!(env_value(&env, "BENCHMARK"), Some("deepswe"));
        assert_eq!(env_value(&env, "TASKS"), None);
        assert_eq!(env_value(&env, "DEEPSWE_REBUILD"), None);

        let args = parse_prepare(&["eval", "prepare", "--benchmark", "deepswe", "--tasks", " a,b ", "--rebuild"]);
        let env = prepare_env(&args, Path::new("/repo"), Path::new("/w"));
        assert_eq!(env_value(&env, "TASKS"), Some("a,b"));
        assert_eq!(env_value(&env, "DEEPSWE_REBUILD"), Some("1"));
    }

    #[test]
    fn eval_prepare_covers_deepswe_only() {
        for bench in ["lolbench", "swebenchpro"] {
            let args = parse_prepare(&["eval", "prepare", "--benchmark", bench]);
            let err = run_prepare(args).unwrap_err().to_string();
            assert!(err.contains("eval prepare covers --benchmark deepswe"), "{err}");
        }
        let args = parse_prepare(&["eval", "prepare", "--benchmark", "other"]);
        assert!(run_prepare(args).is_err());
    }

    #[test]
    fn eval_prepare_is_a_no_op_on_an_x86_64_host() {
        let _serial = crate::test_support::global_state();
        let dir = tempfile::tempdir().unwrap();
        let bin = dir.path().join("bin");
        std::fs::create_dir_all(&bin).unwrap();
        let calls = dir.path().join("docker-calls");
        let docker = bin.join("docker");
        std::fs::write(
            &docker,
            format!(
                "#!/bin/sh\nif [ \"$1\" = info ]; then echo x86_64; exit 0; fi\necho \"$*\" >>'{}'\nexit 1\n",
                calls.display()
            ),
        )
        .unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&docker, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        let env_file = dir.path().join("empty.env");
        std::fs::write(&env_file, "").unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            std::fs::set_permissions(&env_file, std::fs::Permissions::from_mode(0o600)).unwrap();
        }
        let path = std::env::var_os("PATH").unwrap_or_default();
        let mut joined = OsString::from(&bin);
        joined.push(":");
        joined.push(&path);
        let vars: [(&str, OsString); 4] = [
            ("PATH", joined),
            ("MAC_K3D_ROOT", env!("CARGO_MANIFEST_DIR").into()),
            ("MAC_K3D_SHARE", dir.path().join("share").into()),
            ("MAC_K3D_ENV_FILE", env_file.into()),
        ];
        let saved: Vec<_> = vars.iter().map(|(k, _)| (*k, std::env::var_os(k))).collect();
        for (k, v) in &vars {
            std::env::set_var(k, v);
        }
        let got = run_prepare(parse_prepare(&["eval", "prepare", "--benchmark", "deepswe"]));
        for (k, v) in saved {
            match v {
                Some(v) => std::env::set_var(k, v),
                None => std::env::remove_var(k),
            }
        }
        got.unwrap();
        assert!(!calls.exists(), "docker was asked for more than the host CPU");
        assert!(dir.path().join("share/prepare").is_dir());
    }

    #[test]
    fn prepare_script_ships_with_the_pipeline() {
        let root = Path::new(env!("CARGO_MANIFEST_DIR"));
        assert!(root.join(PREPARE_SCRIPT).is_file());
    }

    #[test]
    fn inherit_eval_task_ignores_env_when_benchmark_flag_set() {
        assert_eq!(
            inherit_eval_task(None, Some("deepswe"), Some("ruff_1".into())),
            ""
        );
        assert_eq!(
            inherit_eval_task(
                Some("abs-module-cache-flags".into()),
                Some("deepswe"),
                Some("ruff_1".into())
            ),
            "abs-module-cache-flags"
        );
        assert_eq!(
            inherit_eval_task(None, None, Some("ruff_1".into())),
            "ruff_1"
        );
    }

    #[test]
    fn upsert_dotenv_key_replaces_without_leaking_other_keys() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join(".env");
        std::fs::write(&path, "DEEPSEEK_API_KEY=keep-me\nGITCODE_TOKEN=old\n").unwrap();
        upsert_dotenv_key(&path, "GITCODE_TOKEN", "new-pat-value").unwrap();
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains("DEEPSEEK_API_KEY=keep-me"));
        assert!(text.contains("GITCODE_TOKEN=new-pat-value"));
        assert!(!text.contains("GITCODE_TOKEN=old"));
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600, "expected 600 got {mode:o}");
        }
        assert_eq!(
            read_dotenv_keys(&path, &["GITCODE_TOKEN", "MAC_K3D_GITCODE_PAT"]).as_deref(),
            Some("new-pat-value")
        );
    }

    #[test]
    fn dotenv_line_value_skips_empty_and_comments() {
        assert!(dotenv_line_value("# GITCODE_TOKEN=x", &["GITCODE_TOKEN"]).is_none());
        assert!(dotenv_line_value("GITCODE_TOKEN=", &["GITCODE_TOKEN"]).is_none());
        assert_eq!(
            dotenv_line_value(r#"GITCODE_TOKEN="abc""#, &["GITCODE_TOKEN"])
                .map(|(_, v)| v)
                .as_deref(),
            Some("abc")
        );
    }
}
