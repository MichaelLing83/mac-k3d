use std::path::Path;
use std::thread;
use std::time::Duration;

use crate::error::{Error, Result};
use crate::eval_catalog;
use crate::jenkins_curl::{self, Session};

pub const LOLBENCH_ONE_TASK: &str = "lolbench_one_task";

/// Static Jenkins parameter help. ASCII only (XML 1.0 POST). No apostrophes (Groovy singles).
const DESC_ICODE_MODE: &str = "Choose release (upload a drop) or git (clone URL + ref). Fill only the fields for that choice; leave the other group as it is.";
const DESC_ICODE_RELEASE_FILE: &str = "Release: choose the icode / icode-*-full-* drop here. Git: do not choose a file; leave this control as it is. Vice versa: if you chose git, ignore this; if you chose release, this is the file you upload.";
const DESC_ICODE_GIT_URL: &str = "Git: https URL on github.com or gitcode.com. Release: do not change this; leave it as it is. Vice versa: if you chose release, ignore this; if you chose git, this is required.";
const DESC_ICODE_GIT_REF: &str = "Git: branch name, tag, commit SHA, or pull-request number when KIND is pr. Release: do not change this; leave it as it is. Vice versa: if you chose release, ignore this; if you chose git, this is required.";
const DESC_ICODE_GIT_REF_KIND: &str = "Git: pick branch, tag, commit, or pr (no auto). For pr, REF is the pull-request number. Release: do not change this; leave it as it is. Vice versa: if you chose release, ignore this; if you chose git, pick the kind that matches REF.";
const DESC_DISPATCH_ICODE_GIT_URL: &str = "iCode git URL (https on github.com or gitcode.com). Prepare harness builds it once; every shard runs that build.";
const DESC_JIUWENSWARM_MODE: &str = "Choose git (clone URL + ref; built for the task containers' Linux) or release (upload a file). Fill only the fields for that choice; leave the other group as it is.";
const DESC_JIUWENSWARM_RELEASE_FILE: &str = "Release: a JiuwenSwarm wheel (.whl), a wheelhouse .tar.gz (wheelhouse/*.whl, installed offline), or the harness bundle.tar.gz an earlier run archived. mac-k3d tells them apart by content. Git: do not choose a file.";
const DESC_JIUWENSWARM_GIT_URL: &str = "Git: JiuwenSwarm https URL on github.com or gitcode.com. A private gitcode repo uses the controller's gitcode-pat credential, never a token in the URL. Release: leave it as it is.";
const DESC_JIUWENSWARM_GIT_REF: &str = "Git: branch name, tag, commit SHA, or pull-request number when KIND is pr. Release: leave it as it is.";
const DESC_JIUWENSWARM_GIT_REF_KIND: &str = "Git: branch, tag, commit or pr. For pr, REF is the pull-request number. Release: leave it as it is.";
const DESC_JIUWENSWARM_AGENT_MODE: &str = "The jiuwenswarm-process mode each rollout runs: agent.code.normal or agent.code.plan. Team mode does not run headless.";
const DESC_JIUWENSWARM_MAX_TURNS: &str = "Most model calls one rollout may make: 1 to 1000 (JiuwenSwarm's own limit).";
const DESC_TASKS: &str = "Comma-separated question ids. This field wins over N_TASKS. Example: ruff_1,fastapi_1. Leave empty to run the first N_TASKS sorted ids.";
const DESC_ICODE_RELEASE_SHA256: &str = "Set by some_task or full_suite_task: sha256 of the uploaded iCode release. The shard stops if its harness bundle records another.";
const DESC_ICODE_GIT_SHA: &str = "Set by some_task or full_suite_task: the commit every shard checks out. Empty on a direct build, which resolves the git form itself.";
const DESC_HARNESS_BUNDLE_FROM: &str = "Set by the dispatcher: job#build that archived the harness bundle it prepared. A shard copies it only when this worker's harness store does not hold it. Empty on a direct build.";
const DESC_HARNESS_BUNDLE_SHA256: &str = "Set with HARNESS_BUNDLE_FROM: the bundle's sha256. The shard uses that bundle and no other.";
const DESC_JIUWENSWARM_GIT_SHA: &str = "Set by the dispatcher: the JiuwenSwarm commit every shard runs. Empty on a direct build, which resolves the git form itself.";
const DESC_JIUWENSWARM_RELEASE_FROM: &str = "Set by the dispatcher: job#build that archived the uploaded JiuwenSwarm release, for a shard whose task platform needs its own build. Empty on a direct build.";
const DESC_JIUWENSWARM_RELEASE_SHA256: &str = "Set by the dispatcher: sha256 of the uploaded JiuwenSwarm release. The shard stops if its harness records another.";
const DESC_MAX_IN_FLIGHT: &str = "Builds of this run in the Jenkins queue at once, running or waiting. 0 counts 2 x the online executors that can run this suite. The rest wait in this build's list.";
const DESC_OOM_RETRIES: &str = "How many times a rollout that ran out of memory is put back at the end of the queue. 2 means three tries in all.";
const DESC_ROLLOUT: &str = "Set by some_task or full_suite_task: which attempt number of this question, from 1. 1 on a direct build.";
const DESC_ATTEMPT: &str = "Which try this is after out-of-memory re-queues. 1 on the first try.";
const DESC_TEST_MEMORY_MB: &str = "Testing only: trial memory limit in MB, to force an out of memory. Empty leaves the limit in task.toml.";
const DESC_TEST_MEMORY_ATTEMPTS: &str = "Testing only: TEST_MEMORY_MB applies while ATTEMPT is at most this number. 1 limits the first try; a re-queued try runs as declared.";
const DESC_RUN_GROUP: &str = "Set by some_task or full_suite_task on a shard build: ties the shards of one run together. Empty on a direct build.";
const DESC_SHARD: &str = "Set by some_task or full_suite_task on a shard build: which shard this is. Shown as the build description.";
const DESC_REQUESTED_BY: &str = "Set by some_task or full_suite_task on a shard build: the Jenkins user who started the run, for the report's Requester. Empty on a direct build.";
const DESC_SHARD_TASKS: &str = "Set by some_task or full_suite_task on a shard build: the question ids this shard runs. Empty on a direct build.";
const DESC_SHARD_N_TASKS: &str = "Set by some_task or full_suite_task on a shard build: how many sorted ids to take after TASK_OFFSET. 1 on a direct build.";
const DESC_TASK_OFFSET: &str = "Set by some_task or full_suite_task on a shard build: sorted ids to skip before taking N_TASKS. 0 on a direct build.";
const DESC_AGENT_LABEL: &str = "Label a worker must carry to run this build or its shards.";
const DESC_AGGREGATE_LABEL: &str = "Label of the worker that merges the shards into one report. Empty means AGENT_LABEL.";
/// Every worker registers with this label, so it means any online worker.
const DEFAULT_AGENT_LABEL: &str = "lolbench";
/// Starts the help text of every developer-only field, so the developer page
/// shows which fields a user never sees.
const DEVELOPER_PREFIX: &str = "Developer (testing): ";
const DESC_DEEPSEEK_MODEL: &str = "DeepSeek Chat Completions model id (catalog).";

/// Parameters a dispatcher hands to every shard unchanged.
const FORWARDED_PARAMS: &[&str] = &[
    "HARNESS",
    "LLM",
    "BENCHMARK",
    "AGENT_LABEL",
    "ICODE_GIT_URL",
    "ICODE_GIT_REF",
    "ICODE_GIT_REF_KIND",
    "HARBOR_VERSION",
    "DEEPSWE_REF",
    "LOLBENCH_REF",
    "ICODE_MODE",
    "DEEPSEEK_MODEL",
    "JIUWENSWARM_MODE",
    "JIUWENSWARM_GIT_URL",
    "JIUWENSWARM_GIT_REF",
    "JIUWENSWARM_GIT_REF_KIND",
    "JIUWENSWARM_AGENT_MODE",
    "JIUWENSWARM_MAX_TURNS",
    "TEST_MEMORY_MB",
    "TEST_MEMORY_ATTEMPTS",
];

/// Set by the dispatcher's Prepare harness stage on every shard: the identity
/// its bundle must record. A shard job without the parameter is not sent it.
const SHARD_PINS: &[&str] = &[
    "ICODE_RELEASE_SHA256",
    "ICODE_GIT_SHA",
    "JIUWENSWARM_GIT_SHA",
    "JIUWENSWARM_RELEASE_FROM",
    "JIUWENSWARM_RELEASE_SHA256",
];

/// What a job is for. `one_task` is the only shape that evaluates; the other
/// two split their questions into shards that run as `one_task` builds.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub enum JobShape {
    /// One question, one rollout by default. Also the shard runner.
    #[default]
    One,
    /// Dispatcher over a list of questions (TASKS) or the first N (N_TASKS).
    Some_,
    /// Dispatcher over the whole suite.
    FullSuite,
    /// `build_jiuwenswarm_headless`: the JiuwenSwarm dispatcher on DeepSWE,
    /// over a list (TASKS) or the first N (N_TASKS, all by default). Its
    /// shards are `deepswe_one_task` builds with HARNESS=jiuwenswarm.
    Headless,
}

/// The JiuwenSwarm entry job, written by `mac-k3d config` like the suite jobs.
pub const JIUWENSWARM_HEADLESS: &str = "build_jiuwenswarm_headless";

impl JobShape {
    fn suffix(self) -> &'static str {
        match self {
            JobShape::One => "one_task",
            JobShape::Some_ => "some_task",
            JobShape::FullSuite => "full_suite_task",
            JobShape::Headless => "headless",
        }
    }

    fn is_dispatcher(self) -> bool {
        self != JobShape::One
    }

    /// `mac-k3d eval --job`: `one`, `some`, `full`, or the job suffix itself.
    pub fn from_cli(s: &str) -> Option<Self> {
        match s.trim().to_ascii_lowercase().as_str() {
            "one" | "one_task" => Some(JobShape::One),
            "some" | "some_task" => Some(JobShape::Some_),
            "full" | "full_suite" | "full_suite_task" => Some(JobShape::FullSuite),
            _ => None,
        }
    }
}

/// Who reads the job page. `User` sees each shape's short parameter list;
/// `Developer` also sees the pins, canary and scheduling knobs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UiProfile {
    User,
    Developer,
}

impl UiProfile {
    pub fn parse(s: &str) -> Self {
        if s.trim().eq_ignore_ascii_case("developer") {
            UiProfile::Developer
        } else {
            UiProfile::User
        }
    }
}

/// `deepswe_one_task`, `lolbench_some_task`, `swebenchpro_full_suite_task`, …
/// and `build_jiuwenswarm_headless`.
pub fn eval_job_name(job_benchmark: &str, shape: JobShape) -> String {
    match shape {
        JobShape::Headless => JIUWENSWARM_HEADLESS.into(),
        _ => format!("{job_benchmark}_{}", shape.suffix()),
    }
}

fn benchmark_label(job_benchmark: &str) -> &'static str {
    match job_benchmark {
        "lolbench" => "LoLBench",
        "deepswe" => "DeepSWE",
        "swebenchpro" => "SWE-bench Pro",
        _ => "this benchmark",
    }
}

/// Jenkins job blurb. Same eval goal on every suite: a harness plus an LLM call.
fn job_description(job_benchmark: &str) -> String {
    let label = benchmark_label(job_benchmark);
    let extra = if job_benchmark == "swebenchpro" {
        " The task image is large; use one TASK."
    } else {
        ""
    };
    let harness = if eval_catalog::harness_supports("jiuwenswarm", job_benchmark) {
        "a harness (HARNESS: iCode or JiuwenSwarm)"
    } else {
        "the iCode harness"
    };
    format!(
        "Evaluate {harness} with an LLM call on one {label} task. Harbor runs the harness plus the DeepSeek catalog model, then the same model with no harness.{extra} See docs/user-guide.md."
    )
}

fn shape_description(job_benchmark: &str, shape: JobShape) -> String {
    let label = benchmark_label(job_benchmark);
    let size = full_suite_size(job_benchmark);
    let shard_job = eval_job_name(job_benchmark, JobShape::One);
    match shape {
        JobShape::One => {
            let readers = shard_readers(job_benchmark);
            let (last, rest) = readers.split_last().expect("every suite has dispatchers");
            format!(
                "{} It also runs each shard of {} and {last}; those builds say so in their description.",
                job_description(job_benchmark),
                rest.join(", ")
            )
        }
        JobShape::Some_ => format!(
            "Evaluate the iCode harness on a list of {label} questions (TASKS) or the first N (N_TASKS) at the full rollout count. Each question and rollout is one {shard_job} build; Jenkins places it. This build merges them into one report. See docs/user-guide.md."
        ),
        JobShape::FullSuite => format!(
            "Run the whole {label} suite ({size} questions) at N_ROLLOUTS attempts each. Each question and rollout is one {shard_job} build; Jenkins places it. This build merges them into one report. See docs/harbor-delegation-multiworker/README.md."
        ),
        JobShape::Headless => format!(
            "Evaluate the JiuwenSwarm harness on {label}: a list of questions (TASKS), or else the first N_TASKS (all {size} by default), at N_ROLLOUTS attempts each. Prepare harness builds JiuwenSwarm once from JIUWENSWARM_MODE (a git ref, or an uploaded .whl, wheelhouse .tar.gz or mac-k3d bundle); each question and rollout is one {shard_job} build that runs that bundle. This build merges them into one report. See docs/jiuwenswarm-harness-inputs.md."
        ),
    }
}

/// Questions in the whole suite, the default N_TASKS of a full-suite run.
pub fn full_suite_size(job_benchmark: &str) -> u32 {
    match job_benchmark {
        "deepswe" => 113,
        "lolbench" => 20,
        "swebenchpro" => 731,
        _ => 1,
    }
}

fn task_param_description(job_benchmark: &str) -> String {
    match job_benchmark {
        "lolbench" => {
            "One LoLBench question id. Example: ruff_1. Leave empty to run the first sorted id. For several questions use lolbench_some_task.".into()
        }
        "deepswe" => {
            "One DeepSWE question id. Leave empty to run the first sorted id. For several questions use deepswe_some_task.".into()
        }
        "swebenchpro" => {
            "One SWE-bench Pro instance id. Leave empty to run the first sorted id. The image is large. For several questions use swebenchpro_some_task.".into()
        }
        _ => "One question id. Leave empty to run the first sorted id.".into(),
    }
}

fn n_tasks_param_description(job_benchmark: &str, shape: JobShape) -> String {
    let size = full_suite_size(job_benchmark);
    match shape {
        JobShape::FullSuite => format!(
            "How many questions of the suite to run, lowest ids first. The full suite is {size}; lower it for a rehearsal."
        ),
        JobShape::Headless => format!(
            "Used only when TASKS is empty: the first N sorted question ids. The default, {size}, is the whole suite."
        ),
        _ => format!(
            "Used only when TASKS is empty: the first N sorted question ids. Full suite is {size}; use the full_suite_task job for all of them."
        ),
    }
}

fn benchmark_param_description(job_benchmark: &str) -> String {
    format!(
        "The benchmark this job evaluates the harness on: {}. Fixed for this job; run another suite job to use a different benchmark.",
        benchmark_label(job_benchmark)
    )
}

fn harness_param_description(choices: &[&str], shape: JobShape) -> String {
    if choices.len() > 1 {
        return format!(
            "The harness this build evaluates: {}. The first is the controller's default (mac-k3d set --harness). A dispatcher's shards always run its own harness.",
            choices.join(" or ")
        );
    }
    match shape {
        JobShape::Headless => "The harness this job evaluates: JiuwenSwarm, on DeepSWE. For iCode use deepswe_some_task.".into(),
        JobShape::Some_ | JobShape::FullSuite if eval_catalog::HARNESSES.len() > 1 => format!(
            "The harness this job evaluates: iCode. For JiuwenSwarm on DeepSWE use {JIUWENSWARM_HEADLESS}."
        ),
        _ => "The harness this job evaluates. Fixed so every build names what it tested.".into(),
    }
}

/// HARNESS on this job's page: a choice on a `one_task` whose benchmark more
/// than one harness runs on, with the controller's default first when the
/// benchmark runs it. Fixed on a dispatcher, whose page has that harness's
/// inputs: the suite dispatchers iCode's, `build_jiuwenswarm_headless` JiuwenSwarm's.
fn harness_choices<'a>(job_benchmark: &str, shape: JobShape, opts: &'a JobOpts) -> Vec<&'a str> {
    match shape {
        JobShape::One => eval_catalog::choices_preferred_first(eval_catalog::HARNESSES, &opts.default_harness)
            .into_iter()
            .filter(|h| eval_catalog::harness_supports(h, job_benchmark))
            .collect(),
        JobShape::Headless => vec!["jiuwenswarm"],
        JobShape::Some_ | JobShape::FullSuite => vec!["icode"],
    }
}

fn llm_param_description() -> &'static str {
    "The LLM family the harness calls. Fixed for this job; to change it run mac-k3d set --llm on the controller, then mac-k3d config --skip-secrets. DEEPSEEK_MODEL picks the exact model."
}

/// Options used when rendering / updating `lolbench_one_task`.
#[derive(Debug, Clone)]
pub struct JobOpts {
    pub default_task: String,
    pub default_eval_mode: String,
    pub default_icode_release: String,
    pub default_icode_git_url: String,
    pub default_icode_git_ref: String,
    pub default_icode_git_ref_kind: String,
    pub default_jiuwenswarm_git_url: String,
    pub default_jiuwenswarm_git_ref: String,
    pub default_jiuwenswarm_git_ref_kind: String,
    pub default_icode_args: String,
    pub default_harness: String,
    pub default_llm: String,
    pub default_deepseek_model: String,
    pub default_benchmark: String,
    pub default_n_tasks: u32,
    pub default_n_rollouts: u32,
    pub default_tasks: Vec<String>,
    pub default_shard_size: u32,
    /// 0 = 2 x online executors at dispatch. Above 0 fixes MAX_IN_FLIGHT.
    pub max_in_flight: u32,
    /// Times an OOM rollout is put back at the tail. Default 2.
    pub oom_retries: u32,
    pub ui_profile: UiProfile,
    /// Credential IDs present in Jenkins (only these are bound in the Pipeline).
    pub credential_ids: Vec<String>,
    /// Non-empty value ticks "Trigger builds remotely" on every eval job.
    pub remote_trigger_token: String,
}

impl JobOpts {
    pub fn from_config(config: &crate::config::MacK3dConfig, credential_ids: Vec<String>) -> Self {
        let mut release = config.jenkins_job.default_icode_release.trim().to_string();
        if release.is_empty() {
            // Older YAML used default_binary_target for a raw file path.
            release = config.jenkins_job.default_binary_target.trim().to_string();
        }
        let git_ref = config.jenkins_job.default_icode_git_ref.trim();
        let git_ref_kind = config.jenkins_job.default_icode_git_ref_kind.trim();
        let harness = if config.jenkins_job.default_harness.trim().is_empty() {
            eval_catalog::HARNESSES[0].to_string()
        } else {
            config
                .jenkins_job
                .default_harness
                .trim()
                .to_ascii_lowercase()
        };
        let llm = {
            let live = config.jenkins_job.default_llm.trim();
            let alias = config.jenkins_job.default_model.trim();
            let raw = if live.is_empty() { alias } else { live };
            if raw.is_empty() {
                eval_catalog::LLMS[0].to_string()
            } else {
                raw.to_ascii_lowercase()
            }
        };
        let deepseek_model = {
            let raw = config.jenkins_job.default_deepseek_model.trim();
            if raw.is_empty() {
                eval_catalog::default_model().to_string()
            } else {
                eval_catalog::require_model(raw)
                    .unwrap_or_else(|_| eval_catalog::default_model().to_string())
            }
        };
        let n_rollouts = config.jenkins_job.default_n_rollouts.max(1);
        Self {
            default_task: if config.jenkins_job.default_task.trim().is_empty() {
                String::new()
            } else {
                config.jenkins_job.default_task.clone()
            },
            default_eval_mode: normalize_eval_mode(&config.jenkins_job.default_eval_mode),
            default_icode_release: release,
            default_icode_git_url: config.jenkins_job.default_icode_git_url.clone(),
            default_icode_git_ref: if git_ref.is_empty() {
                "2".into()
            } else {
                git_ref.to_string()
            },
            default_icode_git_ref_kind: match git_ref_kind.to_ascii_lowercase().as_str() {
                "tag" | "commit" | "pr" | "branch" => git_ref_kind.to_ascii_lowercase(),
                _ => "pr".into(),
            },
            default_jiuwenswarm_git_url: config.jenkins_job.default_jiuwenswarm_git_url.trim().to_string(),
            default_jiuwenswarm_git_ref: match config.jenkins_job.default_jiuwenswarm_git_ref.trim() {
                "" => "2".into(),
                r => r.to_string(),
            },
            default_jiuwenswarm_git_ref_kind: {
                let kind = config.jenkins_job.default_jiuwenswarm_git_ref_kind.trim().to_ascii_lowercase();
                if eval_catalog::ICODE_GIT_REF_KINDS.contains(&kind.as_str()) { kind } else { "pr".into() }
            },
            default_icode_args: config.jenkins_job.default_icode_args.clone(),
            default_harness: harness,
            default_llm: llm,
            default_deepseek_model: deepseek_model,
            default_benchmark: config
                .jenkins_job
                .default_benchmark
                .trim()
                .to_ascii_lowercase(),
            default_n_tasks: config.jenkins_job.default_n_tasks.max(1),
            default_n_rollouts: n_rollouts,
            default_tasks: config.jenkins_job.default_tasks.clone(),
            // Controllers that first wrote their YAML under the old default keep
            // `default_shard_size: 10`. Treat that as the previous built-in so a
            // redeploy shows 2; any other saved value (including set --shard-size)
            // is left alone.
            default_shard_size: match config.jenkins_job.default_shard_size {
                10 => 2,
                n => n.max(1),
            },
            max_in_flight: config.jenkins_job.max_in_flight,
            oom_retries: config.jenkins_job.oom_retries,
            ui_profile: UiProfile::parse(&config.jenkins_job.ui_profile),
            credential_ids,
            remote_trigger_token: config.jenkins.remote_trigger_token.trim().to_string(),
        }
    }
}

/// Fill `jenkins.remote_trigger_token` when it is empty, save the controller
/// config, and print the remote-trigger URL only on that first write.
pub fn ensure_remote_trigger_token(
    config: &mut crate::config::MacK3dConfig,
    config_path: Option<&Path>,
) -> Result<()> {
    if !config.jenkins.remote_trigger_token.trim().is_empty() {
        return Ok(());
    }
    let token = generate_remote_trigger_token()?;
    config.jenkins.remote_trigger_token = token.clone();
    let path_label = config_path
        .map(|p| p.display().to_string())
        .unwrap_or_else(|| crate::config::MacK3dConfig::default_config_path().display().to_string());
    match config.save(config_path) {
        Ok(()) => {
            let base = crate::runtime::jenkins::ui_url(config);
            println!(
                "Remote build trigger enabled on all {} jobs.\n\
                 Token saved in {path_label} as jenkins.remote_trigger_token (export omits it).\n\
                 Example: {base}/job/{DEEPSWE_ONE_TASK}/buildWithParameters?token={token}",
                eval_jobs().len()
            );
        }
        Err(err) => {
            println!(
                "Warning: generated a remote trigger token but could not save it to {path_label} ({err}). \
                 This rewrite still ticks the checkbox; the next config generates a new token."
            );
        }
    }
    Ok(())
}

fn generate_remote_trigger_token() -> Result<String> {
    use std::io::Read;
    let mut buf = [0u8; 18];
    let mut file = std::fs::File::open("/dev/urandom").map_err(|e| {
        Error::Config(format!("could not read /dev/urandom for the remote trigger token: {e}"))
    })?;
    file.read_exact(&mut buf).map_err(|e| {
        Error::Config(format!("could not read /dev/urandom for the remote trigger token: {e}"))
    })?;
    Ok(buf.iter().map(|b| format!("{b:02x}")).collect())
}

fn normalize_eval_mode(s: &str) -> String {
    match s.trim().to_ascii_lowercase().as_str() {
        "git" | "source" => "git".into(),
        "release" | "binary" | "" => "release".into(),
        other => other.to_string(),
    }
}

fn jenkins_icode_mode(opts: &JobOpts) -> &'static str {
    match opts.default_eval_mode.trim().to_ascii_lowercase().as_str() {
        "git" | "source" => "git",
        _ => "release",
    }
}

/// Ensure Pipeline job `lolbench_one_task` exists on the controller (create or update).
pub fn ensure_lolbench_one_task(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    opts: &JobOpts,
) -> Result<()> {
    let base = jenkins_url.trim_end_matches('/');

    wait_for_jenkins(base, api_user, api_token_or_password, Duration::from_secs(90))?;

    let session = Session::open(base, api_user, api_token_or_password)?;
    let exists = session.status(&format!("/job/{LOLBENCH_ONE_TASK}/api/json")) == Some(200);

    if exists {
        println!("Updating Jenkins job '{LOLBENCH_ONE_TASK}' Pipeline definition…");
        match update_job_script(&session, opts) {
            Ok(()) => println!("Updated job '{LOLBENCH_ONE_TASK}'."),
            Err(err) => println!(
                "Warning: failed to update job '{LOLBENCH_ONE_TASK}' ({err}).\n\
                 Delete the job in the UI and re-run `mac-k3d config`, or see docs/jenkins-jobs.md."
            ),
        }
        return Ok(());
    }

    let xml = job_config_xml(opts);
    let create = format!("/createItem?name={}", urlencoding_simple(LOLBENCH_ONE_TASK));
    println!("Creating Jenkins job '{LOLBENCH_ONE_TASK}' on {base} …");

    let reply = session.post_xml(&create, "text/xml", &xml)?;
    if !reply.is(&["200", "201", "302", "303"]) {
        let snippet: String = reply.body.chars().take(280).collect();
        println!(
            "Warning: failed to create job '{LOLBENCH_ONE_TASK}' (HTTP {}). {snippet}\n\
             Create it manually — see docs/jenkins-jobs.md.",
            reply.code
        );
        return Ok(());
    }

    println!(
        "Created job '{LOLBENCH_ONE_TASK}'.\n\
         Trigger: {base}/job/{LOLBENCH_ONE_TASK}/buildWithParameters"
    );
    Ok(())
}

fn update_job_script(session: &Session, opts: &JobOpts) -> Result<()> {
    let xml_ok = update_job_config_xml(session, opts).is_ok();
    if xml_ok {
        return Ok(());
    }
    let script = jenkinsfile(opts);
    let b64 = base64_encode(script.as_bytes());
    let groovy = format!(
        r#"
import jenkins.model.Jenkins
import org.jenkinsci.plugins.workflow.job.WorkflowJob
import org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition
import java.util.Base64
def name = '{name}'
def body = new String(Base64.decoder.decode('{b64}'), 'UTF-8')
def job = Jenkins.instance.getItemByFullName(name, WorkflowJob.class)
if (job == null) {{
  throw new IllegalStateException('missing job ' + name)
}}
job.setDefinition(new CpsFlowDefinition(body, true))
job.save()
println('updated-script')
"#,
        name = LOLBENCH_ONE_TASK,
        b64 = b64,
    );

    let output = session.script_text(&groovy)?;
    let body = String::from_utf8_lossy(&output.stdout).to_string();
    if !output.status.success() || !body.contains("updated-script") {
        return Err(Error::Config(truncate(&body, 300)));
    }
    Ok(())
}

fn update_job_config_xml(session: &Session, opts: &JobOpts) -> Result<()> {
    let xml = job_config_xml(opts);
    let reply = session.post_xml(&format!("/job/{LOLBENCH_ONE_TASK}/config.xml"), "text/xml", &xml)?;
    if !reply.is(&["200", "201", "204"]) {
        return Err(Error::Config(format!("config.xml HTTP {}", reply.code)));
    }
    Ok(())
}

fn base64_encode(bytes: &[u8]) -> String {
    const TABLE: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for chunk in bytes.chunks(3) {
        let b0 = chunk[0] as u32;
        let b1 = chunk.get(1).copied().unwrap_or(0) as u32;
        let b2 = chunk.get(2).copied().unwrap_or(0) as u32;
        let n = (b0 << 16) | (b1 << 8) | b2;
        out.push(TABLE[((n >> 18) & 63) as usize] as char);
        out.push(TABLE[((n >> 12) & 63) as usize] as char);
        if chunk.len() > 1 {
            out.push(TABLE[((n >> 6) & 63) as usize] as char);
        } else {
            out.push('=');
        }
        if chunk.len() > 2 {
            out.push(TABLE[(n & 63) as usize] as char);
        } else {
            out.push('=');
        }
    }
    out
}

fn truncate(s: &str, max: usize) -> String {
    let t: String = s.chars().take(max).collect();
    if s.chars().count() > max {
        format!("{t}…")
    } else {
        t
    }
}

/// Create/update the job using the in-cluster Jenkins admin password from kubectl.
pub async fn ensure_lolbench_one_task_from_cluster(
    kubectl: &Path,
    config: &crate::config::MacK3dConfig,
    credential_ids: Vec<String>,
) -> Result<()> {
    if !config.jenkins.enabled {
        return Ok(());
    }
    let url = crate::runtime::jenkins::ui_url(config);
    let password = match crate::runtime::jenkins::admin_password(kubectl, config).await {
        Ok(p) if !p.is_empty() => p,
        Ok(_) | Err(_) => {
            println!(
                "Skipping '{LOLBENCH_ONE_TASK}' create — could not read Jenkins admin password yet."
            );
            return Ok(());
        }
    };
    let opts = JobOpts::from_config(config, credential_ids);
    ensure_lolbench_one_task(&url, "admin", &password, &opts)
}

fn wait_for_jenkins(base: &str, user: &str, token: &str, timeout: Duration) -> Result<()> {
    let start = std::time::Instant::now();
    loop {
        if jenkins_curl::reachable(base, user, token) {
            return Ok(());
        }
        if start.elapsed() >= timeout {
            return Err(Error::Config(format!(
                "Jenkins at {base} not reachable within {}s (needed to create '{LOLBENCH_ONE_TASK}')",
                timeout.as_secs()
            )));
        }
        thread::sleep(Duration::from_secs(2));
    }
}

fn groovy_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'")
}

#[allow(dead_code)]
fn eval_mode_choices(default_mode: &str) -> (&'static str, &'static str) {
    if default_mode == "git" {
        ("git", "release")
    } else {
        ("release", "git")
    }
}

const GIT_PAT_CREDENTIALS: [&str; 2] = ["gitcode-pat", "github-pat"];

/// Bind listed Jenkins credential IDs into the Pipeline. Empty IDs omit the block
/// (`--skip-secrets` / `start` must pass IDs from `existing_ids_on_controller`, not `[]`).
fn with_credentials_block(credential_ids: &[String]) -> (String, String) {
    use crate::prepare::jenkins_credentials::CREDENTIAL_DEFS;
    let mut binds = Vec::new();
    for def in CREDENTIAL_DEFS {
        if credential_ids.iter().any(|id| id == def.id) {
            binds.push(format!(
                "string(credentialsId: '{}', variable: '{}')",
                def.id, def.env_var
            ));
        }
    }
    if binds.is_empty() {
        return (String::new(), String::new());
    }
    let open = format!("          withCredentials([{}]) {{\n", binds.join(", "));
    let close = "          }\n".to_string();
    (open, close)
}

fn job_config_xml(opts: &JobOpts) -> String {
    one_task_job_xml("lolbench", &shape_description("lolbench", JobShape::One), opts)
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

fn question_defaults_for_job(opts: &JobOpts, job_benchmark: &str) -> (String, u32, String) {
    let _ = job_benchmark;
    let tasks = opts.default_tasks.join(",");
    let n = if opts.default_tasks.is_empty() {
        opts.default_n_tasks.max(1)
    } else {
        opts.default_tasks.len() as u32
    };
    let task = if opts.default_tasks.is_empty() {
        opts.default_task.clone()
    } else {
        String::new()
    };
    (task, n, tasks)
}

/// TASK / N_TASKS / TASKS / N_ROLLOUTS defaults for one shape.
///
/// `one_task` pins a single question at one rollout so a smoke run costs one
/// agent call. `some_task` keeps the configured list or N at the full rollout
/// count. `full_suite_task` defaults N_TASKS to the whole suite.
fn shape_defaults(opts: &JobOpts, job_benchmark: &str, shape: JobShape) -> (String, u32, String, u32) {
    let (task, n_tasks, tasks) = question_defaults_for_job(opts, job_benchmark);
    let rollouts = opts.default_n_rollouts.max(1);
    match shape {
        JobShape::One => {
            let task = if task.is_empty() {
                opts.default_tasks.first().cloned().unwrap_or_default()
            } else {
                task
            };
            (task, 1, String::new(), 1)
        }
        JobShape::Some_ => (String::new(), n_tasks.max(2), tasks, rollouts),
        JobShape::FullSuite | JobShape::Headless => (
            String::new(),
            full_suite_size(job_benchmark),
            String::new(),
            rollouts,
        ),
    }
}

fn groovy_quoted_list(items: &[&str]) -> String {
    items
        .iter()
        .map(|s| format!("'{}'", groovy_escape(s)))
        .collect::<Vec<_>>()
        .join(", ")
}

fn xml_choice_strings(items: &[&str]) -> String {
    items
        .iter()
        .map(|s| format!("              <string>{}</string>", xml_escape(s)))
        .collect::<Vec<_>>()
        .join("\n")
}

/// Who sees a parameter on Build with Parameters.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Show {
    Always,
    /// Developer profile only; in the user profile it is a hidden param that keeps its default.
    Developer,
    /// Always hidden: the dispatcher sets it on a shard build.
    Never,
}

#[derive(Debug, Clone)]
enum ParamKind {
    /// The first choice is the default.
    Choice(Vec<String>),
    Text(String),
    StashedFile,
}

/// One job parameter. The Groovy `parameters {}` block and the XML
/// `<parameterDefinitions>` both render from the same list, so they cannot drift.
#[derive(Debug, Clone)]
struct ParamSpec {
    name: &'static str,
    kind: ParamKind,
    description: String,
    show: Show,
}

impl ParamSpec {
    fn visible(&self, profile: UiProfile) -> bool {
        match self.show {
            Show::Always => true,
            Show::Developer => profile == UiProfile::Developer,
            Show::Never => false,
        }
    }

    fn default_value(&self) -> String {
        match &self.kind {
            ParamKind::Choice(c) => c.first().cloned().unwrap_or_default(),
            ParamKind::Text(d) => d.clone(),
            ParamKind::StashedFile => String::new(),
        }
    }
}

fn choice_param(name: &'static str, choices: &[&str], description: &str, show: Show) -> ParamSpec {
    ParamSpec {
        name,
        kind: ParamKind::Choice(choices.iter().map(|s| s.to_string()).collect()),
        description: description.to_string(),
        show,
    }
}

fn text_param(name: &'static str, default: impl ToString, description: &str, show: Show) -> ParamSpec {
    ParamSpec {
        name,
        kind: ParamKind::Text(default.to_string()),
        description: description.to_string(),
        show,
    }
}

/// JiuwenSwarm's inputs (mode, release upload, git URL / ref / kind) and its
/// run settings (agent mode, max turns), each shown as `show` says.
fn jiuwenswarm_params(opts: &JobOpts, show: Show) -> (Vec<ParamSpec>, Vec<ParamSpec>) {
    let kinds = eval_catalog::choices_preferred_first(
        eval_catalog::ICODE_GIT_REF_KINDS,
        &opts.default_jiuwenswarm_git_ref_kind,
    );
    let inputs = vec![
        choice_param("JIUWENSWARM_MODE", eval_catalog::JIUWENSWARM_MODES, DESC_JIUWENSWARM_MODE, show),
        ParamSpec {
            name: "JIUWENSWARM_RELEASE_FILE",
            kind: ParamKind::StashedFile,
            description: DESC_JIUWENSWARM_RELEASE_FILE.into(),
            show,
        },
        text_param("JIUWENSWARM_GIT_URL", opts.default_jiuwenswarm_git_url.trim(), DESC_JIUWENSWARM_GIT_URL, show),
        text_param("JIUWENSWARM_GIT_REF", &opts.default_jiuwenswarm_git_ref, DESC_JIUWENSWARM_GIT_REF, show),
        choice_param("JIUWENSWARM_GIT_REF_KIND", &kinds, DESC_JIUWENSWARM_GIT_REF_KIND, show),
    ];
    let settings = vec![
        choice_param("JIUWENSWARM_AGENT_MODE", eval_catalog::JIUWENSWARM_AGENT_MODES, DESC_JIUWENSWARM_AGENT_MODE, show),
        text_param(
            "JIUWENSWARM_MAX_TURNS",
            eval_catalog::JIUWENSWARM_MAX_TURNS_DEFAULT,
            DESC_JIUWENSWARM_MAX_TURNS,
            show,
        ),
    ];
    (inputs, settings)
}

/// Every parameter of one job, in display order. HARNESS / LLM / BENCHMARK come
/// first on every shape and profile so a build always says what it evaluates.
fn eval_params(job_benchmark: &str, shape: JobShape, opts: &JobOpts) -> Vec<ParamSpec> {
    use Show::{Always, Developer, Never};
    let (task, n_tasks, tasks, n_rollouts) = shape_defaults(opts, job_benchmark, shape);
    let model_choices =
        eval_catalog::choices_preferred_first(eval_catalog::MODELS, &opts.default_deepseek_model);
    let git_kind_choices = eval_catalog::choices_preferred_first(
        eval_catalog::ICODE_GIT_REF_KINDS,
        &opts.default_icode_git_ref_kind,
    );
    let harnesses = harness_choices(job_benchmark, shape, opts);
    let mut p = vec![
        choice_param("HARNESS", &harnesses, &harness_param_description(&harnesses, shape), Always),
        choice_param("LLM", &[opts.default_llm.as_str()], llm_param_description(), Always),
        choice_param("BENCHMARK", &[job_benchmark], &benchmark_param_description(job_benchmark), Always),
    ];
    // The JiuwenSwarm job has its inputs in place of iCode's.
    let icode_inputs = shape != JobShape::Headless;
    match shape {
        JobShape::One => {
            p.push(text_param("TASK", task, &task_param_description(job_benchmark), Always));
            // One build is one rollout. Several rollouts of one question go
            // through some_task with TASKS set to that id.
            p.push(text_param("N_ROLLOUTS", 1, "Fixed: one rollout. Use some_task for more.", Never));
            p.push(choice_param(
                "ICODE_MODE",
                &eval_catalog::choices_preferred_first(eval_catalog::ICODE_CI_MODES, jenkins_icode_mode(opts)),
                DESC_ICODE_MODE,
                Always,
            ));
            p.push(ParamSpec {
                name: "ICODE_RELEASE_FILE",
                kind: ParamKind::StashedFile,
                description: DESC_ICODE_RELEASE_FILE.into(),
                show: Always,
            });
        }
        JobShape::Some_ | JobShape::Headless => {
            p.push(text_param("TASKS", tasks, DESC_TASKS, Always));
            p.push(text_param("N_TASKS", n_tasks, &n_tasks_param_description(job_benchmark, shape), Always));
        }
        JobShape::FullSuite => {}
    }
    if shape.is_dispatcher() {
        p.push(text_param(
            "N_ROLLOUTS",
            n_rollouts,
            &format!("Attempts per question. Default {n_rollouts}. Every shard uses the same value, and pass@k is reported up to this k."),
            Always,
        ));
    }
    if shape.is_dispatcher() && icode_inputs {
        p.push(choice_param(
            "ICODE_MODE",
            &eval_catalog::choices_preferred_first(eval_catalog::ICODE_CI_MODES, jenkins_icode_mode(opts)),
            DESC_ICODE_MODE,
            Always,
        ));
        p.push(ParamSpec {
            name: "ICODE_RELEASE_FILE",
            kind: ParamKind::StashedFile,
            description: DESC_ICODE_RELEASE_FILE.into(),
            show: Always,
        });
    }
    if icode_inputs {
        let git_url_desc = if shape.is_dispatcher() { DESC_DISPATCH_ICODE_GIT_URL } else { DESC_ICODE_GIT_URL };
        p.push(text_param("ICODE_GIT_URL", opts.default_icode_git_url.trim(), git_url_desc, Always));
        p.push(text_param("ICODE_GIT_REF", &opts.default_icode_git_ref, DESC_ICODE_GIT_REF, Always));
        p.push(choice_param("ICODE_GIT_REF_KIND", &git_kind_choices, DESC_ICODE_GIT_REF_KIND, Always));
    }
    let headless_fields = (shape == JobShape::Headless).then(|| jiuwenswarm_params(opts, Always));
    if let Some((inputs, _)) = &headless_fields {
        p.extend(inputs.iter().cloned());
    }
    p.push(choice_param("DEEPSEEK_MODEL", &model_choices, DESC_DEEPSEEK_MODEL, Always));
    if let Some((_, settings)) = headless_fields {
        p.extend(settings);
    }
    // For a direct JiuwenSwarm build of deepswe_one_task: developer page only.
    if shape == JobShape::One && eval_catalog::harness_supports("jiuwenswarm", job_benchmark) {
        let (inputs, settings) = jiuwenswarm_params(opts, Developer);
        p.extend(inputs);
        p.extend(settings);
    }

    match shape {
        JobShape::One => {
            p.push(text_param("TASKS", "", DESC_SHARD_TASKS, Never));
            p.push(text_param("N_TASKS", 1, DESC_SHARD_N_TASKS, Never));
            p.push(text_param("TASK_OFFSET", 0, DESC_TASK_OFFSET, Never));
            p.push(text_param("RUN_GROUP", "", DESC_RUN_GROUP, Never));
            p.push(text_param("SHARD", "", DESC_SHARD, Never));
            p.push(text_param("REQUESTED_BY", "", DESC_REQUESTED_BY, Never));
            p.push(text_param("HARNESS_BUNDLE_FROM", "", DESC_HARNESS_BUNDLE_FROM, Never));
            p.push(text_param("HARNESS_BUNDLE_SHA256", "", DESC_HARNESS_BUNDLE_SHA256, Never));
            p.push(text_param("ICODE_RELEASE_SHA256", "", DESC_ICODE_RELEASE_SHA256, Never));
            p.push(text_param("ICODE_GIT_SHA", "", DESC_ICODE_GIT_SHA, Never));
            if eval_catalog::harness_supports("jiuwenswarm", job_benchmark) {
                p.push(text_param("JIUWENSWARM_GIT_SHA", "", DESC_JIUWENSWARM_GIT_SHA, Never));
                p.push(text_param("JIUWENSWARM_RELEASE_FROM", "", DESC_JIUWENSWARM_RELEASE_FROM, Never));
                p.push(text_param("JIUWENSWARM_RELEASE_SHA256", "", DESC_JIUWENSWARM_RELEASE_SHA256, Never));
            }
            p.push(text_param("ROLLOUT", 1, DESC_ROLLOUT, Never));
            p.push(text_param("ATTEMPT", 1, DESC_ATTEMPT, Never));
            p.push(text_param("OOM_RETRIES", opts.oom_retries, DESC_OOM_RETRIES, Never));
        }
        JobShape::Some_ | JobShape::Headless => {}
        JobShape::FullSuite => {
            p.push(text_param("N_TASKS", n_tasks, &n_tasks_param_description(job_benchmark, shape), Developer));
        }
    }
    if shape.is_dispatcher() {
        p.push(text_param("MAX_IN_FLIGHT", opts.max_in_flight, DESC_MAX_IN_FLIGHT, Developer));
        p.push(text_param("OOM_RETRIES", opts.oom_retries, DESC_OOM_RETRIES, Developer));
    }
    p.push(text_param("TEST_MEMORY_MB", "", DESC_TEST_MEMORY_MB, Developer));
    p.push(text_param("TEST_MEMORY_ATTEMPTS", 1, DESC_TEST_MEMORY_ATTEMPTS, Developer));
    p.push(text_param("AGENT_LABEL", DEFAULT_AGENT_LABEL, DESC_AGENT_LABEL, Developer));
    if shape.is_dispatcher() {
        p.push(text_param("AGGREGATE_LABEL", "", DESC_AGGREGATE_LABEL, Developer));
    }
    p.extend([
        text_param(
            "HARBOR_VERSION",
            crate::prepare::toolchain::harbor_version(),
            "Harbor version the Environment stage installs and checks (pin: pipeline/config/toolchain.env). A mismatch fails the stage.",
            Developer,
        ),
        text_param("DEEPSWE_REF", "0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea", "DeepSWE commit pinned by P2.", Developer),
    ]);
    if shape != JobShape::Headless {
        p.push(text_param("LOLBENCH_REF", "1b10d10bb4a10cea54374ac34b8f76b69dc8ce75", "LoLBench commit pinned by P2.", Developer));
    }
    for spec in &mut p {
        if spec.show == Developer {
            spec.description = format!("{DEVELOPER_PREFIX}{}", spec.description);
        }
    }
    p
}

/// One field of a job's Build with Parameters page, as `mac-k3d eval --job` sees it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct JobParam {
    pub name: &'static str,
    /// False for shard plumbing that only a dispatcher sets on a shard build.
    pub settable: bool,
    /// Shown only in the developer profile; a hidden parameter in the user profile.
    pub developer: bool,
}

/// The fields of `<job_benchmark>_<shape>` in page order. Names and visibility
/// do not depend on the controller's config, only defaults do.
pub fn job_params(job_benchmark: &str, shape: JobShape) -> Vec<JobParam> {
    let opts = JobOpts::from_config(&crate::config::MacK3dConfig::default(), Vec::new());
    eval_params(job_benchmark, shape, &opts)
        .into_iter()
        .map(|p| JobParam {
            name: p.name,
            settable: p.show != Show::Never,
            developer: p.show == Show::Developer,
        })
        .collect()
}

fn groovy_params(params: &[ParamSpec], profile: UiProfile) -> String {
    params
        .iter()
        .map(|p| {
            let name = p.name;
            let desc = groovy_escape(&p.description);
            if !p.visible(profile) {
                let default = groovy_escape(&p.default_value());
                return format!("    hidden(name: '{name}', defaultValue: '{default}', description: '{desc}')");
            }
            match &p.kind {
                ParamKind::Choice(choices) => {
                    let refs: Vec<&str> = choices.iter().map(String::as_str).collect();
                    format!(
                        "    choice(name: '{name}', choices: [{}], description: '{desc}')",
                        groovy_quoted_list(&refs)
                    )
                }
                ParamKind::Text(default) => format!(
                    "    string(name: '{name}', defaultValue: '{}', description: '{desc}')",
                    groovy_escape(default)
                ),
                ParamKind::StashedFile => format!("    stashedFile(name: '{name}', description: '{desc}')"),
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn xml_params(params: &[ParamSpec], profile: UiProfile) -> String {
    params
        .iter()
        .map(|p| {
            let name = xml_escape(p.name);
            let desc = xml_escape(&p.description);
            if !p.visible(profile) {
                return format!(
                    r#"        <com.wangyin.parameter.WHideParameterDefinition plugin="hidden-parameter">
          <name>{name}</name>
          <description>{desc}</description>
          <defaultValue>{}</defaultValue>
        </com.wangyin.parameter.WHideParameterDefinition>"#,
                    xml_escape(&p.default_value())
                );
            }
            match &p.kind {
                ParamKind::Choice(choices) => {
                    let refs: Vec<&str> = choices.iter().map(String::as_str).collect();
                    format!(
                        r#"        <hudson.model.ChoiceParameterDefinition>
          <name>{name}</name>
          <description>{desc}</description>
          <choices class="java.util.Arrays$ArrayList">
            <a class="string-array">
{}
            </a>
          </choices>
        </hudson.model.ChoiceParameterDefinition>"#,
                        xml_choice_strings(&refs)
                    )
                }
                ParamKind::Text(default) => format!(
                    r#"        <hudson.model.StringParameterDefinition>
          <name>{name}</name>
          <description>{desc}</description>
          <defaultValue>{}</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>"#,
                    xml_escape(default)
                ),
                ParamKind::StashedFile => format!(
                    r#"        <io.jenkins.plugins.file_parameters.StashedFileParameterDefinition>
          <name>{name}</name>
          <description>{desc}</description>
        </io.jenkins.plugins.file_parameters.StashedFileParameterDefinition>"#
                ),
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

/// Jobs allowed to copy a one_task build's artifacts: the dispatchers that run
/// their shards as one_task builds.
fn shard_readers(job_benchmark: &str) -> Vec<String> {
    dispatchers(job_benchmark)
        .into_iter()
        .map(|s| eval_job_name(job_benchmark, s))
        .collect()
}

/// The dispatchers that queue this suite's one_task builds.
fn dispatchers(job_benchmark: &str) -> Vec<JobShape> {
    let mut shapes = vec![JobShape::Some_, JobShape::FullSuite];
    if eval_catalog::harness_supports("jiuwenswarm", job_benchmark) {
        shapes.push(JobShape::Headless);
    }
    shapes
}

const DISPLAY_NAME_GROOVY: &str =
    r##"currentBuild.displayName = "#${env.BUILD_NUMBER} ${params.HARNESS}/${params.DEEPSEEK_MODEL}/${params.BENCHMARK}""##;

/// Sets `env.BUILD_USER`, which provenance records as the report's Requester: the
/// Jenkins user (or API token owner) who started this build, else the user a
/// dispatcher forwarded in `REQUESTED_BY`. A sandbox refusal only warns.
fn requester_groovy(indent: &str) -> String {
    [
        "String requester = ''",
        "try {",
        "  def causes = currentBuild.getBuildCauses('hudson.model.Cause$UserIdCause')",
        "  if (causes) { requester = (causes[0].userId ?: causes[0].userName ?: '').toString() }",
        "} catch (Throwable t) {",
        "  echo \"WARNING: could not read who started this build (${t})\"",
        "}",
        "if (!requester.trim()) { requester = params.REQUESTED_BY?.trim() ?: 'unknown' }",
        "env.BUILD_USER = requester",
    ]
    .join(&format!("\n{indent}"))
}

fn one_task_jenkinsfile(job_benchmark: &str, opts: &JobOpts) -> String {
    eval_jenkinsfile(job_benchmark, JobShape::One, opts)
}

/// One Jenkins stage per pipeline phase, in run order. Each runs
/// `MAC_K3D_PHASE=<phase> run_all.sh` in its own shell; state passes through
/// eval-runs/. Only Evaluate holds this worker's CPU lock.
struct EvalStage {
    title: &'static str,
    phase: &'static str,
    holds_cpu_lock: bool,
    /// An out-of-memory rollout is re-queued whole. Scoring a partial trial
    /// would record the failure twice, once here and once on the retry.
    skips_on_oom: bool,
    /// Copies the dispatcher's harness bundle first, unless this worker's store holds it.
    fetches_bundle: bool,
}

const EVAL_STAGES: [EvalStage; 7] = [
    EvalStage { title: "Environment", phase: "env", holds_cpu_lock: false, skips_on_oom: false, fetches_bundle: false },
    EvalStage { title: "Tasks", phase: "tasks", holds_cpu_lock: false, skips_on_oom: false, fetches_bundle: true },
    EvalStage { title: "Evaluate", phase: "evaluate", holds_cpu_lock: true, skips_on_oom: false, fetches_bundle: false },
    EvalStage { title: "Anti-cheat", phase: "anticheat", holds_cpu_lock: false, skips_on_oom: true, fetches_bundle: false },
    EvalStage { title: "Score", phase: "score", holds_cpu_lock: false, skips_on_oom: true, fetches_bundle: false },
    EvalStage { title: "Report", phase: "report", holds_cpu_lock: false, skips_on_oom: true, fetches_bundle: false },
    EvalStage { title: "Archive", phase: "archive", holds_cpu_lock: false, skips_on_oom: true, fetches_bundle: false },
];

/// The Tasks stage's first step on a shard: the dispatcher's harness bundle,
/// copied only when this worker's harness store does not hold it. The store
/// check recomputes the tree's digest, so a damaged tree is removed here and
/// copied again rather than used. Runs after Environment extracted the pipeline.
const FETCH_BUNDLE_GROOVY: &str = r#"          script {
            sh 'rm -rf "${WORKSPACE}/harness-bundle"'
            if (params.HARNESS_BUNDLE_FROM?.trim()) {
              String want = (params.HARNESS_BUNDLE_SHA256 ?: '').trim().toLowerCase()
              if (!(want ==~ /[0-9a-f]{64}/)) {
                error "HARNESS_BUNDLE_FROM needs HARNESS_BUNDLE_SHA256, a sha256 (got '${want}')"
              }
              int stored = sh(returnStatus: true, script: '''#!/bin/bash
export PATH="${HOME}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH}"
python3 "${WORKSPACE}/mac-k3d-pipeline/pipeline/lib/harness_store.py" get \
  --harness "$HARNESS" --sha "$HARNESS_BUNDLE_SHA256" >/dev/null
''')
              if (stored == 0) {
                echo "harness store: bundle ${want.take(16)} is already on ${env.NODE_NAME}; not copying it"
              } else {
                def from = params.HARNESS_BUNDLE_FROM.split('#', 2)
                if (from.size() != 2 || !from[1].trim()) {
                  error "HARNESS_BUNDLE_FROM must be job#build (got ${params.HARNESS_BUNDLE_FROM})"
                }
                copyArtifacts projectName: from[0],
                  selector: specific(from[1].trim()),
                  filter: 'eval-runs/harness/bundle.tar.gz',
                  target: 'harness-bundle',
                  flatten: true
                echo "harness store: copied bundle ${want.take(16)} from ${params.HARNESS_BUNDLE_FROM}"
              }
            }
          }
"#;

fn eval_jenkinsfile(job_benchmark: &str, shape: JobShape, opts: &JobOpts) -> String {
    if shape.is_dispatcher() {
        return dispatcher_jenkinsfile(job_benchmark, shape, opts);
    }
    let params = groovy_params(&eval_params(job_benchmark, shape, opts), opts.ui_profile);
    let readers = groovy_escape(&shard_readers(job_benchmark).join(","));
    let bootstrap = eval_bootstrap_sh(job_benchmark, opts, 12);
    let stages = EVAL_STAGES
        .iter()
        .enumerate()
        .map(|(i, stage)| eval_stage(stage, i == 0, &bootstrap, opts))
        .collect::<Vec<_>>()
        .join("\n\n");
    format!(
        r#"pipeline {{
  agent {{ label "${{params.AGENT_LABEL}} && fits-${{params.BENCHMARK}}" }}

  options {{
    timeout(time: 96, unit: 'HOURS')
    copyArtifactPermission('{readers}')
  }}

  parameters {{
{params}
  }}

  stages {{
{stages}
  }}

  post {{
    always {{
      script {{
        if (fileExists('eval-runs/last_output.txt')) {{
          def pattern = readFile('eval-runs/last_output.txt').trim()
          if (pattern) {{
            archiveArtifacts artifacts: pattern, allowEmptyArchive: true
          }}
        }}
        // The run's .tar.gz, downloadable from the build page. A shard skips it:
        // its dispatcher archives one combined .tar.gz instead.
        if (!params.RUN_GROUP?.trim() && fileExists('eval-runs/last_backup.txt')) {{
          def backup = readFile('eval-runs/last_backup.txt').trim()
          if (backup) {{
            archiveArtifacts artifacts: backup, allowEmptyArchive: true
          }}
        }}
        // A shard archives this build's trials and verdicts so the dispatcher
        // can merge them. The workspace keeps every earlier build's trials too.
        if (params.RUN_GROUP?.trim()) {{
          archiveArtifacts artifacts: "eval-runs/harness/harbor_runs/jenkins-${{env.BUILD_NUMBER}}/**, eval-runs/harness/anticheat/**", allowEmptyArchive: true
          // suite_tasks.txt is the whole suite. A shard given ids already names
          // its questions in selected_tasks.txt. An offset shard (no TASK and no
          // TASKS) archives the list so the merge can name a slice that ran nothing.
          def inputs = 'eval-runs/selected_tasks.txt, eval-runs/skipped_tasks.txt, eval-runs/eval_protocol_inputs.json, eval-runs/eval_resources.json, eval-runs/harness/container_mem.jsonl'
          if (!params.TASK?.trim() && !params.TASKS?.trim()) {{
            inputs = 'eval-runs/suite_tasks.txt, ' + inputs
          }}
          archiveArtifacts artifacts: inputs, allowEmptyArchive: true
        }}
        // report/render counts the rollouts that have no score and prints their
        // causes. Each stays in the report as that rollout's own failure; the
        // build result does not change.
        if (fileExists('eval-runs/unscored_rollouts.txt')) {{
          String lost = readFile('eval-runs/unscored_rollouts.txt').trim()
          if (lost ==~ /\d+/ && (lost as Integer) > 0) {{
            echo "WARNING: ${{lost}} rollouts have no score (causes above; see Unscored in summary.md)"
          }}
        }}
        // A killed shell never runs its EXIT trap. Remove this build's trial
        // containers here, including an abort or a timeout. The copied harness
        // bundle is in the store by now, or of no use to a later build.
        sh '''#!/bin/bash
set -euo pipefail
rm -rf "${{WORKSPACE}}/harness-bundle"
export PATH="${{HOME}}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${{PATH}}"
export MAC_K3D_ROOT="${{WORKSPACE}}/mac-k3d-pipeline"
if [ ! -f "$MAC_K3D_ROOT/pipeline/lib/trial_network.py" ]; then
  command -v mac-k3d >/dev/null || exit 0
  mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean || exit 0
fi
for dir in \
  "${{WORKSPACE}}/eval-runs/harness/harbor_runs/jenkins-${{BUILD_NUMBER}}" \
  "${{WORKSPACE}}/eval-runs/canary/jenkins-${{BUILD_NUMBER}}"
do
  [ -d "$dir" ] || continue
  python3 "$MAC_K3D_ROOT/pipeline/lib/trial_network.py" teardown --jobs-dir "$dir" || true
done
'''
        // A direct build has no dispatcher to put it back on the queue.
        if (env.TRIAL_OOM == '1' && !params.RUN_GROUP?.trim()) {{
          int attempt = (params.ATTEMPT ?: '1') as Integer
          int cap = (params.OOM_RETRIES ?: '2') as Integer
          if (attempt <= cap) {{
            echo "OOM on attempt ${{attempt}}; queueing attempt ${{attempt + 1}}"
            List again = []
            params.each {{ key, value ->
              String name = key.toString()
              if (name in ['ATTEMPT', 'ICODE_RELEASE_FILE', 'JIUWENSWARM_RELEASE_FILE']) {{ return }}
              again << string(name: name, value: value == null ? '' : value.toString())
            }}
            again << string(name: 'ATTEMPT', value: "${{attempt + 1}}")
            build job: env.JOB_NAME, wait: false, propagate: false, quietPeriod: 0, parameters: again
          }} else {{
            echo "OOM on attempt ${{attempt}}; not queueing again (OOM_RETRIES=${{cap}})"
          }}
        }}
        // The dispatcher reads this through run.buildVariables: a shard that
        // failed before its Harbor job dir existed spent no model tokens.
        if (fileExists("eval-runs/harness/harbor_runs/jenkins-${{env.BUILD_NUMBER}}")) {{
          env.HARBOR_STARTED = '1'
        }}
      }}
    }}
  }}
}}
"#
    )
}

/// One `stage('<title>')`. The first stage names the build, unstashes the
/// iCode upload and clears the previous build's pipeline extract.
fn eval_stage(stage: &EvalStage, first: bool, bootstrap: &str, opts: &JobOpts) -> String {
    let (cred_open, cred_close) = with_credentials_block(&opts.credential_ids);
    let title = stage.title;
    let run = format!(
        r#"MAC_K3D_PHASE={} bash "$MAC_K3D_ROOT/pipeline/stages/run_all.sh""#,
        stage.phase
    );
    if stage.holds_cpu_lock {
        return format!(
            r#"    stage('{title}') {{
      steps {{
{cred_open}          sh '''#!/bin/bash
set -euo pipefail
export PATH="${{HOME}}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${{PATH}}"
export MAC_K3D_ROOT="${{WORKSPACE}}/mac-k3d-pipeline"
if [ -f "$MAC_K3D_ROOT/pipeline/lib/trial_network.py" ]; then
  roots=()
  base="$(dirname "$WORKSPACE")"
  for runs in "$base"/*/eval-runs; do
    [ -d "$runs/harness/harbor_runs" ] && roots+=(--root "$runs/harness/harbor_runs")
    [ -d "$runs/canary" ] && roots+=(--root "$runs/canary")
  done
  if [ "${{#roots[@]}}" -gt 0 ]; then
    python3 "$MAC_K3D_ROOT/pipeline/lib/trial_network.py" sweep "${{roots[@]}}" || true
  fi
fi
'''
          script {{
            // Lock the CPUs this one task declares, not every core. The tasks
            // phase wrote the count. The resources are named <node>-core-N and
            // carry the node name as a label, so two workers never share a pool.
            int cpusEach = 1
            if (fileExists('eval-runs/cpus_each.txt')) {{
              String raw = readFile('eval-runs/cpus_each.txt').trim()
              if (raw ==~ /\d+/ && (raw as Integer) >= 1) {{ cpusEach = raw as Integer }}
            }}
            lock(label: env.NODE_NAME, quantity: cpusEach, variable: 'HELD_CORES') {{
              String heldCores = env.HELD_CORES ?: ''
              int held = heldCores.trim() ? heldCores.split(',').size() : 0
              if (held < cpusEach) {{
                error "Needed ${{cpusEach}} ${{env.NODE_NAME}}-core-N resources and locked ${{held}}. Run mac-k3d config on this worker."
              }}
              env.CPU_LOCK_QTY = "${{held}}"
              sh '''#!/bin/bash
set -euo pipefail
{bootstrap}
                {run}
              '''
              if (fileExists('eval-runs/harness/oom_trials.txt')) {{
                env.TRIAL_OOM = '1'
                currentBuild.result = 'UNSTABLE'
                currentBuild.description = "OOM, attempt ${{params.ATTEMPT ?: '1'}}"
              }}
            }}
          }}
{cred_close}      }}
    }}"#
        );
    }
    let (setup, clear) = if first {
        let requester = requester_groovy("            ");
        (
            format!(
                r#"          script {{
            {DISPLAY_NAME_GROOVY}
            if (params.RUN_GROUP?.trim()) {{
              currentBuild.description = "shard ${{params.SHARD}}"
            }}
            {requester}
            // A shard gets its dispatcher's harness bundle in Tasks; only a
            // JiuwenSwarm release is handed on, for a shard that builds its own.
            String upload = params.HARNESS == 'jiuwenswarm' ? 'JIUWENSWARM_RELEASE_FILE' : 'ICODE_RELEASE_FILE'
            if (params.JIUWENSWARM_RELEASE_FROM?.trim()) {{
              def from = params.JIUWENSWARM_RELEASE_FROM.split('#', 2)
              if (from.size() != 2 || !from[1].trim()) {{
                error "JIUWENSWARM_RELEASE_FROM must be job#build (got ${{params.JIUWENSWARM_RELEASE_FROM}})"
              }}
              copyArtifacts projectName: from[0],
                selector: specific(from[1].trim()),
                filter: 'jiuwenswarm-release/JIUWENSWARM_RELEASE_FILE',
                target: '.',
                flatten: true
            }} else if (!params.HARNESS_BUNDLE_FROM?.trim()) {{
              try {{
                unstash upload
              }} catch (Throwable t) {{
                echo "No ${{upload}} stash (${{t}})"
              }}
            }}
          }}
"#
            ),
            "            rm -rf \"${WORKSPACE}/mac-k3d-pipeline/pipeline\"\n",
        )
    } else {
        (String::new(), "")
    };
    let announce = if first {
        "\n            echo \"MAC_K3D_ROOT=$MAC_K3D_ROOT BENCHMARK=$BENCHMARK HARNESS=$HARNESS ICODE_MODE=$ICODE_MODE${JIUWENSWARM_MODE:+ JIUWENSWARM_MODE=$JIUWENSWARM_MODE}\""
    } else {
        ""
    };
    let when = if stage.skips_on_oom {
        "      when { expression { env.TRIAL_OOM != '1' } }\n"
    } else {
        ""
    };
    let fetch = if stage.fetches_bundle { FETCH_BUNDLE_GROOVY } else { "" };
    format!(
        r#"    stage('{title}') {{
{when}      steps {{
{cred_open}{setup}{fetch}          sh '''#!/bin/bash
set -euo pipefail
{clear}{bootstrap}{announce}
            {run}
          '''
{cred_close}      }}
    }}"#,
        when = when,
    )
}

/// some_task / full_suite_task. Splits the questions into small shards, queues
/// every shard as a `<suite>_one_task` build, then merges what came back. It
/// runs on `agent none` until the merge, so it never holds a worker executor
/// while its own shards wait for one.
fn dispatcher_jenkinsfile(job_benchmark: &str, shape: JobShape, opts: &JobOpts) -> String {
    let params = groovy_params(&eval_params(job_benchmark, shape, opts), opts.ui_profile);
    let shard_job = groovy_escape(&eval_job_name(job_benchmark, JobShape::One));
    let ids_from_tasks = if matches!(shape, JobShape::Some_ | JobShape::Headless) {
        "          if (params.TASKS?.trim()) {\n            ids = params.TASKS.split(',').collect { it.trim() }.findAll { it }\n          }\n"
    } else {
        ""
    };
    // Only what both this job and its shard job have.
    let own: Vec<&str> = eval_params(job_benchmark, shape, opts).iter().map(|p| p.name).collect();
    let shard: Vec<&str> = eval_params(job_benchmark, JobShape::One, opts).iter().map(|p| p.name).collect();
    let forwarded = FORWARDED_PARAMS
        .iter()
        .filter(|n| own.contains(n) && shard.contains(n))
        .map(|n| format!("                  string(name: '{n}', value: params.{n}),"))
        .collect::<Vec<_>>()
        .join("\n");
    // The identity Prepare harness pinned, for each shard to find in its bundle.
    let pins = SHARD_PINS
        .iter()
        .filter(|n| shard.contains(n))
        .map(|n| format!("                  string(name: '{n}', value: env.{n} ?: ''),"))
        .collect::<Vec<_>>()
        .join("\n");
    // Preparing clones and builds; it never calls an LLM, so it gets the git PATs only.
    let git_pats: Vec<String> = opts
        .credential_ids
        .iter()
        .filter(|id| GIT_PAT_CREDENTIALS.contains(&id.as_str()))
        .cloned()
        .collect();
    let (prep_cred_open, prep_cred_close) = with_credentials_block(&git_pats);
    // One build of the harness for every shard: prepared on a worker (or found
    // in its harness store), archived, and named by sha256. A shard uses that
    // bundle and no other; one of another task platform builds its own from the
    // same pinned commit or release.
    let prepare_stage = r#"    stage('Prepare harness') {
      steps {
        script {
          // Jenkins places this job on the same worker every time, so a worker
          // that cannot build the harness (its Docker store cannot run the build
          // image) is left out and another AGENT_LABEL worker builds it, once.
          Map at = [node: '']
          def prepareOn = { String where ->
            node(where) {
              at.node = env.NODE_NAME
              // #region agent log
              echo "[debug 8643d4] H1 prepare attempt node=${env.NODE_NAME} label=${where}"
              // #endregion
              deleteDir()
              String mode = (params.HARNESS == 'jiuwenswarm' ? params.JIUWENSWARM_MODE : params.ICODE_MODE) ?: 'git'
              String upload = params.HARNESS == 'jiuwenswarm' ? 'JIUWENSWARM_RELEASE_FILE' : 'ICODE_RELEASE_FILE'
              if (mode == 'release') {
                try {
                  unstash upload
                } catch (Throwable t) {
                  error "release mode needs an uploaded ${upload} (${t})"
                }
              }
__CRED_OPEN__              sh '''#!/bin/bash
set -euo pipefail
export PATH="${HOME}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${PATH}"
command -v mac-k3d >/dev/null || { echo "mac-k3d is not on PATH on ${NODE_NAME:-this worker}. Run scripts/redeploy.sh." >&2; exit 1; }
export MAC_K3D_ROOT="${WORKSPACE}/mac-k3d-pipeline"
rm -rf "$MAC_K3D_ROOT"
mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean
export MAC_K3D_EVAL_WORKDIR="${WORKSPACE}/eval-runs"
mkdir -p "$MAC_K3D_EVAL_WORKDIR"
export GIT_TERMINAL_PROMPT=0
for upload in ICODE_RELEASE JIUWENSWARM_RELEASE; do
  if [ -s "${WORKSPACE}/${upload}_FILE" ]; then
    export "${upload}=${WORKSPACE}/${upload}_FILE" "${upload}_UPLOADED=1"
  fi
done
bash "$MAC_K3D_ROOT/pipeline/tools/prepare_harness.sh"
'''
__CRED_CLOSE__              String sha = readFile('eval-runs/harness/bundle.sha256').trim()
              if (!(sha ==~ /[0-9a-f]{64}/)) {
                error "Prepare harness left no bundle sha256 (got '${sha}')"
              }
              List<String> pin = readFile('eval-runs/harness/pin.txt').trim().split(' ') as List<String>
              if (pin.size() != 2 || !(pin[0] in ['git', 'release'])) {
                error "Prepare harness left no identity pin (got '${pin.join(' ')}')"
              }
              env.HARNESS_BUNDLE_SHA256 = sha
              if (params.HARNESS == 'jiuwenswarm') {
                if (pin[0] == 'git') { env.JIUWENSWARM_GIT_SHA = pin[1] } else { env.JIUWENSWARM_RELEASE_SHA256 = pin[1] }
              } else {
                if (pin[0] == 'git') { env.ICODE_GIT_SHA = pin[1] } else { env.ICODE_RELEASE_SHA256 = pin[1] }
              }
              // A JiuwenSwarm wheel or wheelhouse goes to the shards too, for one whose
              // task platform needs its own build. An uploaded bundle is the bundle itself.
              if (params.HARNESS == 'jiuwenswarm' && pin[0] == 'release' && pin[1] != sha) {
                sh 'mkdir -p jiuwenswarm-release && cp JIUWENSWARM_RELEASE_FILE jiuwenswarm-release/JIUWENSWARM_RELEASE_FILE'
                env.JIUWENSWARM_RELEASE_FROM = "${env.JOB_NAME}#${env.BUILD_NUMBER}"
              }
              echo "Every shard runs harness bundle ${sha} (${pin[0]} ${pin[1]})"
              archiveArtifacts artifacts: 'eval-runs/harness/bundle.tar.gz, eval-runs/harness/bundle.json'
              if (env.JIUWENSWARM_RELEASE_FROM) {
                archiveArtifacts artifacts: 'jiuwenswarm-release/JIUWENSWARM_RELEASE_FILE'
              }
            }
          }
          try {
            prepareOn(params.AGENT_LABEL)
          } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
            throw e
          } catch (Throwable t) {
            String elsewhere = "(${params.AGENT_LABEL}) && !\"${at.node}\""
            List<String> others = []
            if (at.node) {
              try {
                others = nodesByLabel(label: elsewhere, offline: false)
              } catch (Throwable ignored) {
              }
            }
            // #region agent log
            echo "[debug 8643d4] H1 prepare failed node=${at.node} others=${others}"
            // #endregion
            if (!others) {
              throw t
            }
            echo "Prepare harness failed on ${at.node} (${t}); building it once more on another ${params.AGENT_LABEL} worker: ${others.join(', ')}"
            prepareOn(elsewhere)
          }
        }
      }
    }

"#
    .replace("__CRED_OPEN__", &prep_cred_open)
    .replace("__CRED_CLOSE__", &prep_cred_close);
    format!(
        r#"pipeline {{
  agent none

  options {{
    timeout(time: 96, unit: 'HOURS')
    copyArtifactPermission('{shard_job}')
  }}

  parameters {{
{params}
  }}

  stages {{
{prepare_stage}    stage('Shards') {{
      steps {{
        script {{
          {display_name}
          {requester}
          int rollouts = (params.N_ROLLOUTS as Integer)
          if (rollouts < 1) {{
            error 'N_ROLLOUTS must be an integer >= 1'
          }}
          // Explicit ids win over N_TASKS, exactly as in a one_task build.
          List<String> ids = []
{ids_from_tasks}          int total = ids ? ids.size() : (params.N_TASKS as Integer)
          if (total < 1) {{
            error 'nothing to run: set N_TASKS >= 1 or a non-empty TASKS list'
          }}
          // A worker too small for this suite does not carry fits-<benchmark>,
          // so a build is never placed where it cannot lock the cores.
          String fits = "${{params.AGENT_LABEL}} && fits-${{params.BENCHMARK}}"
          List<String> workerNames = null
          try {{
            workerNames = nodesByLabel(label: fits, offline: false)
          }} catch (Throwable t) {{
            echo "WARNING: could not count workers for label ${{fits}} (${{t}})"
          }}
          if (workerNames != null && workerNames.isEmpty()) {{
            List<String> online = []
            try {{
              online = nodesByLabel(label: '{default_label}', offline: false)
            }} catch (Throwable t) {{
            }}
            error "No online worker has label ${{fits}}. Online {default_label} workers: ${{online ? online.join(', ') : 'none'}}."
          }}
          // nodesByLabel returns names, not executor counts. Each worker's
          // registration adds executors-<n>, so the sum is the online capacity.
          int totalExec = 0
          boolean countedOk = true
          List<String> execNotes = []
          try {{
            for (int n = 1; n <= 64; n++) {{
              def nodes = nodesByLabel(label: "${{fits}} && executors-${{n}}", offline: false)
              if (nodes) {{
                totalExec += n * nodes.size()
                for (String node : nodes) {{ execNotes << "${{node}}:${{n}}" }}
              }}
            }}
          }} catch (Throwable t) {{
            countedOk = false
            echo "WARNING: could not count executors (${{t}}); MAX_IN_FLIGHT falls back to 20"
          }}
          int cap = (params.MAX_IN_FLIGHT ?: '0') as Integer
          String mode
          if (!countedOk || totalExec < 1) {{
            if (countedOk) {{
              echo "WARNING: no executors-<n> label on online workers; MAX_IN_FLIGHT falls back to 20. Re-run mac-k3d config on each worker."
            }}
            cap = 20
            mode = 'fallback'
          }} else if (cap > 0) {{
            mode = 'fixed'
            if (cap < totalExec) {{
              echo "WARNING: MAX_IN_FLIGHT=${{cap}} is below ${{totalExec}} online executors; some executors will sit idle"
            }}
          }} else {{
            cap = totalExec * 2
            mode = 'auto'
          }}
          echo "executors=${{totalExec}} [${{execNotes.join(', ')}}] max_in_flight=${{cap}} (${{mode}})"
          env.RUN_GROUP = "${{env.JOB_NAME}}-${{env.BUILD_NUMBER}}".replaceAll('[^A-Za-z0-9_.-]', '_')
          echo "RUN_GROUP=${{env.RUN_GROUP}} questions=${{total}} rollouts=${{rollouts}} units=${{total * rollouts}}"

          // One unit is one question and one rollout. Lanes pop the head; an
          // OOM rollout is appended, so it runs after everything already waiting.
          List<Map> units = []
          for (int i = 0; i < total; i++) {{
            for (int r = 1; r <= rollouts; r++) {{
              units << [index: i, offset: i, tasks: ids ? ids[i] : '', rollout: r, attempt: 1, preHarbor: false]
            }}
          }}
          int inflight = 0
          int oomRetries = (params.OOM_RETRIES ?: '2') as Integer
          String queueLock = "mack3d-${{env.RUN_GROUP}}"
          Map<String, Integer> shardBuilds = [:]
          Map<String, String> shardPlan = [:]
          List<String> notOk = []
          List<String> retried = []
          int lanes = units.size() < cap ? units.size() : cap
          Map<String, Closure> branches = [:]
          for (int lane = 0; lane < lanes; lane++) {{
            String name = "lane-${{lane + 1}}"
            branches[name] = {{
              while (true) {{
                def unit = null
                String action = 'stop'
                lock(resource: queueLock) {{
                  if (units) {{
                    unit = units.remove(0)
                    inflight++
                    action = 'run'
                  }} else if (inflight > 0) {{
                    action = 'wait'
                  }}
                }}
                if (action == 'stop') {{ break }}
                if (action == 'wait') {{
                  // A build still running may OOM and put its unit back.
                  sleep 5
                  continue
                }}
                String note = "rollout ${{unit.rollout}}/${{rollouts}} question ${{unit.index + 1}}/${{total}} attempt ${{unit.attempt}} of ${{env.JOB_NAME}} #${{env.BUILD_NUMBER}}"
                List shardParams = [
{forwarded}
                  string(name: 'TASK', value: ''),
                  string(name: 'TASKS', value: unit.tasks),
                  string(name: 'N_TASKS', value: '1'),
                  string(name: 'TASK_OFFSET', value: ids ? '0' : "${{unit.offset}}"),
                  string(name: 'N_ROLLOUTS', value: '1'),
                  string(name: 'ROLLOUT', value: "${{unit.rollout}}"),
                  string(name: 'ATTEMPT', value: "${{unit.attempt}}"),
                  string(name: 'RUN_GROUP', value: env.RUN_GROUP),
                  string(name: 'SHARD', value: note),
                  string(name: 'REQUESTED_BY', value: env.BUILD_USER ?: ''),
                  string(name: 'HARNESS_BUNDLE_FROM', value: "${{env.JOB_NAME}}#${{env.BUILD_NUMBER}}"),
                  string(name: 'HARNESS_BUNDLE_SHA256', value: env.HARNESS_BUNDLE_SHA256),
{pins}
                ]
                def run = build job: '{shard_job}', wait: true, propagate: false, quietPeriod: 0, parameters: shardParams
                String started = ''
                String oom = ''
                try {{
                  started = run.buildVariables?.HARBOR_STARTED ?: ''
                  oom = run.buildVariables?.TRIAL_OOM ?: ''
                }} catch (Throwable t) {{
                  started = 'unknown'
                  echo "WARNING: cannot read #${{run.number}}'s variables (${{t}}); not retrying it"
                }}
                boolean requeue = false
                if (oom == '1' && (unit.attempt as Integer) <= oomRetries) {{
                  echo "OOM on #${{run.number}} attempt ${{unit.attempt}}; queueing it at the end"
                  unit.attempt = (unit.attempt as Integer) + 1
                  requeue = true
                  retried << "build ${{run.number}} oom"
                }} else if (run.result == 'FAILURE' && !started && !unit.preHarbor) {{
                  // Failed before Harbor started: no model tokens spent. Once only.
                  echo "Build ${{run.number}} failed before Harbor started; queueing it at the end"
                  unit.preHarbor = true
                  requeue = true
                  retried << "build ${{run.number}} before-harbor"
                }}
                String key = "${{unit.index}}-${{unit.rollout}}"
                lock(resource: queueLock) {{
                  inflight--
                  if (requeue) {{
                    units << unit
                  }} else {{
                    shardBuilds[key] = run.number
                    shardPlan[key] = "${{unit.index + 1}}|${{run.number}}|${{run.result}}|${{unit.offset}}|1|${{unit.tasks}}|${{unit.rollout}}|${{unit.attempt}}"
                    if (['FAILURE', 'ABORTED', 'NOT_BUILT'].contains(run.result)) {{
                      notOk << "question ${{unit.index + 1}} rollout ${{unit.rollout}} #${{run.number}} ${{run.result}}"
                    }}
                  }}
                }}
              }}
            }}
          }}
          parallel branches
          env.SHARD_BUILDS = shardBuilds.values().join(',')
          List<String> planLines = []
          for (int i = 0; i < total; i++) {{
            for (int r = 1; r <= rollouts; r++) {{
              String line = shardPlan["${{i}}-${{r}}"]
              if (line != null) {{ planLines << line }}
            }}
          }}
          env.SHARD_PLAN = planLines.join(';')
          if (retried) {{
            echo "Retried once after failing before Harbor started: ${{retried.join(', ')}}"
          }}
          if (notOk) {{
            unstable "Shards that did not succeed: ${{notOk.join(', ')}}. The report merges the rest."
          }}
        }}
      }}
    }}

    stage('Aggregate') {{
      agent {{ label "${{params.AGGREGATE_LABEL?.trim() ?: params.AGENT_LABEL}}" }}
      steps {{
        script {{
          deleteDir()
          if (!env.SHARD_BUILDS?.trim()) {{
            error 'No shard build started, so there is nothing to merge.'
          }}
          // The iCode transcripts are most of a trial's size and no metric reads
          // them; they stay in each shard build's artifacts.
          for (String n : env.SHARD_BUILDS.split(',')) {{
            copyArtifacts projectName: '{shard_job}',
              selector: specific(n),
              filter: 'eval-runs/**',
              excludes: 'eval-runs/harness/harbor_runs/**/agent/icode-project/**',
              target: "shards/${{n}}",
              flatten: false,
              optional: true
          }}
          // A shard that stopped early archives nothing; the plan still names it.
          writeFile file: 'shards/plan.txt', text: (env.SHARD_PLAN ?: '').replace(';', '\n') + '\n'
        }}
        sh '''#!/bin/bash
set -euo pipefail
          export PATH="${{HOME}}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${{PATH}}"
          MAC_K3D_ROOT="${{WORKSPACE}}/mac-k3d-pipeline"
          mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean
          python3 "$MAC_K3D_ROOT/pipeline/lib/aggregate_runs.py" \
            --shards "${{WORKSPACE}}/shards" \
            --benchmark "$BENCHMARK" \
            --run-group "$RUN_GROUP" \
            --n-rollouts "${{N_ROLLOUTS:-4}}" \
            --plan "${{WORKSPACE}}/shards/plan.txt" \
            --build-url "${{BUILD_URL:-}}" \
            --requester-user "${{BUILD_USER:-}}" \
            --shard-job '{shard_job}' \
            --out "${{WORKSPACE}}/aggregate"
          python3 "$MAC_K3D_ROOT/pipeline/lib/cost_token_report.py" \
            --run-dir "${{WORKSPACE}}/aggregate" \
            --out "${{WORKSPACE}}/aggregate/cost-token-report.md" \
            || echo "WARNING: cost/token analysis failed; the combined .tar.gz goes ahead without cost-token-report.md"
          python3 "$MAC_K3D_ROOT/pipeline/lib/archive_run.py" \
            --report-dir "${{WORKSPACE}}/aggregate" \
            --harness-dir "${{WORKSPACE}}/aggregate/harness" \
            --task-file "${{WORKSPACE}}/aggregate/selected_tasks.txt" \
            --suite "$BENCHMARK" \
            --backup-root "${{WORKSPACE}}/backup" \
            --run-folder "$RUN_GROUP"
        '''
      }}
      post {{
        always {{
          // aggregate/harness is a second copy of the shards' trials.
          archiveArtifacts artifacts: 'aggregate/**', excludes: 'aggregate/harness/**', allowEmptyArchive: true
          archiveArtifacts artifacts: 'backup/**', allowEmptyArchive: true
        }}
      }}
    }}
  }}

  post {{
    always {{
      script {{
        // No shard reads the bundle any more, even after an abort. Its sha256
        // in summary.md and report.html is the record, so the build page keeps
        // bundle.json but not the bundle or a JiuwenSwarm release.
        // A sandbox refusal only warns; the eval result does not change.
        try {{
          File root = currentBuild.rawBuild.getRootDir()
          File bundle = new File(root, "archive/eval-runs/harness/bundle.tar.gz")
          File release = new File(root, "archive/jiuwenswarm-release")
          if ((bundle.exists() && !bundle.delete()) || (release.isDirectory() && !release.deleteDir())) {{
            echo "WARNING: could not remove the harness bundle from this build's artifacts. Its sha256 is in summary.md and report.html."
          }}
        }} catch (Throwable t) {{
          echo "WARNING: could not remove the harness bundle from this build's artifacts (${{t}}). Its sha256 is in summary.md and report.html."
        }}
      }}
    }}
  }}
}}
"#,
        display_name = DISPLAY_NAME_GROOVY,
        requester = requester_groovy("          "),
        default_label = DEFAULT_AGENT_LABEL,
    )
}

/// The shell prelude every phase runs: extract this worker's pipeline once per
/// build, export the parameters, validate them. Idempotent, so each phase
/// re-derives the same env without the phases having to share a shell.
fn eval_bootstrap_sh(job_benchmark: &str, opts: &JobOpts, indent: usize) -> String {
    let pad = " ".repeat(indent);
    let body = format!(
        r#"export PATH="${{HOME}}/.local/bin:/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:${{PATH}}"
command -v docker >/dev/null
docker info >/dev/null
export MAC_K3D_EVAL_WORKDIR="${{WORKSPACE}}/eval-runs"

# The pipeline is the one embedded in this worker's mac-k3d binary, extracted
# once per build (the first stage clears it), so a redeploy mid-build cannot change the
# scripts under a running build. BUILD.json names the commit for the artifact.
# Every build refuses a binary built from uncommitted src/ or pipeline/.
export MAC_K3D_ROOT="${{WORKSPACE}}/mac-k3d-pipeline"
if [ ! -f "$MAC_K3D_ROOT/pipeline/BUILD.json" ]; then
  command -v mac-k3d >/dev/null || {{ echo "mac-k3d is not on PATH on ${{NODE_NAME:-this worker}}. Run scripts/redeploy.sh." >&2; exit 1; }}
  mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean
fi

export N_TASKS="${{N_TASKS:-1}}"
export N_ROLLOUTS="${{N_ROLLOUTS:-4}}"
export RUN_GROUP="${{RUN_GROUP:-}}"
export TASK_OFFSET="${{TASK_OFFSET:-0}}"
case "$TASK_OFFSET" in
  "" | *[!0-9]*) echo "TASK_OFFSET must be an integer >= 0 (got '$TASK_OFFSET')" >&2; exit 1 ;;
esac
for pair in "N_ROLLOUTS=$N_ROLLOUTS" "N_TASKS=$N_TASKS"; do
  name="${{pair%%=*}}"
  value="${{pair#*=}}"
  case "$value" in
    "" | *[!0-9]*) echo "$name must be an integer >= 1 (got '$value')" >&2; exit 1 ;;
  esac
  [ "$value" -ge 1 ] || {{ echo "$name must be an integer >= 1 (got '$value')" >&2; exit 1; }}
done
export TASK="${{TASK:-}}"
export TASKS="${{TASKS:-}}"
export ICODE_MODE="${{ICODE_MODE:-{icode_mode_fb}}}"
export ICODE_RELEASE=""
export ICODE_RELEASE_UPLOADED=""
if [ "${{ICODE_MODE}}" = "git" ]; then
  bash "$MAC_K3D_ROOT/pipeline/lib/icode_input.sh" --force-rm "${{WORKSPACE}}/ICODE_RELEASE_FILE"
elif [ -s "${{WORKSPACE}}/ICODE_RELEASE_FILE" ]; then
  export ICODE_RELEASE="${{WORKSPACE}}/ICODE_RELEASE_FILE"
  export ICODE_RELEASE_UPLOADED=1
fi
export ICODE_GIT_URL="${{ICODE_GIT_URL:-}}"
export ICODE_GIT_REF="${{ICODE_GIT_REF:-main}}"
export ICODE_GIT_REF_KIND="${{ICODE_GIT_REF_KIND:-branch}}"
export ICODE_GIT_SHA="${{ICODE_GIT_SHA:-}}"
export HARBOR_VERSION="${{HARBOR_VERSION:-{harbor_fb}}}"
export DEEPSWE_REF="${{DEEPSWE_REF:-0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea}}"
export LOLBENCH_REF="${{LOLBENCH_REF:-1b10d10bb4a10cea54374ac34b8f76b69dc8ce75}}"
export ICODE_RELEASE_SHA256="${{ICODE_RELEASE_SHA256:-}}"
export HARNESS="${{HARNESS:-{harness_fb}}}"
export LLM="${{LLM:-{llm_fb}}}"
export BENCHMARK="${{BENCHMARK:-{bench}}}"
export DEEPSEEK_MODEL="${{DEEPSEEK_MODEL:-{model_fb}}}"
export JIUWENSWARM_RELEASE=""
export JIUWENSWARM_RELEASE_UPLOADED=""
if [ -s "${{WORKSPACE}}/JIUWENSWARM_RELEASE_FILE" ]; then
  export JIUWENSWARM_RELEASE="${{WORKSPACE}}/JIUWENSWARM_RELEASE_FILE"
  export JIUWENSWARM_RELEASE_UPLOADED=1
fi
# The dispatcher's harness bundle: only that sha256 is used, from this worker's
# harness store or from the copy the Tasks stage made.
export HARNESS_BUNDLE_SHA256="${{HARNESS_BUNDLE_SHA256:-}}"
unset HARNESS_BUNDLE_FILE
if [ -n "$HARNESS_BUNDLE_SHA256" ] && [ -s "${{WORKSPACE}}/harness-bundle/bundle.tar.gz" ]; then
  export HARNESS_BUNDLE_FILE="${{WORKSPACE}}/harness-bundle/bundle.tar.gz"
fi
if [ -z "${{DEEPSEEK_API_KEY:-}}" ]; then
  echo "DEEPSEEK_API_KEY missing. Store credential deepseek-api-key on the Jenkins controller." >&2
  exit 1
fi
if [ "$HARNESS" = icode ] && [ -z "$HARNESS_BUNDLE_SHA256" ] && {{ [ "${{ICODE_MODE}}" = "release" ] || [ "${{ICODE_MODE}}" = "binary" ]; }}; then
  if [ -z "${{ICODE_RELEASE}}" ]; then
    echo "ICODE_MODE=release: upload ICODE_RELEASE_FILE on Jenkins Build with Parameters (not a worker path)." >&2
    exit 1
  fi
fi"#,
        icode_mode_fb = jenkins_icode_mode(opts),
        harbor_fb = crate::prepare::toolchain::harbor_version(),
        harness_fb = eval_catalog::HARNESSES[0],
        llm_fb = eval_catalog::LLMS[0],
        bench = groovy_escape(job_benchmark),
        model_fb = eval_catalog::default_model(),
    );
    body
        .lines()
        .map(|l| {
            if l.is_empty() {
                String::new()
            } else {
                format!("{pad}{l}")
            }
        })
        .collect::<Vec<_>>()
        .join("\n")
}

fn one_task_job_xml(job_benchmark: &str, description: &str, opts: &JobOpts) -> String {
    eval_job_xml(job_benchmark, JobShape::One, description, opts)
}

fn eval_job_xml(
    job_benchmark: &str,
    shape: JobShape,
    description: &str,
    opts: &JobOpts,
) -> String {
    let script = eval_jenkinsfile(job_benchmark, shape, opts);
    let params = xml_params(&eval_params(job_benchmark, shape, opts), opts.ui_profile);
    let desc_xml = xml_escape(description);
    let readers: Vec<String> = if shape == JobShape::One {
        shard_readers(job_benchmark)
    } else {
        vec![eval_job_name(job_benchmark, JobShape::One)]
    };
    let reader_xml = readers
        .iter()
        .map(|r| format!("        <string>{}</string>", xml_escape(r)))
        .collect::<Vec<_>>()
        .join("\n");
    let copy_permission = format!(
        r#"    <hudson.plugins.copyartifact.CopyArtifactPermissionProperty plugin="copyartifact">
      <projectNameList>
{reader_xml}
      </projectNameList>
    </hudson.plugins.copyartifact.CopyArtifactPermissionProperty>
"#
    );
    let token = opts.remote_trigger_token.trim();
    let auth_token = if token.is_empty() {
        String::new()
    } else {
        format!("  <authToken>{}</authToken>\n", xml_escape(token))
    };
    format!(
        r#"<?xml version='1.0' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>{desc_xml}</description>
  <keepDependencies>false</keepDependencies>
  <properties>
{copy_permission}    <hudson.model.ParametersDefinitionProperty>
      <parameterDefinitions>
{params}
      </parameterDefinitions>
    </hudson.model.ParametersDefinitionProperty>
  </properties>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script><![CDATA[{script}]]></script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
{auth_token}</flow-definition>
"#
    )
}

fn jenkinsfile(opts: &JobOpts) -> String {
    one_task_jenkinsfile("lolbench", opts)
}

fn urlencoding_simple(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            _ => format!("%{:02X}", c as u8),
        })
        .collect()
}

pub const DEEPSWE_ONE_TASK: &str = "deepswe_one_task";
pub const SWEBENCHPRO_ONE_TASK: &str = "swebenchpro_one_task";
/// Every suite the job generator knows about.
pub const EVAL_BENCHMARKS: &[&str] = &["deepswe", "lolbench", "swebenchpro"];
pub const EVAL_SHAPES: &[JobShape] = &[JobShape::One, JobShape::Some_, JobShape::FullSuite];
/// Jobs an older mac-k3d created that the current shapes replace.
const RETIRED_JOBS: &[&str] = &["eval_aggregate"];

/// Every eval job `mac-k3d config` writes: one / some / full-suite per suite,
/// then `build_jiuwenswarm_headless` on DeepSWE.
pub fn eval_jobs() -> Vec<(&'static str, JobShape)> {
    let mut jobs: Vec<(&'static str, JobShape)> = EVAL_BENCHMARKS
        .iter()
        .flat_map(|b| EVAL_SHAPES.iter().map(move |s| (*b, *s)))
        .collect();
    jobs.push(("deepswe", JobShape::Headless));
    jobs
}

/// Create or update every eval job, and delete retired ones. Jobs that already
/// exist are rewritten, so running it again after an upgrade is how a
/// controller picks up a new shape. A `build_jiuwenswarm_headless` made by
/// hand is replaced the same way.
pub fn ensure_eval_jobs(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    opts: &JobOpts,
) -> Result<()> {
    for (bench, shape) in eval_jobs() {
        let name = eval_job_name(bench, shape);
        let xml = eval_job_xml(bench, shape, &shape_description(bench, shape), opts);
        ensure_named_one_task_xml(&name, &xml, jenkins_url, api_user, api_token_or_password)?;
    }
    for name in RETIRED_JOBS {
        delete_job_if_present(name, jenkins_url, api_user, api_token_or_password)?;
    }
    Ok(())
}

fn delete_job_if_present(
    job_name: &str,
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
) -> Result<()> {
    let session = Session::open(jenkins_url, api_user, api_token_or_password)?;
    if session.status(&format!("/job/{job_name}/api/json")) != Some(200) {
        return Ok(());
    }
    let reply = session.post(&format!("/job/{job_name}/doDelete"))?;
    if reply.is(&["200", "302", "303"]) {
        println!("Deleted retired job '{job_name}'.");
    } else {
        println!(
            "Warning: could not delete retired job '{job_name}' (HTTP {}); delete it in the UI.",
            reply.code
        );
    }
    Ok(())
}

pub async fn ensure_eval_jobs_from_cluster(
    kubectl: &Path,
    config: &crate::config::MacK3dConfig,
    credential_ids: Vec<String>,
) -> Result<()> {
    if !config.jenkins.enabled {
        return Ok(());
    }
    let url = crate::runtime::jenkins::ui_url(config);
    let password = match crate::runtime::jenkins::admin_password(kubectl, config).await {
        Ok(p) if !p.is_empty() => p,
        Ok(_) | Err(_) => {
            println!("Skipping eval job create — could not read Jenkins admin password yet.");
            return Ok(());
        }
    };
    let opts = JobOpts::from_config(config, credential_ids);
    ensure_eval_jobs(&url, "admin", &password, &opts)
}

fn ensure_named_one_task_xml(
    job_name: &str,
    xml: &str,
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
) -> Result<()> {
    let base = jenkins_url.trim_end_matches('/');

    wait_for_jenkins(base, api_user, api_token_or_password, Duration::from_secs(90))?;

    let session = Session::open(base, api_user, api_token_or_password)?;
    let exists = session.status(&format!("/job/{job_name}/api/json")) == Some(200);

    if exists {
        println!("Updating Jenkins job '{job_name}'…");
        let reply = session.post_xml(&format!("/job/{job_name}/config.xml"), "text/xml", xml)?;
        if reply.is(&["200", "201"]) {
            println!("Updated job '{job_name}'.");
        } else {
            println!("Warning: failed to update '{job_name}' (HTTP {}).", reply.code);
        }
        return Ok(());
    }

    let create = format!("/createItem?name={}", urlencoding_simple(job_name));
    println!("Creating Jenkins job '{job_name}' on {base} …");
    let reply = session.post_xml(&create, "text/xml", xml)?;
    if !reply.is(&["200", "201", "302", "303"]) {
        println!("Warning: failed to create '{job_name}' (HTTP {}).", reply.code);
        return Ok(());
    }
    println!(
        "Created job '{job_name}'.\n\
         Trigger: {base}/job/{job_name}/buildWithParameters  (or: mac-k3d eval)"
    );
    Ok(())
}

/// Ensure Pipeline job `deepswe_one_task` (DeepSWE evaluates the iCode harness with an LLM call).
pub fn ensure_deepswe_one_task(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    opts: &JobOpts,
) -> Result<()> {
    let xml = deepswe_one_task_job_xml(opts);
    ensure_named_one_task_xml(
        DEEPSWE_ONE_TASK,
        &xml,
        jenkins_url,
        api_user,
        api_token_or_password,
    )
}

/// Ensure Pipeline job `swebenchpro_one_task` (SWE-bench Pro evaluates the iCode harness with an LLM call).
pub fn ensure_swebenchpro_one_task(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    opts: &JobOpts,
) -> Result<()> {
    let xml = swebenchpro_one_task_job_xml(opts);
    ensure_named_one_task_xml(
        SWEBENCHPRO_ONE_TASK,
        &xml,
        jenkins_url,
        api_user,
        api_token_or_password,
    )
}

pub async fn ensure_deepswe_one_task_from_cluster(
    kubectl: &Path,
    config: &crate::config::MacK3dConfig,
    credential_ids: Vec<String>,
) -> Result<()> {
    if !config.jenkins.enabled {
        return Ok(());
    }
    let url = crate::runtime::jenkins::ui_url(config);
    let password = match crate::runtime::jenkins::admin_password(kubectl, config).await {
        Ok(p) if !p.is_empty() => p,
        Ok(_) | Err(_) => {
            println!(
                "Skipping '{DEEPSWE_ONE_TASK}' create — could not read Jenkins admin password yet."
            );
            return Ok(());
        }
    };
    let opts = JobOpts::from_config(config, credential_ids);
    ensure_deepswe_one_task(&url, "admin", &password, &opts)
}

pub async fn ensure_swebenchpro_one_task_from_cluster(
    kubectl: &Path,
    config: &crate::config::MacK3dConfig,
    credential_ids: Vec<String>,
) -> Result<()> {
    if !config.jenkins.enabled {
        return Ok(());
    }
    let url = crate::runtime::jenkins::ui_url(config);
    let password = match crate::runtime::jenkins::admin_password(kubectl, config).await {
        Ok(p) if !p.is_empty() => p,
        Ok(_) | Err(_) => {
            println!(
                "Skipping '{SWEBENCHPRO_ONE_TASK}' create — could not read Jenkins admin password yet."
            );
            return Ok(());
        }
    };
    let opts = JobOpts::from_config(config, credential_ids);
    ensure_swebenchpro_one_task(&url, "admin", &password, &opts)
}

fn deepswe_one_task_job_xml(opts: &JobOpts) -> String {
    one_task_job_xml("deepswe", &shape_description("deepswe", JobShape::One), opts)
}

fn swebenchpro_one_task_job_xml(opts: &JobOpts) -> String {
    one_task_job_xml("swebenchpro", &shape_description("swebenchpro", JobShape::One), opts)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample_opts() -> JobOpts {
        JobOpts {
            default_task: "ruff_1".into(),
            default_eval_mode: "binary".into(),
            default_icode_release:
                "https://gitcode.com/example/icode-linux-x86_64-full-v0.1.41.tar.gz".into(),
            default_icode_git_url: "https://gitcode.com/example/icode.git".into(),
            default_icode_git_ref: "main".into(),
            default_icode_git_ref_kind: "branch".into(),
            default_jiuwenswarm_git_url: "https://gitcode.com/michaelling/jiuwenswarm".into(),
            default_jiuwenswarm_git_ref: "2".into(),
            default_jiuwenswarm_git_ref_kind: "pr".into(),
            default_icode_args: "--help".into(),
            default_harness: "icode".into(),
            default_llm: "deepseek".into(),
            default_deepseek_model: eval_catalog::default_model().into(),
            default_benchmark: "lolbench".into(),
            default_n_tasks: 1,
            default_n_rollouts: 4,
            default_tasks: Vec::new(),
            default_shard_size: 2,
            max_in_flight: 0,
            oom_retries: 2,
            ui_profile: UiProfile::Developer,
            credential_ids: vec!["deepseek-api-key".into(), "gitcode-pat".into()],
            remote_trigger_token: String::new(),
        }
    }

    fn deepswe_opts(credential_ids: Vec<String>) -> JobOpts {
        JobOpts {
            default_task: "abs-stepped-slices".into(),
            default_eval_mode: "binary".into(),
            default_icode_release: String::new(),
            default_icode_git_url: String::new(),
            default_icode_git_ref: "main".into(),
            default_icode_git_ref_kind: "branch".into(),
            default_jiuwenswarm_git_url: "https://gitcode.com/michaelling/jiuwenswarm".into(),
            default_jiuwenswarm_git_ref: "2".into(),
            default_jiuwenswarm_git_ref_kind: "pr".into(),
            default_icode_args: String::new(),
            default_harness: "icode".into(),
            default_llm: "deepseek".into(),
            default_deepseek_model: eval_catalog::default_model().into(),
            default_benchmark: "deepswe".into(),
            default_n_tasks: 1,
            default_n_rollouts: 4,
            default_tasks: Vec::new(),
            default_shard_size: 2,
            max_in_flight: 0,
            oom_retries: 2,
            ui_profile: UiProfile::User,
            credential_ids,
            remote_trigger_token: String::new(),
        }
    }

    #[test]
    fn every_eval_job_ticks_remote_trigger_when_a_token_is_set() {
        let mut opts = sample_opts();
        opts.remote_trigger_token = "trigger-token".into();
        for bench in EVAL_BENCHMARKS {
            for shape in EVAL_SHAPES {
                let xml = eval_job_xml(bench, *shape, "d", &opts);
                assert!(
                    xml.contains("<authToken>trigger-token</authToken>"),
                    "{bench} {shape:?} missing authToken"
                );
            }
        }
        let bare = eval_job_xml("deepswe", JobShape::One, "d", &sample_opts());
        assert!(!bare.contains("<authToken>"), "{bare}");
        opts.remote_trigger_token = "a&b".into();
        let escaped = eval_job_xml("deepswe", JobShape::One, "d", &opts);
        assert!(escaped.contains("<authToken>a&amp;b</authToken>"));
    }

    #[test]
    fn remote_trigger_token_is_generated_once_and_reused() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.yaml");
        let mut cfg = crate::config::MacK3dConfig::default();
        cfg.jenkins.host_port = 17070;
        cfg.save(Some(&path)).unwrap();
        ensure_remote_trigger_token(&mut cfg, Some(&path)).unwrap();
        let first = cfg.jenkins.remote_trigger_token.clone();
        assert_eq!(first.len(), 36, "{first}");
        assert!(first.chars().all(|c| c.is_ascii_hexdigit()), "{first}");
        let text = std::fs::read_to_string(&path).unwrap();
        assert!(text.contains(&first), "{text}");
        let mut again = crate::config::MacK3dConfig::load_file(&path).unwrap();
        ensure_remote_trigger_token(&mut again, Some(&path)).unwrap();
        assert_eq!(again.jenkins.remote_trigger_token, first);
    }

    /// The `parameters { ... }` block of a Jenkinsfile.
    fn params_block(jf: &str) -> &str {
        let start = jf.find("  parameters {\n").expect("parameters block") + "  parameters {\n".len();
        let end = start + jf[start..].find("\n  }\n").expect("end of parameters block");
        &jf[start..end]
    }

    fn groovy_param_names(jf: &str) -> Vec<String> {
        params_block(jf)
            .lines()
            .map(|l| {
                let rest = &l[l.find("name: '").expect("name") + "name: '".len()..];
                rest[..rest.find('\'').unwrap()].to_string()
            })
            .collect()
    }

    fn xml_param_names(xml: &str) -> Vec<String> {
        let start = xml.find("<parameterDefinitions>").unwrap();
        let end = xml.find("</parameterDefinitions>").unwrap();
        xml[start..end]
            .lines()
            .filter_map(|l| {
                let l = l.trim();
                l.strip_prefix("<name>")
                    .and_then(|r| r.strip_suffix("</name>"))
                    .map(str::to_string)
            })
            .collect()
    }

    fn visible_names(bench: &str, shape: JobShape, opts: &JobOpts) -> Vec<&'static str> {
        eval_params(bench, shape, opts)
            .into_iter()
            .filter(|p| p.visible(opts.ui_profile))
            .map(|p| p.name)
            .collect()
    }

    #[test]
    fn lolbench_one_task_uses_shared_pipeline() {
        let jf = jenkinsfile(&sample_opts());
        assert!(jf.contains("pipeline/stages/run_all.sh"));
        assert!(jf.contains("BENCHMARK"));
        assert!(jf.contains("lolbench"));
        assert!(jf.contains("ruff_1"));
        assert!(jf.contains("export TASK="));
        assert!(jf.contains("export HARNESS="));
        assert!(jf.contains("export ICODE_MODE="));
        assert!(jf.contains("ICODE_GIT_URL"));
        assert!(jf.contains("ICODE_GIT_REF"));
        assert!(jf.contains("ICODE_GIT_REF_KIND"));
        assert!(!jf.contains("ICODE_SOURCE"));
        assert!(!jf.contains("'source'"));
        assert!(!jf.contains("string(name: 'ICODE_RELEASE'"));
        assert!(jf.contains("ICODE_RELEASE_FILE"));
        assert!(jf.contains("ICODE_RELEASE_UPLOADED"));
        assert!(!jf.contains("--sync-pipeline"));
        assert!(!jf.contains("MAC_K3D_ROOT:-"));
        assert!(!jf.contains(".local/share"));
        assert!(!jf.contains("LLM_NAME"));
        assert!(jf.contains("stashedFile(name: 'ICODE_RELEASE_FILE'"));
        assert!(jf.contains("String upload = params.HARNESS == 'jiuwenswarm' ? 'JIUWENSWARM_RELEASE_FILE' : 'ICODE_RELEASE_FILE'"));
        assert!(jf.contains("unstash upload"));
        assert!(jf.contains("--force-rm"));
        assert!(jf.contains(r#"if [ "${ICODE_MODE}" = "git" ]"#));
        assert!(jf.contains("leave this control as it is"));
        assert!(jf.contains("Vice versa"));
        assert!(jf.contains("ICODE_GIT_REF_KIND:-branch"));
        assert!(!jf.contains("ICODE_GIT_REF_KIND:-auto"));
        assert!(jf.contains("HARBOR_VERSION:-0.22.0"));
        assert!(jf.contains("DEEPSWE_REF:-0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea"));
        assert!(jf.contains("LOLBENCH_REF:-1b10d10bb4a10cea54374ac34b8f76b69dc8ce75"));
        assert!(jf.contains("ICODE_RELEASE_SHA256:-"));
        assert!(jf.contains("ICODE_GIT_SHA:-"));
        assert!(!jf.contains("stage('Pin iCode')"));
        assert!(!jf.contains("OFFICIAL"));
        assert!(!jf.contains("CANARY"));
        assert!(!jf.contains("ICODE_EXPECT_SHA"));
        assert!(jf.contains("upload ICODE_RELEASE_FILE"));
        assert!(jf.contains("icode"));
        // Each worker locks its own cores; a shared pool would let a build on
        // worker A hold tokens that belong to worker B.
        assert!(!jf.contains("lock(label: 'CPU_CORES'"));
        assert!(jf.contains("lock(label: env.NODE_NAME, quantity: cpusEach, variable: 'HELD_CORES')"));
        assert!(jf.contains("eval-runs/cpus_each.txt"));
        assert!(jf.contains("env.CPU_LOCK_QTY = \"${held}\""));
        assert!(jf.contains("withCredentials"));
        assert!(jf.contains("deepseek-api-key"));
        assert!(jf.contains("deepseek-v4-pro"));
        assert!(!jf.contains("harbor run"));
        assert!(!jf.contains("EVAL_MODE"));
        assert!(!jf.contains("icode-in"));
    }

    #[test]
    fn every_suite_gets_exactly_three_jobs() {
        let names: Vec<String> = EVAL_BENCHMARKS
            .iter()
            .flat_map(|b| EVAL_SHAPES.iter().map(move |s| eval_job_name(b, *s)))
            .collect();
        assert_eq!(
            names,
            vec![
                "deepswe_one_task",
                "deepswe_some_task",
                "deepswe_full_suite_task",
                "lolbench_one_task",
                "lolbench_some_task",
                "lolbench_full_suite_task",
                "swebenchpro_one_task",
                "swebenchpro_some_task",
                "swebenchpro_full_suite_task",
            ]
        );
        // The merge happens inside the dispatcher build; no separate job.
        assert_eq!(RETIRED_JOBS, &["eval_aggregate"]);
        let opts = deepswe_opts(Vec::new());
        for bench in EVAL_BENCHMARKS {
            for shape in EVAL_SHAPES {
                let xml = eval_job_xml(bench, *shape, "d", &opts);
                assert!(!xml.contains("eval_aggregate"), "{bench} {shape:?}");
            }
        }
    }

    #[test]
    fn each_shape_shows_only_its_params() {
        let opts = deepswe_opts(Vec::new());
        assert_eq!(
            visible_names("deepswe", JobShape::One, &opts),
            vec![
                "HARNESS",
                "LLM",
                "BENCHMARK",
                "TASK",
                "ICODE_MODE",
                "ICODE_RELEASE_FILE",
                "ICODE_GIT_URL",
                "ICODE_GIT_REF",
                "ICODE_GIT_REF_KIND",
                "DEEPSEEK_MODEL",
            ]
        );
        assert_eq!(
            visible_names("deepswe", JobShape::Some_, &opts),
            vec![
                "HARNESS",
                "LLM",
                "BENCHMARK",
                "TASKS",
                "N_TASKS",
                "N_ROLLOUTS",
                "ICODE_MODE",
                "ICODE_RELEASE_FILE",
                "ICODE_GIT_URL",
                "ICODE_GIT_REF",
                "ICODE_GIT_REF_KIND",
                "DEEPSEEK_MODEL",
            ]
        );
        assert_eq!(
            visible_names("deepswe", JobShape::FullSuite, &opts),
            vec![
                "HARNESS",
                "LLM",
                "BENCHMARK",
                "N_ROLLOUTS",
                "ICODE_MODE",
                "ICODE_RELEASE_FILE",
                "ICODE_GIT_URL",
                "ICODE_GIT_REF",
                "ICODE_GIT_REF_KIND",
                "DEEPSEEK_MODEL",
            ]
        );
    }

    #[test]
    fn shapes_get_their_own_defaults() {
        let mut opts = deepswe_opts(vec!["deepseek-api-key".into()]);
        opts.default_n_tasks = 5;
        opts.default_n_rollouts = 4;
        opts.default_task = "abs-stepped-slices".into();
        let one = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        let some = eval_jenkinsfile("deepswe", JobShape::Some_, &opts);
        let full = eval_jenkinsfile("deepswe", JobShape::FullSuite, &opts);
        // one_task: a single named question, a single attempt.
        assert!(one.contains("string(name: 'TASK', defaultValue: 'abs-stepped-slices'"));
        assert!(one.contains("hidden(name: 'N_ROLLOUTS', defaultValue: '1'"));
        assert!(one.contains("hidden(name: 'N_TASKS', defaultValue: '1'"));
        // some_task: N questions at the full rollout count.
        assert!(some.contains("string(name: 'N_TASKS', defaultValue: '5'"));
        assert!(some.contains("string(name: 'N_ROLLOUTS', defaultValue: '4'"));
        assert!(some.contains("string(name: 'TASKS', defaultValue: ''"));
        // full_suite_task: the whole suite at the full rollout count.
        assert!(full.contains("string(name: 'N_ROLLOUTS', defaultValue: '4'"));
        assert!(full.contains("hidden(name: 'N_TASKS', defaultValue: '113'"));
    }

    #[test]
    fn user_profile_hides_dev_params() {
        let opts = deepswe_opts(Vec::new());
        assert_eq!(opts.ui_profile, UiProfile::User);
        for shape in EVAL_SHAPES {
            let jf = eval_jenkinsfile("deepswe", *shape, &opts);
            let block = params_block(&jf);
            assert!(block.contains("hidden(name: 'AGENT_LABEL', defaultValue: 'lolbench'"), "{shape:?}");
            if *shape == JobShape::One {
                assert!(block.contains("hidden(name: 'N_ROLLOUTS', defaultValue: '1'"), "{shape:?}");
            } else {
                assert!(block.contains("string(name: 'N_ROLLOUTS'"), "{shape:?}");
            }
            assert!(!block.contains("OFFICIAL"), "{shape:?}");
            assert!(!block.contains("CANARY"), "{shape:?}");
            assert!(!block.contains("ICODE_EXPECT_SHA"), "{shape:?}");
            let xml = eval_job_xml("deepswe", *shape, "d", &opts);
            assert!(!xml.contains("<name>OFFICIAL</name>"), "{shape:?}");
            assert!(!xml.contains("<name>CANARY</name>"), "{shape:?}");
        }
        let one = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(params_block(&one).contains("string(name: 'TASK'"));
        let full = eval_jenkinsfile("deepswe", JobShape::FullSuite, &opts);
        assert!(params_block(&full).contains("hidden(name: 'MAX_IN_FLIGHT', defaultValue: '0'"));
        assert!(params_block(&full).contains("hidden(name: 'OOM_RETRIES', defaultValue: '2'"));
        for shape in EVAL_SHAPES {
            let jf = eval_jenkinsfile("deepswe", *shape, &opts);
            let block = params_block(&jf);
            assert!(block.contains("hidden(name: 'TEST_MEMORY_MB', defaultValue: ''"), "{shape:?}");
            assert!(
                block.contains("hidden(name: 'TEST_MEMORY_ATTEMPTS', defaultValue: '1'"),
                "{shape:?}"
            );
        }
    }

    #[test]
    fn developer_profile_shows_dev_params() {
        let mut opts = deepswe_opts(Vec::new());
        opts.ui_profile = UiProfile::Developer;
        let one = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        let block = params_block(&one);
        assert!(block.contains("string(name: 'HARBOR_VERSION', defaultValue: '0.22.0'"));
        assert!(!block.contains("CANARY"));
        assert!(!block.contains("OFFICIAL"));
        // Shard plumbing stays hidden for developers too: the dispatcher owns it.
        assert!(block.contains("hidden(name: 'RUN_GROUP', defaultValue: ''"));
        assert!(block.contains("hidden(name: 'TASK_OFFSET', defaultValue: '0'"));
        assert!(block.contains("hidden(name: 'SHARD', defaultValue: ''"));
        assert!(block.contains("hidden(name: 'REQUESTED_BY', defaultValue: ''"));
        let full = eval_jenkinsfile("deepswe", JobShape::FullSuite, &opts);
        assert!(params_block(&full).contains("string(name: 'MAX_IN_FLIGHT', defaultValue: '0'"));
        assert!(params_block(&full).contains("string(name: 'OOM_RETRIES', defaultValue: '2'"));
        assert!(params_block(&full).contains("string(name: 'N_TASKS', defaultValue: '113'"));
        for shape in EVAL_SHAPES {
            let jf = eval_jenkinsfile("deepswe", *shape, &opts);
            let block = params_block(&jf);
            assert!(block.contains("string(name: 'TEST_MEMORY_MB', defaultValue: ''"), "{shape:?}");
            assert!(
                block.contains("string(name: 'TEST_MEMORY_ATTEMPTS', defaultValue: '1'"),
                "{shape:?}"
            );
            assert!(block.contains("Developer (testing): Testing only: trial memory limit in MB"), "{shape:?}");
        }
        let some = eval_jenkinsfile("deepswe", JobShape::Some_, &opts);
        assert!(some.contains("string(name: 'TEST_MEMORY_MB', value: params.TEST_MEMORY_MB)"));
        assert!(some.contains("string(name: 'TEST_MEMORY_ATTEMPTS', value: params.TEST_MEMORY_ATTEMPTS)"));
    }

    #[test]
    fn harness_llm_benchmark_always_visible() {
        for profile in [UiProfile::User, UiProfile::Developer] {
            let mut opts = deepswe_opts(Vec::new());
            opts.ui_profile = profile;
            for bench in EVAL_BENCHMARKS {
                for shape in EVAL_SHAPES {
                    let jf = eval_jenkinsfile(bench, *shape, &opts);
                    let first: Vec<&str> = params_block(&jf).lines().take(3).collect();
                    let harnesses = if *bench == "deepswe" && *shape == JobShape::One {
                        "['icode', 'jiuwenswarm']"
                    } else {
                        "['icode']"
                    };
                    assert!(
                        first[0].starts_with(&format!("    choice(name: 'HARNESS', choices: {harnesses}")),
                        "{first:?}"
                    );
                    assert!(first[1].starts_with("    choice(name: 'LLM', choices: ['deepseek']"));
                    assert!(first[2].starts_with(&format!("    choice(name: 'BENCHMARK', choices: ['{bench}']")));
                    assert!(jf.contains(DISPLAY_NAME_GROOVY), "{bench} {shape:?}");
                    let xml = eval_job_xml(bench, *shape, "d", &opts);
                    assert_eq!(&xml_param_names(&xml)[..3], &["HARNESS", "LLM", "BENCHMARK"]);
                }
            }
        }
        let some = eval_jenkinsfile("deepswe", JobShape::Some_, &deepswe_opts(Vec::new()));
        for name in ["HARNESS", "LLM", "BENCHMARK"] {
            assert!(some.contains(&format!("string(name: '{name}', value: params.{name})")));
        }
    }

    #[test]
    fn no_mac_k3d_git_params() {
        for profile in [UiProfile::User, UiProfile::Developer] {
            let mut opts = deepswe_opts(Vec::new());
            opts.ui_profile = profile;
            for bench in EVAL_BENCHMARKS {
                for shape in EVAL_SHAPES {
                    let xml = eval_job_xml(bench, *shape, "d", &opts);
                    assert!(!xml.contains("MAC_K3D_GIT"), "{bench} {shape:?} {profile:?}");
                    assert!(!xml.contains("MAC_K3D_SHA"));
                    assert!(!xml.contains("mac-k3d-src"));
                    assert!(!xml.contains("git fetch"));
                }
            }
        }
        assert!(!FORWARDED_PARAMS.iter().any(|p| p.starts_with("MAC_K3D")));
    }

    #[test]
    fn eval_job_runs_one_stage_per_phase_and_locks_only_evaluate() {
        let opts = deepswe_opts(vec!["deepseek-api-key".into()]);
        let jf = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        let phases: Vec<&str> = EVAL_STAGES.iter().map(|s| s.phase).collect();
        assert_eq!(phases, crate::prepare::eval_assets::PHASES);
        let mut last = 0;
        for stage in &EVAL_STAGES {
            let at = jf
                .find(&format!("stage('{}')", stage.title))
                .unwrap_or_else(|| panic!("no stage {}", stage.title));
            assert!(at > last, "stage {} out of order", stage.title);
            last = at;
            let run = format!(r#"MAC_K3D_PHASE={} bash "$MAC_K3D_ROOT/pipeline/stages/run_all.sh""#, stage.phase);
            assert_eq!(jf.matches(&run).count(), 1, "{}", stage.phase);
        }
        assert_eq!(jf.matches("    stage('").count(), 7);
        assert!(!jf.contains("MAC_K3D_PHASE=prepare"));
        // The CPU lock covers the canary and the Harbor run, nothing else.
        assert_eq!(jf.matches("lock(").count(), 1);
        let lock = jf.find("lock(label: env.NODE_NAME").unwrap();
        let evaluate = jf.find("MAC_K3D_PHASE=evaluate ").unwrap();
        assert!(jf.find("stage('Evaluate')").unwrap() < lock);
        assert!(jf.find("MAC_K3D_PHASE=tasks ").unwrap() < lock);
        assert!(lock < evaluate);
        assert!(evaluate < jf.find("stage('Anti-cheat')").unwrap());
        assert!(jf.find("lock(").unwrap() < jf.find("stage('Anti-cheat')").unwrap());
    }

    #[test]
    fn bootstrap_extracts_pipeline_from_installed_binary() {
        let opts = deepswe_opts(vec!["deepseek-api-key".into()]);
        let jf = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(jf.contains(r#"export MAC_K3D_ROOT="${WORKSPACE}/mac-k3d-pipeline""#));
        assert!(jf.contains(r#"if [ ! -f "$MAC_K3D_ROOT/pipeline/BUILD.json" ]; then"#));
        assert!(jf.contains(r#"mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean"#));
        assert!(!jf.contains("git fetch"));
        assert!(!jf.contains("MAC_K3D_GIT"));
        // The first stage starts from a fresh extract; the other six reuse it.
        // Only pipeline/ goes: output/ under the same root keeps the archive
        // phase's backups.
        let clear = r#"rm -rf "${WORKSPACE}/mac-k3d-pipeline/pipeline""#;
        assert_eq!(jf.matches(clear).count(), 1);
        let first = jf.find("stage('Environment')").unwrap();
        let second = jf.find("stage('Tasks')").unwrap();
        let at = jf.find(clear).unwrap();
        assert!(first < at && at < second);
        assert_eq!(jf.matches("mac-k3d pipeline --extract-to").count(), 8);
        // Jenkins does not trace a script that starts with a shebang, so the
        // prelude is not printed on every stage. The one-line summary is the
        // Environment stage only.
        assert_eq!(jf.matches("#!/bin/bash").count(), 10);
        assert_eq!(
            jf.matches("echo \"MAC_K3D_ROOT=$MAC_K3D_ROOT BENCHMARK=$BENCHMARK HARNESS=$HARNESS ICODE_MODE=$ICODE_MODE${JIUWENSWARM_MODE:+ JIUWENSWARM_MODE=$JIUWENSWARM_MODE}\"").count(),
            1
        );

        let some = eval_jenkinsfile("deepswe", JobShape::Some_, &opts);
        assert!(some.contains(r#"mac-k3d pipeline --extract-to "$MAC_K3D_ROOT" --require-clean"#));
        assert!(some.contains(r#"python3 "$MAC_K3D_ROOT/pipeline/lib/aggregate_runs.py""#));
        assert!(!some.contains("git fetch"));
    }

    #[test]
    fn no_resume_or_cpu_lock_qty_param() {
        for profile in [UiProfile::User, UiProfile::Developer] {
            let mut opts = deepswe_opts(Vec::new());
            opts.ui_profile = profile;
            for bench in EVAL_BENCHMARKS {
                for shape in EVAL_SHAPES {
                    let xml = eval_job_xml(bench, *shape, "d", &opts);
                    assert!(!xml.contains("RESUME"), "{bench} {shape:?}");
                    assert!(!xml.contains("name: 'CPU_LOCK_QTY'"));
                    assert!(!xml.contains("<name>CPU_LOCK_QTY</name>"));
                    assert!(!xml.contains("'SHARDS'"));
                    assert!(!xml.contains("SHARD_JOB"));
                }
            }
        }
    }

    #[test]
    fn some_task_dispatches_to_one_task() {
        let jf = eval_jenkinsfile("deepswe", JobShape::Some_, &deepswe_opts(Vec::new()));
        // The dispatcher holds no executor while its shards queue for one.
        assert!(jf.starts_with("pipeline {\n  agent none"));
        // The queue lock is controller state. It is not a worker CPU lock.
        assert!(!jf.contains("lock(label:"));
        assert!(jf.contains("lock(resource: queueLock)"));
        assert!(!jf.contains("run_all.sh"));
        assert!(jf.contains("build job: 'deepswe_one_task'"));
        assert!(jf.contains("params.TASKS?.trim()"));
        assert!(jf.contains("parallel branches"));
        assert!(jf.contains("propagate: false"));
        assert!(jf.contains("string(name: 'TASK_OFFSET', value: ids ? '0' : \"${unit.offset}\")"));
        assert!(jf.contains("string(name: 'N_ROLLOUTS', value: '1')"));
        assert!(jf.contains("string(name: 'ROLLOUT', value: \"${unit.rollout}\")"));
        assert!(jf.contains("quietPeriod: 0"));
        assert!(jf.contains("string(name: 'SHARD', value: note)"));
        assert!(jf.contains("string(name: 'ICODE_MODE', value: params.ICODE_MODE)"));
        // One Prepare harness stage pins the harness and its bundle for every shard.
        assert!(!jf.contains("stage('iCode release')"));
        assert!(!jf.contains("stage('Pin iCode')"));
        assert!(!jf.contains("ICODE_RELEASE_FROM"));
        assert!(jf.contains("stage('Prepare harness')"));
        assert!(jf.contains("bash \"$MAC_K3D_ROOT/pipeline/tools/prepare_harness.sh\""));
        assert!(jf.contains("string(name: 'ICODE_GIT_SHA', value: env.ICODE_GIT_SHA ?: '')"));
        assert!(jf.contains("string(name: 'ICODE_RELEASE_SHA256', value: env.ICODE_RELEASE_SHA256 ?: '')"));
        assert!(jf.contains("string(name: 'JIUWENSWARM_GIT_SHA', value: env.JIUWENSWARM_GIT_SHA ?: '')"));
        assert!(jf.contains(
            "string(name: 'HARNESS_BUNDLE_FROM', value: \"${env.JOB_NAME}#${env.BUILD_NUMBER}\")"
        ));
        assert!(jf.contains("string(name: 'HARNESS_BUNDLE_SHA256', value: env.HARNESS_BUNDLE_SHA256)"));
        assert!(jf.contains("archiveArtifacts artifacts: 'eval-runs/harness/bundle.tar.gz, eval-runs/harness/bundle.json'"));
        assert!(!jf.contains("sha256.txt"));
        assert!(jf.contains("archive/eval-runs/harness/bundle.tar.gz"));
        assert!(jf.contains("#!/bin/bash"));
        assert!(jf.contains("copyArtifactPermission('deepswe_one_task')"));
        assert!(jf.contains("unstable \"Shards that did not succeed"));
        // The merge runs in this build, on a worker, from the shard build numbers.
        assert!(jf.contains(
            "stage('Aggregate') {\n      agent { label \"${params.AGGREGATE_LABEL?.trim() ?: params.AGENT_LABEL}\" }"
        ));
        assert!(jf.contains("copyArtifacts projectName: 'deepswe_one_task'"));
        assert!(jf.contains("selector: specific(n)"));
        assert!(jf.contains("env.SHARD_BUILDS = shardBuilds.values().join(',')"));
        assert!(jf.contains("aggregate_runs.py"));
        assert!(jf.contains("archiveArtifacts artifacts: 'aggregate/**'"));
        let own: Vec<&str> = eval_params("deepswe", JobShape::Some_, &deepswe_opts(Vec::new())).iter().map(|p| p.name).collect();
        for name in FORWARDED_PARAMS {
            let line = format!("string(name: '{name}', value: params.{name})");
            assert_eq!(jf.contains(&line), own.contains(name), "{name}");
        }
    }

    // #region agent log
    #[test]
    #[ignore]
    fn debug_8643d4_dump_headless_jenkinsfile() {
        let jf = eval_jenkinsfile("deepswe", JobShape::Headless, &deepswe_opts(vec!["deepseek-api-key".into(), "gitcode-pat".into()]));
        std::fs::write("/tmp/mk3d-8643d4-headless.Jenkinsfile", jf).unwrap();
    }
    // #endregion

    #[test]
    fn every_dispatcher_prepares_the_harness_once_for_its_shards() {
        let opts = deepswe_opts(vec!["deepseek-api-key".into(), "gitcode-pat".into()]);
        for bench in EVAL_BENCHMARKS {
            for shape in dispatchers(bench) {
                let jf = eval_jenkinsfile(bench, shape, &opts);
                let prep = jf.find("stage('Prepare harness')").unwrap_or_else(|| panic!("{bench} {shape:?}"));
                let shards = jf.find("stage('Shards')").unwrap();
                assert!(prep < shards, "{bench} {shape:?}");
                let stage = &jf[prep..shards];
                // A worker that cannot build it is left out, and another builds it once.
                assert!(!stage.contains("agent { label"), "{bench} {shape:?}");
                assert!(stage.contains("prepareOn(params.AGENT_LABEL)"));
                assert!(stage.contains("String elsewhere = \"(${params.AGENT_LABEL}) && !\\\"${at.node}\\\"\""));
                assert!(stage.contains("others = nodesByLabel(label: elsewhere, offline: false)"));
                assert_eq!(stage.matches("prepareOn(elsewhere)").count(), 1, "{bench} {shape:?}");
                let abort = stage.find("catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {\n            throw e").unwrap();
                assert!(abort < stage.find("catch (Throwable t) {\n            String elsewhere").unwrap());
                // Git mode prepares too: there is no release-only condition.
                assert!(!stage.contains("when {"), "{bench} {shape:?}");
                // The PAT reaches a private clone; no LLM key reaches the build.
                assert!(stage.contains(
                    "withCredentials([string(credentialsId: 'gitcode-pat', variable: 'GITCODE_TOKEN')])"
                ));
                assert!(!stage.contains("DEEPSEEK_API_KEY"), "{bench} {shape:?}");
                assert!(stage.contains("bash \"$MAC_K3D_ROOT/pipeline/tools/prepare_harness.sh\""));
                assert!(stage.contains("env.HARNESS_BUNDLE_SHA256 = sha"));
                assert!(stage.contains("readFile('eval-runs/harness/pin.txt')"));
                assert!(stage.contains(
                    "archiveArtifacts artifacts: 'eval-runs/harness/bundle.tar.gz, eval-runs/harness/bundle.json'"
                ));
                assert!(jf.contains("string(name: 'HARNESS_BUNDLE_SHA256', value: env.HARNESS_BUNDLE_SHA256)"));
                // After the run, aborted or not, the bundle leaves the build page; bundle.json stays.
                let cleanup = jf.find("new File(root, \"archive/eval-runs/harness/bundle.tar.gz\")").unwrap();
                let post = jf.rfind("\n  post {\n    always {").unwrap();
                assert!(jf.find("stage('Aggregate')").unwrap() < post && post < cleanup, "{bench} {shape:?}");
                assert_eq!(jf.matches("archive/eval-runs/harness/bundle.tar.gz").count(), 1);
                // JiuwenSwarm pins reach only a shard job that takes them.
                assert_eq!(jf.contains("name: 'JIUWENSWARM_GIT_SHA'"), *bench == "deepswe", "{bench}");
            }
        }
    }

    #[test]
    fn a_shard_copies_the_bundle_only_when_its_store_lacks_it() {
        for bench in EVAL_BENCHMARKS {
            let jf = eval_jenkinsfile(bench, JobShape::One, &deepswe_opts(Vec::new()));
            let stage = &jf[jf.find("stage('Tasks')").unwrap()..jf.find("stage('Evaluate')").unwrap()];
            let check = stage.find("harness_store.py\" get").expect("store check");
            let copy = stage.find("filter: 'eval-runs/harness/bundle.tar.gz'").expect("copy");
            let run = stage.find("MAC_K3D_PHASE=tasks").unwrap();
            assert!(check < copy && copy < run, "{bench}");
            assert!(stage.contains("if (stored == 0) {"));
            assert!(stage.contains("not copying it"));
            // The pipeline names the store; the job bakes in no worker path.
            assert!(!stage.contains("--store"));
            assert_eq!(jf.matches("harness_store.py\" get").count(), 1, "{bench}");
            assert!(jf.contains("export HARNESS_BUNDLE_FILE=\"${WORKSPACE}/harness-bundle/bundle.tar.gz\""));
            let post = &jf[jf.find("  post {").unwrap()..];
            assert!(post.contains("rm -rf \"${WORKSPACE}/harness-bundle\""));
            // A shard with a bundle needs no iCode release upload.
            assert!(jf.contains("[ \"$HARNESS\" = icode ] && [ -z \"$HARNESS_BUNDLE_SHA256\" ]"));
        }
    }

    #[test]
    fn deepswe_one_task_offers_both_harnesses_and_the_rest_stay_fixed() {
        fn choices(bench: &str, shape: JobShape, opts: &JobOpts) -> Vec<String> {
            match &eval_params(bench, shape, opts)[0] {
                ParamSpec { name: "HARNESS", kind: ParamKind::Choice(c), .. } => c.clone(),
                other => panic!("first param is {}", other.name),
            }
        }
        let mut opts = deepswe_opts(Vec::new());
        assert_eq!(choices("deepswe", JobShape::One, &opts), ["icode", "jiuwenswarm"]);
        assert_eq!(choices("deepswe", JobShape::Some_, &opts), ["icode"]);
        assert_eq!(choices("lolbench", JobShape::One, &opts), ["icode"]);
        assert_eq!(choices("deepswe", JobShape::Headless, &opts), ["jiuwenswarm"]);
        opts.default_harness = "jiuwenswarm".into();
        assert_eq!(choices("deepswe", JobShape::One, &opts), ["jiuwenswarm", "icode"]);
        // A suite dispatcher's page has iCode's inputs; JiuwenSwarm's dispatcher is build_jiuwenswarm_headless.
        assert_eq!(choices("deepswe", JobShape::FullSuite, &opts), ["icode"]);
        // A benchmark JiuwenSwarm does not run keeps iCode.
        assert_eq!(choices("lolbench", JobShape::One, &opts), ["icode"]);
        let jf = eval_jenkinsfile("deepswe", JobShape::One, &deepswe_opts(Vec::new()));
        assert!(params_block(&jf).contains("choice(name: 'HARNESS', choices: ['icode', 'jiuwenswarm']"));
    }

    #[test]
    fn the_aggregate_leaves_transcripts_and_trial_copies_in_the_shard_builds() {
        for shape in dispatchers("deepswe") {
            let jf = eval_jenkinsfile("deepswe", shape, &deepswe_opts(Vec::new()));
            let stage = &jf[jf.find("stage('Aggregate')").unwrap()..];
            assert!(stage.contains(
                "filter: 'eval-runs/**',\n              excludes: 'eval-runs/harness/harbor_runs/**/agent/icode-project/**',"
            ));
            assert!(stage.contains(
                "archiveArtifacts artifacts: 'aggregate/**', excludes: 'aggregate/harness/**', allowEmptyArchive: true"
            ));
            assert!(stage.contains("archiveArtifacts artifacts: 'backup/**'"));
            // The report names the shard builds that keep the transcripts.
            assert!(stage.contains("--shard-job 'deepswe_one_task'"));
            assert!(params_block(&jf).contains("hidden(name: 'AGGREGATE_LABEL', defaultValue: ''"));
            // Not forwarded: a shard always runs on AGENT_LABEL.
            assert!(!jf.contains("string(name: 'AGGREGATE_LABEL', value:"));
        }
        let one = eval_jenkinsfile("deepswe", JobShape::One, &deepswe_opts(Vec::new()));
        assert!(!one.contains("AGGREGATE_LABEL"));
    }

    #[test]
    fn one_task_warns_when_rollouts_have_no_score() {
        let opts = deepswe_opts(Vec::new());
        for bench in EVAL_BENCHMARKS {
            let jf = eval_jenkinsfile(bench, JobShape::One, &opts);
            let post = &jf[jf.find("  post {").unwrap()..];
            assert!(post.contains("if (fileExists('eval-runs/unscored_rollouts.txt')) {"), "{bench}");
            assert!(post.contains("String lost = readFile('eval-runs/unscored_rollouts.txt').trim()"));
            assert!(post.contains(r"if (lost ==~ /\d+/ && (lost as Integer) > 0) {"));
            assert!(post.contains(
                "echo \"WARNING: ${lost} rollouts have no score (causes above; see Unscored in summary.md)\""
            ));
            // A scored rollout's own failure never changes the build result.
            // Out of memory does: the rollout is re-queued, and this build is unstable.
            assert!(jf.contains("currentBuild.result = 'UNSTABLE'"), "{bench}");
            assert!(!jf.contains("unstable \""), "{bench}");
            // The dispatcher's retry rule reads this.
            assert!(post.contains(
                "if (fileExists(\"eval-runs/harness/harbor_runs/jenkins-${env.BUILD_NUMBER}\")) {\n          env.HARBOR_STARTED = '1'"
            ));
            let warned = post.find("unscored_rollouts.txt").unwrap();
            assert!(post.find("last_output.txt").unwrap() < warned);
            assert!(post.find("harbor_runs/jenkins-").unwrap() < warned);
        }
    }

    #[test]
    fn some_task_is_unstable_only_for_a_shard_that_did_not_finish() {
        let some = eval_jenkinsfile("deepswe", JobShape::Some_, &deepswe_opts(Vec::new()));
        assert!(!some.contains("unscored_rollouts.txt"));
        assert!(some.contains("if (['FAILURE', 'ABORTED', 'NOT_BUILT'].contains(run.result)) {"));
        assert!(!some.contains("run.result != 'SUCCESS'"));
        assert!(some.contains("unstable \"Shards that did not succeed"));
        // One build statement. A failure before Harbor, or an OOM, goes back on the tail.
        assert!(some.contains("run.result == 'FAILURE' && !started && !unit.preHarbor"));
        assert!(some.contains("oom == '1' && (unit.attempt as Integer) <= oomRetries"));
        assert!(some.contains("units << unit"));
        assert!(some.contains("started = run.buildVariables?.HARBOR_STARTED ?: ''"));
        assert!(some.contains("oom = run.buildVariables?.TRIAL_OOM ?: ''"));
        assert!(some.contains(
            "shardPlan[key] = \"${unit.index + 1}|${run.number}|${run.result}|${unit.offset}|1|${unit.tasks}|${unit.rollout}|${unit.attempt}\""
        ));
        let launches: Vec<_> = some.match_indices("build job: 'deepswe_one_task'").map(|(i, _)| i).collect();
        assert_eq!(launches.len(), 1);
        assert_eq!(some.matches("parameters: shardParams").count(), 1);
        // An unreadable variable map means no retry: tokens are never spent twice.
        assert!(some.contains("started = 'unknown'"));
    }

    #[test]
    fn a_direct_build_requeued_after_oom_sends_no_upload_as_a_string() {
        let one = eval_jenkinsfile("deepswe", JobShape::One, &deepswe_opts(Vec::new()));
        assert!(one.contains(
            "if (name in ['ATTEMPT', 'ICODE_RELEASE_FILE', 'JIUWENSWARM_RELEASE_FILE']) { return }"
        ));
    }

    #[test]
    fn full_suite_dispatches_shards_then_aggregates() {
        let opts = deepswe_opts(vec!["deepseek-api-key".into()]);
        let jf = eval_jenkinsfile("deepswe", JobShape::FullSuite, &opts);
        assert!(jf.contains("agent none"));
        assert!(!jf.contains("lock(label:"));
        assert!(jf.contains("build job: 'deepswe_one_task'"));
        assert!(!jf.contains("params.TASKS"));
        assert!(jf.contains("parallel branches"));
        assert!(jf.contains("env.RUN_GROUP"));
        assert!(jf.contains("TASK_OFFSET"));
        assert!(jf.contains("for (int r = 1; r <= rollouts; r++)"));
        assert!(jf.contains("cap = totalExec * 2"));
        assert!(jf.contains("MAX_IN_FLIGHT falls back to 20"));
        assert!(jf.contains("hidden(name: 'N_TASKS', defaultValue: '113'"));
        assert!(jf.contains("stage('Aggregate')"));
        assert!(jf.contains("stage('Prepare harness')"));
        assert!(jf.contains("string(name: 'ICODE_GIT_SHA', value: env.ICODE_GIT_SHA ?: '')"));
        assert!(!jf.contains("eval_aggregate"));
        assert!(!jf.contains("SHARDS"));

        let lol = eval_jenkinsfile("lolbench", JobShape::FullSuite, &opts);
        assert!(lol.contains("build job: 'lolbench_one_task'"));
        assert!(lol.contains("hidden(name: 'N_TASKS', defaultValue: '20'"));
    }

    /// Names the rendered Build with Parameters page shows (hidden ones left out).
    fn page_names(jf: &str) -> Vec<String> {
        params_block(jf)
            .lines()
            .filter(|l| !l.trim_start().starts_with("hidden("))
            .map(|l| {
                let rest = &l[l.find("name: '").expect("name") + "name: '".len()..];
                rest[..rest.find('\'').unwrap()].to_string()
            })
            .collect()
    }

    #[test]
    fn developer_page_is_the_user_page_plus_extras() {
        let user = deepswe_opts(Vec::new());
        let mut dev = user.clone();
        dev.ui_profile = UiProfile::Developer;
        for (bench, shape) in eval_jobs() {
            let specs = eval_params(bench, shape, &user);
            let page = |profile: UiProfile| -> Vec<(&str, String)> {
                specs
                    .iter()
                    .filter(|p| p.visible(profile))
                    .map(|p| (p.name, p.default_value()))
                    .collect()
            };
            let (user_page, dev_page) = (page(UiProfile::User), page(UiProfile::Developer));
            // Every user field, in the same order with the same default, comes first.
            assert_eq!(&dev_page[..user_page.len()], &user_page[..], "{bench} {shape:?}");
            for p in specs.iter().filter(|p| p.visible(UiProfile::Developer) && !p.visible(UiProfile::User)) {
                assert_eq!(p.show, Show::Developer, "{bench} {shape:?} {}", p.name);
                assert!(p.description.starts_with(DEVELOPER_PREFIX), "{bench} {shape:?} {}", p.name);
            }
            for p in specs.iter().filter(|p| p.show != Show::Developer) {
                assert!(!p.description.starts_with(DEVELOPER_PREFIX), "{bench} {shape:?} {}", p.name);
            }
            // Same on the rendered pages.
            let user_names = page_names(&eval_jenkinsfile(bench, shape, &user));
            let dev_names = page_names(&eval_jenkinsfile(bench, shape, &dev));
            assert_eq!(&dev_names[..user_names.len()], &user_names[..], "{bench} {shape:?}");
            assert!(dev_names.len() > user_names.len(), "{bench} {shape:?}");
        }
    }

    #[test]
    fn job_params_mark_shard_plumbing_and_developer_fields() {
        let one = job_params("deepswe", JobShape::One);
        let get = |ps: &[JobParam], n: &str| ps.iter().find(|p| p.name == n).cloned().expect(n);
        for n in [
            "TASKS", "N_TASKS", "TASK_OFFSET", "RUN_GROUP", "SHARD", "REQUESTED_BY",
            "HARNESS_BUNDLE_FROM", "HARNESS_BUNDLE_SHA256", "ICODE_RELEASE_SHA256", "ICODE_GIT_SHA",
            "JIUWENSWARM_GIT_SHA", "JIUWENSWARM_RELEASE_FROM", "JIUWENSWARM_RELEASE_SHA256",
        ] {
            assert!(!get(&one, n).settable, "{n}");
        }
        // Only DeepSWE runs JiuwenSwarm, so only its shard job takes JiuwenSwarm pins.
        let lol = job_params("lolbench", JobShape::One);
        assert!(lol.iter().any(|p| p.name == "HARNESS_BUNDLE_SHA256"));
        assert!(!lol.iter().any(|p| p.name.starts_with("JIUWENSWARM_")));
        assert!(get(&one, "TASK").settable && !get(&one, "TASK").developer);
        assert!(get(&one, "HARBOR_VERSION").settable && get(&one, "HARBOR_VERSION").developer);
        let some = job_params("deepswe", JobShape::Some_);
        assert!(get(&some, "TASKS").settable && !get(&some, "TASKS").developer);
        assert!(get(&some, "MAX_IN_FLIGHT").settable && get(&some, "MAX_IN_FLIGHT").developer);
        assert!(get(&some, "OOM_RETRIES").settable && get(&some, "OOM_RETRIES").developer);
        assert!(!get(&one, "N_ROLLOUTS").settable);
        assert!(!get(&one, "ROLLOUT").settable);
        assert!(!get(&one, "ATTEMPT").settable);
        assert!(get(&some, "AGENT_LABEL").developer);
        assert!(get(&some, "AGGREGATE_LABEL").settable && get(&some, "AGGREGATE_LABEL").developer);
        assert!(!one.iter().any(|p| p.name == "AGGREGATE_LABEL"));
        assert!(some.iter().all(|p| p.settable));
        let full = job_params("deepswe", JobShape::FullSuite);
        assert!(get(&full, "N_TASKS").developer);
        assert!(!full.iter().any(|p| p.name == "TASKS"));
        for shape_params in [&one, &some, &full] {
            assert!(get(shape_params, "TEST_MEMORY_MB").settable && get(shape_params, "TEST_MEMORY_MB").developer);
            assert!(
                get(shape_params, "TEST_MEMORY_ATTEMPTS").settable
                    && get(shape_params, "TEST_MEMORY_ATTEMPTS").developer
            );
        }
        assert_eq!(JobShape::from_cli("some"), Some(JobShape::Some_));
        assert_eq!(JobShape::from_cli("full_suite_task"), Some(JobShape::FullSuite));
        assert_eq!(JobShape::from_cli("ONE"), Some(JobShape::One));
        assert_eq!(JobShape::from_cli("all"), None);
    }

    #[test]
    fn dispatchers_count_workers_with_nodes_by_label_and_fail_fast() {
        let opts = deepswe_opts(Vec::new());
        for bench in EVAL_BENCHMARKS {
            for shape in dispatchers(bench) {
                let jf = eval_jenkinsfile(bench, shape, &opts);
                // nodesWithLabel is not a Jenkins step: a build calling it counts 0 workers.
                assert!(!jf.contains("nodesWithLabel"), "{bench} {shape:?}");
                assert!(jf.contains(
                    "workerNames = nodesByLabel(label: fits, offline: false)"
                ));
                assert!(jf.contains("fits-${params.BENCHMARK}"));
                assert!(jf.contains("if (workerNames != null && workerNames.isEmpty()) {"));
                assert!(jf.contains("online = nodesByLabel(label: 'lolbench', offline: false)"));
                assert!(jf.contains(
                    "error \"No online worker has label ${fits}. Online lolbench workers: ${online ? online.join(', ') : 'none'}.\""
                ));
                assert!(jf.contains("echo \"WARNING: could not count workers for label"));
                assert!(jf.contains("executors=${totalExec}"));
                assert!(jf.contains("for (int n = 1; n <= 64; n++)"));
                assert!(jf.contains("cap = totalExec * 2"));
                assert!(jf.contains("mode = 'fixed'"));
                assert!(jf.contains("some executors will sit idle"));
                assert!(jf.contains("MAX_IN_FLIGHT falls back to 20"));
                // The fail-fast check runs before any rollout is queued.
                assert!(jf.find("workerNames.isEmpty()").unwrap() < jf.find("build job:").unwrap());
            }
        }
    }

    #[test]
    fn groovy_and_xml_param_sets_match() {
        for profile in [UiProfile::User, UiProfile::Developer] {
            let mut opts = deepswe_opts(vec!["deepseek-api-key".into()]);
            opts.ui_profile = profile;
            for (bench, shape) in eval_jobs() {
                let jf = eval_jenkinsfile(bench, shape, &opts);
                let xml = eval_job_xml(bench, shape, "d", &opts);
                assert_eq!(groovy_param_names(&jf), xml_param_names(&xml), "{bench} {shape:?}");
                assert_eq!(
                    params_block(&jf).matches("    hidden(").count(),
                    xml.matches("<com.wangyin.parameter.WHideParameterDefinition").count(),
                    "{bench} {shape:?} {profile:?}"
                );
            }
        }
    }

    #[test]
    fn config_writes_build_jiuwenswarm_headless_after_the_suite_jobs() {
        let jobs = eval_jobs();
        assert_eq!(jobs.len(), EVAL_BENCHMARKS.len() * EVAL_SHAPES.len() + 1);
        assert_eq!(jobs.last(), Some(&("deepswe", JobShape::Headless)));
        assert_eq!(eval_job_name("deepswe", JobShape::Headless), "build_jiuwenswarm_headless");
        let names: Vec<String> = jobs.iter().map(|(b, s)| eval_job_name(b, *s)).collect();
        assert_eq!(names.iter().filter(|n| *n == JIUWENSWARM_HEADLESS).count(), 1);
    }

    #[test]
    fn build_jiuwenswarm_headless_page_has_jiuwenswarm_inputs_only() {
        let mut opts = deepswe_opts(vec!["deepseek-api-key".into(), "gitcode-pat".into()]);
        assert_eq!(
            visible_names("deepswe", JobShape::Headless, &opts),
            [
                "HARNESS", "LLM", "BENCHMARK", "TASKS", "N_TASKS", "N_ROLLOUTS",
                "JIUWENSWARM_MODE", "JIUWENSWARM_RELEASE_FILE", "JIUWENSWARM_GIT_URL",
                "JIUWENSWARM_GIT_REF", "JIUWENSWARM_GIT_REF_KIND", "DEEPSEEK_MODEL",
                "JIUWENSWARM_AGENT_MODE", "JIUWENSWARM_MAX_TURNS",
            ]
        );
        opts.ui_profile = UiProfile::Developer;
        let developer: Vec<&str> = eval_params("deepswe", JobShape::Headless, &opts)
            .into_iter()
            .filter(|p| p.show == Show::Developer)
            .map(|p| p.name)
            .collect();
        assert_eq!(
            developer,
            [
                "MAX_IN_FLIGHT", "OOM_RETRIES", "TEST_MEMORY_MB", "TEST_MEMORY_ATTEMPTS",
                "AGENT_LABEL", "AGGREGATE_LABEL", "HARBOR_VERSION", "DEEPSWE_REF",
            ]
        );
        let all: Vec<&str> = eval_params("deepswe", JobShape::Headless, &opts).iter().map(|p| p.name).collect();
        assert!(!all.iter().any(|n| n.starts_with("ICODE_") || *n == "LOLBENCH_REF"), "{all:?}");

        let jf = eval_jenkinsfile("deepswe", JobShape::Headless, &opts);
        let block = params_block(&jf);
        assert!(block.contains("choice(name: 'HARNESS', choices: ['jiuwenswarm']"));
        assert!(block.contains("choice(name: 'BENCHMARK', choices: ['deepswe']"));
        // Empty TASKS runs every question.
        assert!(block.contains("string(name: 'TASKS', defaultValue: ''"));
        assert!(block.contains("string(name: 'N_TASKS', defaultValue: '113'"));
        assert!(block.contains("choice(name: 'JIUWENSWARM_MODE', choices: ['git', 'release']"));
        assert!(block.contains("stashedFile(name: 'JIUWENSWARM_RELEASE_FILE'"));
        assert!(block.contains(
            "string(name: 'JIUWENSWARM_GIT_URL', defaultValue: 'https://gitcode.com/michaelling/jiuwenswarm'"
        ));
        assert!(block.contains("string(name: 'JIUWENSWARM_GIT_REF', defaultValue: '2'"));
        assert!(block.contains("choice(name: 'JIUWENSWARM_GIT_REF_KIND', choices: ['pr', 'branch', 'tag', 'commit']"));
        assert!(block.contains("choice(name: 'JIUWENSWARM_AGENT_MODE', choices: ['agent.code.normal', 'agent.code.plan']"));
        assert!(block.contains("string(name: 'JIUWENSWARM_MAX_TURNS', defaultValue: '500'"));
        let desc = shape_description("deepswe", JobShape::Headless);
        assert!(desc.starts_with("Evaluate the JiuwenSwarm harness on DeepSWE"), "{desc}");
        assert!(desc.contains("all 113 by default") && desc.contains("deepswe_one_task"), "{desc}");
    }

    #[test]
    fn build_jiuwenswarm_headless_queues_deepswe_one_task_with_its_inputs() {
        let opts = deepswe_opts(vec!["deepseek-api-key".into(), "gitcode-pat".into()]);
        let jf = eval_jenkinsfile("deepswe", JobShape::Headless, &opts);
        assert!(jf.contains("build job: 'deepswe_one_task'"), "the shard job");
        assert!(jf.contains("copyArtifactPermission('deepswe_one_task')"));
        assert!(jf.contains("stage('Prepare harness')"));
        assert!(jf.contains("params.HARNESS == 'jiuwenswarm' ? params.JIUWENSWARM_MODE : params.ICODE_MODE"));
        // A list of ids, as some_task reads it.
        assert!(jf.contains("ids = params.TASKS.split(',')"));
        for name in [
            "HARNESS", "BENCHMARK", "DEEPSEEK_MODEL", "JIUWENSWARM_MODE", "JIUWENSWARM_GIT_URL",
            "JIUWENSWARM_GIT_REF", "JIUWENSWARM_GIT_REF_KIND", "JIUWENSWARM_AGENT_MODE",
            "JIUWENSWARM_MAX_TURNS", "AGENT_LABEL", "HARBOR_VERSION", "DEEPSWE_REF",
        ] {
            assert!(jf.contains(&format!("string(name: '{name}', value: params.{name})")), "{name}");
        }
        for name in ["ICODE_MODE", "ICODE_GIT_URL", "ICODE_GIT_REF", "LOLBENCH_REF"] {
            assert!(!jf.contains(&format!("string(name: '{name}', value: params.{name})")), "{name}");
        }
        for pin in ["JIUWENSWARM_GIT_SHA", "JIUWENSWARM_RELEASE_FROM", "JIUWENSWARM_RELEASE_SHA256"] {
            assert!(jf.contains(&format!("string(name: '{pin}', value: env.{pin} ?: '')")), "{pin}");
        }
        assert!(jf.contains("string(name: 'HARNESS_BUNDLE_SHA256', value: env.HARNESS_BUNDLE_SHA256)"));
        assert!(jf.contains("archiveArtifacts artifacts: 'jiuwenswarm-release/JIUWENSWARM_RELEASE_FILE'"));
        assert!(jf.contains("copyArtifacts projectName: 'deepswe_one_task'"), "the Aggregate");

        // deepswe_one_task lets it copy the shards' artifacts, and runs its harness.
        let xml = eval_job_xml("deepswe", JobShape::One, "d", &opts);
        assert!(xml.contains("<string>build_jiuwenswarm_headless</string>"));
        assert!(eval_jenkinsfile("deepswe", JobShape::One, &opts)
            .contains("copyArtifactPermission('deepswe_some_task,deepswe_full_suite_task,build_jiuwenswarm_headless')"));
        assert!(shape_description("deepswe", JobShape::One)
            .contains("each shard of deepswe_some_task, deepswe_full_suite_task and build_jiuwenswarm_headless"));
        assert!(shape_description("lolbench", JobShape::One)
            .contains("each shard of lolbench_some_task and lolbench_full_suite_task;"));
        let lol = eval_job_xml("lolbench", JobShape::One, "d", &opts);
        assert!(!lol.contains("build_jiuwenswarm_headless"));
    }

    #[test]
    fn deepswe_one_task_takes_jiuwenswarm_inputs_on_the_developer_page() {
        let mut opts = deepswe_opts(Vec::new());
        let user = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(params_block(&user).contains("hidden(name: 'JIUWENSWARM_MODE', defaultValue: 'git'"));
        assert!(params_block(&user).contains("hidden(name: 'JIUWENSWARM_MAX_TURNS', defaultValue: '500'"));
        opts.ui_profile = UiProfile::Developer;
        let names = visible_names("deepswe", JobShape::One, &opts);
        for name in [
            "JIUWENSWARM_MODE", "JIUWENSWARM_RELEASE_FILE", "JIUWENSWARM_GIT_URL", "JIUWENSWARM_GIT_REF",
            "JIUWENSWARM_GIT_REF_KIND", "JIUWENSWARM_AGENT_MODE", "JIUWENSWARM_MAX_TURNS",
        ] {
            assert!(names.contains(&name), "{name}");
        }
        let dev = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(params_block(&dev).contains(&format!("description: '{DEVELOPER_PREFIX}Choose git (clone URL + ref")));
        for bench in ["lolbench", "swebenchpro"] {
            let names = visible_names(bench, JobShape::One, &opts);
            assert!(!names.iter().any(|n| n.starts_with("JIUWENSWARM_")), "{bench}");
        }
        // The shard announces which harness and input it runs.
        assert!(dev.contains("HARNESS=$HARNESS ICODE_MODE=$ICODE_MODE${JIUWENSWARM_MODE:+ JIUWENSWARM_MODE=$JIUWENSWARM_MODE}"));
    }

    #[test]
    fn a_shard_archives_its_trials_for_the_dispatcher() {
        let opts = deepswe_opts(Vec::new());
        let jf = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(jf.contains("params.RUN_GROUP?.trim()"));
        // Only this build's trials, and the verdict summary the Aggregate merges.
        assert!(jf.contains(
            "\"eval-runs/harness/harbor_runs/jenkins-${env.BUILD_NUMBER}/**, eval-runs/harness/anticheat/**\""
        ));
        assert!(!jf.contains("'eval-runs/harness/harbor_runs/**'"));
        assert!(jf.contains("eval-runs/selected_tasks.txt"));
        // The whole suite, only when this shard was not given ids. The merge
        // names an offset slice that ran nothing from that list.
        assert!(jf.contains("if (!params.TASK?.trim() && !params.TASKS?.trim()) {"));
        assert!(jf.contains("inputs = 'eval-runs/suite_tasks.txt, ' + inputs"));
        assert!(jf.contains("currentBuild.description = \"shard ${params.SHARD}\""));
        // A direct build names whoever started it; a shard takes the dispatcher's user.
        let cause = jf.find("currentBuild.getBuildCauses('hudson.model.Cause$UserIdCause')").expect("cause");
        assert!(jf.contains("requester = params.REQUESTED_BY?.trim() ?: 'unknown'"));
        let set = jf.find("env.BUILD_USER = requester").expect("BUILD_USER");
        assert!(cause < set && set < jf.find("sh '''").unwrap());
        assert!(jf.contains("} catch (Throwable t) {\n              echo \"WARNING: could not read who started this build (${t})\""));
        // Copy Artifact refuses cross-job copies unless the source job allows them.
        assert!(jf.contains("copyArtifactPermission('deepswe_some_task,deepswe_full_suite_task,build_jiuwenswarm_headless')"));
        let xml = eval_job_xml("deepswe", JobShape::One, "d", &opts);
        assert!(xml.contains("CopyArtifactPermissionProperty"));
        assert!(xml.contains("<string>deepswe_full_suite_task</string>"));
        let some = eval_job_xml("deepswe", JobShape::Some_, "d", &opts);
        assert!(some.contains("CopyArtifactPermissionProperty"));
        assert!(some.contains("<string>deepswe_one_task</string>"));
    }

    #[test]
    fn the_dispatcher_tells_the_merge_which_shards_ran() {
        let opts = deepswe_opts(Vec::new());
        for shape in dispatchers("deepswe") {
            let jf = eval_jenkinsfile("deepswe", shape, &opts);
            // One line per question x rollout, the unit's final build, in plan order.
            assert!(jf.contains(
                "shardPlan[key] = \"${unit.index + 1}|${run.number}|${run.result}|${unit.offset}|1|${unit.tasks}|${unit.rollout}|${unit.attempt}\""
            ));
            assert!(jf.find("shardPlan[key] =").unwrap() < jf.find("parallel branches").unwrap());
            assert!(jf.contains("String line = shardPlan[\"${i}-${r}\"]"));
            assert!(jf.contains("if (line != null) { planLines << line }"));
            assert!(jf.contains("env.SHARD_PLAN = planLines.join(';')"));
            // Written after copyArtifacts, so a shard that archived nothing is still named.
            let write = jf
                .find("writeFile file: 'shards/plan.txt', text: (env.SHARD_PLAN ?: '').replace(';', '\\n') + '\\n'")
                .expect("plan.txt");
            assert!(jf.find("copyArtifacts").unwrap() < write);
            assert!(write < jf.find("aggregate_runs.py").unwrap());
            assert!(jf.contains(r#"--plan "${WORKSPACE}/shards/plan.txt""#), "{shape:?}");
            assert!(jf.contains(r#"--build-url "${BUILD_URL:-}""#), "{shape:?}");
            // The user who started the dispatcher reaches every shard and the merged report.
            let cause = jf.find("currentBuild.getBuildCauses('hudson.model.Cause$UserIdCause')").expect("cause");
            assert!(cause < jf.find("parallel branches").unwrap());
            assert!(jf.contains("string(name: 'REQUESTED_BY', value: env.BUILD_USER ?: '')"), "{shape:?}");
            assert!(jf.contains(r#"--requester-user "${BUILD_USER:-}""#), "{shape:?}");
        }
    }

    #[test]
    fn the_run_tar_gz_is_on_the_build_page() {
        let opts = deepswe_opts(Vec::new());
        // A one_task build archives the .tar.gz archive/backup names; a shard does not.
        let one = eval_jenkinsfile("deepswe", JobShape::One, &opts);
        assert!(one.contains(
            "if (!params.RUN_GROUP?.trim() && fileExists('eval-runs/last_backup.txt')) {"
        ));
        assert!(one.contains("def backup = readFile('eval-runs/last_backup.txt').trim()"));
        assert!(one.contains("archiveArtifacts artifacts: backup, allowEmptyArchive: true"));
        // A dispatcher packs the merged shards once and archives that.
        for shape in dispatchers("deepswe") {
            let jf = eval_jenkinsfile("deepswe", shape, &opts);
            let merge = jf.find("aggregate_runs.py").expect("aggregate step");
            let cost = jf.find("cost_token_report.py").expect("cost step");
            let pack = jf.find("archive_run.py").expect("pack step");
            assert!(merge < cost && cost < pack, "{shape:?}");
            assert!(jf.contains(r#"--task-file "${WORKSPACE}/aggregate/selected_tasks.txt""#));
            assert!(jf.contains(r#"--backup-root "${WORKSPACE}/backup""#));
            assert!(jf.contains(r#"--run-folder "$RUN_GROUP""#));
            assert!(jf.contains("archiveArtifacts artifacts: 'backup/**', allowEmptyArchive: true"));
            assert!(!jf.contains("last_backup.txt"), "{shape:?}");
        }
    }

    #[test]
    fn job_xml_wraps_pipeline_in_cdata() {
        let xml = job_config_xml(&sample_opts());
        assert!(xml.contains("<![CDATA["));
        assert!(xml.contains("flow-definition"));
        assert!(xml.contains("<name>TASK</name>"));
        assert!(xml.contains("<name>ICODE_MODE</name>"));
        assert!(xml.contains("<name>BENCHMARK</name>"));
        assert!(xml.contains("<string>lolbench</string>"));
        assert!(xml.contains("<defaultValue>ruff_1</defaultValue>"));
        assert!(xml.contains("<string>release</string>"));
        assert!(xml.contains("<name>ICODE_GIT_URL</name>"));
        assert!(xml.contains("<name>ICODE_GIT_REF</name>"));
        assert!(xml.contains("<name>ICODE_GIT_REF_KIND</name>"));
        assert!(xml.contains("<name>ICODE_RELEASE_FILE</name>"));
        assert!(xml.contains("<name>HARBOR_VERSION</name>"));
        assert!(xml.contains("<defaultValue>0.22.0</defaultValue>"));
        assert!(xml.contains("<name>DEEPSWE_REF</name>"));
        assert!(xml.contains(
            "<defaultValue>0b9fabbb63b9104d678fe965e1632f2dd9eaa2ea</defaultValue>"
        ));
        assert!(xml.contains("<name>LOLBENCH_REF</name>"));
        assert!(!xml.contains("<name>ICODE_EXPECT_SHA</name>"));
        assert!(!xml.contains("<name>OFFICIAL</name>"));
        assert!(!xml.contains("<name>CANARY</name>"));
        assert!(!xml.contains("<name>CANARY_ALLOW_HOST</name>"));
        assert!(xml.contains("StashedFileParameterDefinition"));
        assert!(
            !xml.contains("<name>ICODE_RELEASE</name>"),
            "Jenkins UI must not ask for a worker ICODE_RELEASE path"
        );
        assert!(xml.contains("<?xml version='1.0'"));
        assert!(xml.contains("leave this control as it is"));
        assert!(xml.contains("Vice versa"));
        assert!(xml.contains("<string>branch</string>"));
        assert!(xml.contains("<string>tag</string>"));
        assert!(xml.contains("<string>commit</string>"));
        assert!(xml.contains("<string>pr</string>"));
        assert!(
            !xml.contains("<string>auto</string>"),
            "Jenkins KIND list is branch/tag/commit/pr only"
        );
        assert!(
            xml.is_ascii(),
            "non-ASCII (en-dash, arrow) breaks Jenkins XML 1.1 POST"
        );
        assert!(!xml.contains("<name>ICODE_SOURCE</name>"));
        assert!(!xml.contains("<string>source</string>"));
        assert!(!xml.contains("<name>EVAL_MODE</name>"));
        assert!(!xml.contains("<name>LOLBENCH_PATH</name>"));
        assert!(xml.contains("pipeline {"));
        assert!(xml.contains("run_all.sh"));
    }

    #[test]
    fn job_opts_from_config_falls_back_to_old_binary_target() {
        let mut cfg = crate::config::MacK3dConfig::default();
        cfg.jenkins_job.default_icode_release.clear();
        cfg.jenkins_job.default_binary_target = "/tmp/icode".into();
        let opts = JobOpts::from_config(&cfg, Vec::new());
        assert_eq!(opts.default_icode_release, "/tmp/icode");
        assert!(opts.default_task.is_empty());
        assert_eq!(opts.default_eval_mode, "git");
        assert_eq!(opts.default_icode_git_ref, "2");
        assert_eq!(opts.default_icode_git_ref_kind, "pr");
        assert_eq!(opts.default_n_rollouts, 4);
        assert_eq!(opts.ui_profile, UiProfile::User);
        assert_eq!(opts.default_shard_size, 2);
        assert_eq!(
            opts.default_icode_git_url,
            "https://gitcode.com/michaelling/jiuwenicode"
        );
    }

    #[test]
    fn job_opts_from_config_reads_ui_profile() {
        let mut cfg = crate::config::MacK3dConfig::default();
        cfg.jenkins_job.ui_profile = "developer".into();
        cfg.jenkins_job.default_shard_size = 0;
        let opts = JobOpts::from_config(&cfg, Vec::new());
        assert_eq!(opts.ui_profile, UiProfile::Developer);
        assert_eq!(opts.default_shard_size, 1);
        assert_eq!(UiProfile::parse("anything else"), UiProfile::User);
    }

    #[test]
    fn a_saved_shard_size_of_ten_renders_as_two_on_every_dispatcher() {
        let mut cfg = crate::config::MacK3dConfig::default();
        cfg.jenkins_job.default_shard_size = 10;
        let opts = JobOpts::from_config(&cfg, Vec::new());
        assert_eq!(opts.default_shard_size, 2);
        assert_eq!(opts.max_in_flight, 0);
        assert_eq!(opts.oom_retries, 2);
        for bench in EVAL_BENCHMARKS {
            for shape in dispatchers(bench) {
                let jf = eval_jenkinsfile(bench, shape, &opts);
                let block = params_block(&jf);
                assert!(
                    block.contains("name: 'MAX_IN_FLIGHT'") && block.contains("defaultValue: '0'"),
                    "{bench} {shape:?}: {block}"
                );
                assert!(!block.contains("SHARD_SIZE"), "{bench} {shape:?}");
            }
            let one = eval_jenkinsfile(bench, JobShape::One, &opts);
            assert!(!params_block(&one).contains("SHARD_SIZE"), "{bench}");
        }
        // An intentional override other than the old default is kept.
        cfg.jenkins_job.default_shard_size = 3;
        assert_eq!(JobOpts::from_config(&cfg, Vec::new()).default_shard_size, 3);
    }

    #[test]
    fn job_opts_from_config_normalizes_eval_mode() {
        let mut cfg = crate::config::MacK3dConfig::default();
        cfg.jenkins_job.default_eval_mode = "SOURCE".into();
        cfg.jenkins_job.default_icode_git_url = "https://gitcode.com/example/icode.git".into();
        cfg.jenkins_job.default_icode_git_ref.clear();
        let opts = JobOpts::from_config(&cfg, Vec::new());
        assert_eq!(opts.default_eval_mode, "git");
        assert_eq!(opts.default_icode_git_ref, "2");
        assert_eq!(
            opts.default_icode_git_url,
            "https://gitcode.com/example/icode.git"
        );
        let jf = jenkinsfile(&opts);
        assert!(jf.contains("ICODE_MODE:-git"));
        assert!(!jf.contains("export ICODE_SOURCE="));
    }

    #[test]
    fn base64_roundtrip_ascii() {
        let s = "hello pipeline";
        let enc = base64_encode(s.as_bytes());
        assert_eq!(enc, "aGVsbG8gcGlwZWxpbmU=");
    }

    #[test]
    fn deepswe_one_task_xml_mentions_progress_and_scripts() {
        let xml = deepswe_one_task_job_xml(&deepswe_opts(vec!["deepseek-api-key".into()]));
        assert!(xml.contains("<![CDATA["));
        assert!(xml.contains("pipeline/stages/run_all.sh"));
        assert!(!xml.contains("Documents/Toby/mac-k3d"));
        assert!(xml.contains("MAC_K3D_PHASE=evaluate"));
        assert!(xml.contains("deepswe"));
        assert!(xml.contains("deepseek"));
        assert!(xml.contains("DEEPSEEK_MODEL"));
        assert!(xml.contains("deepseek-v4-pro"));
        assert!(xml.contains("withCredentials"));
        assert!(xml.contains("ParametersDefinitionProperty"));
        assert!(xml.contains("<name>N_TASKS</name>"));
        assert!(xml.contains("<name>N_ROLLOUTS</name>"));
        assert!(xml.contains("<defaultValue>1</defaultValue>"));
        assert!(xml.contains("<name>TASK</name>"));
        // The build extracts its own pipeline from the installed binary, so
        // there is no share-dir hint to run mac-k3d config first.
        assert!(!xml.contains("mac-k3d config -c worker.yaml"));
        assert!(!xml.contains("eval --stage p0"));
        assert!(xml.contains("Run scripts/redeploy.sh."));
        assert!(xml.contains("user-guide"));
        assert!(!xml.contains("harbor run"));
        assert!(
            !xml.contains("/home/Toby/Documents/Toby/iCode-main"),
            "job must not hardcode a lab iCode path"
        );
        assert!(!xml.contains("RESUME"));
        assert!(!xml.contains("BooleanParameterDefinition"));
    }

    #[test]
    fn deepswe_one_task_xml_omits_bind_when_credential_ids_empty() {
        let xml = deepswe_one_task_job_xml(&deepswe_opts(Vec::new()));
        assert!(
            !xml.contains("withCredentials"),
            "empty IDs must omit the bind"
        );
    }

    #[test]
    fn skip_secrets_must_pass_listed_ids_to_keep_bind() {
        // --skip-secrets / start list existing IDs then call this helper; [] would strip the bind.
        let xml = deepswe_one_task_job_xml(&deepswe_opts(vec!["deepseek-api-key".into()]));
        assert!(xml.contains("withCredentials"));
        assert!(xml.contains("deepseek-api-key"));
        assert!(xml.contains("DEEPSEEK_API_KEY"));
    }

    #[test]
    fn both_jobs_share_run_all_and_differ_by_benchmark() {
        let deepswe = deepswe_one_task_job_xml(&deepswe_opts(vec!["deepseek-api-key".into()]));
        let lolbench = job_config_xml(&sample_opts());
        let swebenchpro =
            swebenchpro_one_task_job_xml(&deepswe_opts(vec!["deepseek-api-key".into()]));
        assert!(deepswe.contains("<string>deepswe</string>"));
        assert!(lolbench.contains("<string>lolbench</string>"));
        assert!(swebenchpro.contains("<string>swebenchpro</string>"));
        assert!(deepswe.contains("run_all.sh"));
        assert!(lolbench.contains("run_all.sh"));
        assert!(swebenchpro.contains("run_all.sh"));
        assert!(lolbench.contains("Evaluate the iCode harness with an LLM call on one LoLBench task"));
        assert!(deepswe.contains(
            "Evaluate a harness (HARNESS: iCode or JiuwenSwarm) with an LLM call on one DeepSWE task"
        ));
        assert!(swebenchpro.contains(
            "Evaluate the iCode harness with an LLM call on one SWE-bench Pro task"
        ));
        assert!(lolbench.contains("One LoLBench question id"));
        assert!(deepswe.contains("One DeepSWE question id"));
        assert!(swebenchpro.contains("One SWE-bench Pro instance id"));
        assert!(!lolbench.contains("stays on Pier"));
        assert!(!deepswe.contains("stays on Pier"));
        assert!(!swebenchpro.contains("stays on Pier"));
        assert!(!deepswe.contains("harbor run"));
        assert!(!lolbench.contains("harbor run"));
        assert!(deepswe.contains("StashedFileParameterDefinition"));
        assert!(lolbench.contains("StashedFileParameterDefinition"));
        assert!(swebenchpro.contains("StashedFileParameterDefinition"));
        assert!(!deepswe.contains("<name>ICODE_RELEASE</name>"));
        assert!(!lolbench.contains("<name>ICODE_RELEASE</name>"));
        assert!(!swebenchpro.contains("<name>ICODE_RELEASE</name>"));
    }

    #[test]
    fn question_defaults_apply_to_all_jobs() {
        let mut opts = sample_opts();
        opts.default_benchmark = "deepswe".into();
        opts.default_task = "abs-stepped-slices".into();
        opts.default_n_tasks = 2;
        opts.default_tasks.clear();
        let deepswe = deepswe_one_task_job_xml(&opts);
        let lolbench = job_config_xml(&opts);
        let swebenchpro = swebenchpro_one_task_job_xml(&opts);
        assert!(deepswe.contains("<defaultValue>abs-stepped-slices</defaultValue>"));
        assert!(lolbench.contains("<defaultValue>abs-stepped-slices</defaultValue>"));
        assert!(swebenchpro.contains("<defaultValue>abs-stepped-slices</defaultValue>"));
        assert!(deepswe.contains("<name>N_ROLLOUTS</name>"));
        // one_task is the smoke shape: one question, one rollout, whatever the
        // config's N_TASKS says. some_task is where the configured count lands.
        let some = eval_job_xml("deepswe", JobShape::Some_, "d", &opts);
        assert!(some.contains("<name>N_TASKS</name>\n          <description>Used only when TASKS is empty"));
        assert!(some.contains("<defaultValue>2</defaultValue>"));
        assert!(some.contains("Full suite is 113"));
        let some_pro = eval_job_xml("swebenchpro", JobShape::Some_, "d", &opts);
        assert!(some_pro.contains("Full suite is 731"));
        let (task, n, tasks) = question_defaults_for_job(&opts, "swebenchpro");
        assert_eq!(task, "abs-stepped-slices");
        assert_eq!(n, 2);
        assert!(tasks.is_empty());
        assert!(swebenchpro.contains("<name>N_ROLLOUTS</name>"));
        assert!(lolbench.contains("<name>N_ROLLOUTS</name>"));
        assert!(lolbench.contains("<name>HARNESS</name>"));
        assert!(lolbench.contains("<string>icode</string>"));
        assert!(lolbench.contains("<name>LLM</name>"));
        assert!(lolbench.contains("<string>deepseek</string>"));
    }

    #[test]
    fn tasks_list_becomes_tasks_param() {
        let mut opts = deepswe_opts(Vec::new());
        opts.default_task.clear();
        opts.default_tasks = vec!["abs-module-cache-flags".into(), "abs-stepped-slices".into()];
        // some_task takes the list; one_task takes its first id.
        let some = eval_job_xml("deepswe", JobShape::Some_, "d", &opts);
        assert!(some.contains("<name>TASKS</name>"));
        assert!(
            some.contains("<defaultValue>abs-module-cache-flags,abs-stepped-slices</defaultValue>")
        );
        let one = deepswe_one_task_job_xml(&opts);
        assert!(one.contains("<name>TASK</name>\n          <description>One DeepSWE question id"));
        assert!(one.contains("<defaultValue>abs-module-cache-flags</defaultValue>"));
        assert!(one.contains("export TASKS="));
        let (task, n, tasks) = question_defaults_for_job(&opts, "deepswe");
        assert!(task.is_empty());
        assert_eq!(n, 2);
        assert_eq!(tasks, "abs-module-cache-flags,abs-stepped-slices");
    }

    #[test]
    fn job_xml_file_param_does_not_bake_worker_release_path() {
        let mut opts = sample_opts();
        opts.default_icode_release = "/tmp/lab-icode-full-drop".into();
        let xml = job_config_xml(&opts);
        assert!(xml.contains("<name>ICODE_RELEASE_FILE</name>"));
        assert!(xml.contains("StashedFileParameterDefinition"));
        assert!(!xml.contains("/tmp/lab-icode-full-drop"));
        assert!(!xml.contains("file(name:"));
        assert!(xml.contains("stashedFile(name: 'ICODE_RELEASE_FILE'"));
        assert!(xml.contains("'ICODE_RELEASE_FILE'"));
        assert!(xml.contains("unstash upload"));
    }

    #[test]
    fn deepseek_model_is_catalog_choice_with_both_ids() {
        let xml = deepswe_one_task_job_xml(&deepswe_opts(Vec::new()));
        assert!(xml.contains("<name>DEEPSEEK_MODEL</name>"));
        assert!(xml.contains("<string>deepseek-v4-pro</string>"));
        assert!(xml.contains("<string>deepseek-flash</string>"));
        assert!(xml.contains("ChoiceParameterDefinition"));
        assert!(!xml.contains("<defaultValue>deepseek-flash</defaultValue>"));
        assert!(!xml.contains("<defaultValue>deepseek-v4-pro</defaultValue>"));

        let jf = jenkinsfile(&sample_opts());
        assert!(
            jf.contains(
                "choice(name: 'DEEPSEEK_MODEL', choices: ['deepseek-flash', 'deepseek-v4-pro']"
            ),
            "{jf}"
        );
        assert!(jf.contains("export DEEPSEEK_MODEL=\"${DEEPSEEK_MODEL:-deepseek-flash}\""));

        let mut opts = sample_opts();
        opts.default_deepseek_model = "deepseek-v4-pro".into();
        let jf = jenkinsfile(&opts);
        assert!(
            jf.contains(
                "choice(name: 'DEEPSEEK_MODEL', choices: ['deepseek-v4-pro', 'deepseek-flash']"
            ),
            "a controller that pins v4-pro still lists it first: {jf}"
        );
    }

    #[test]
    fn job_opts_empty_deepseek_model_uses_catalog_default() {
        let cfg = crate::config::MacK3dConfig::default();
        let opts = JobOpts::from_config(&cfg, Vec::new());
        assert_eq!(opts.default_deepseek_model, eval_catalog::default_model());
    }

    #[test]
    fn dump_swebenchpro_job_xml_when_env_set() {
        let Ok(path) = std::env::var("DUMP_SWEBENCHPRO_JOB_XML") else {
            return;
        };
        if path.trim().is_empty() {
            return;
        }
        let mut opts = sample_opts();
        opts.default_icode_git_url = String::new();
        opts.default_benchmark = "swebenchpro".into();
        opts.default_task = String::new();
        opts.default_n_tasks = 1;
        opts.default_deepseek_model = "deepseek-flash".into();
        std::fs::write(&path, swebenchpro_one_task_job_xml(&opts)).expect("write xml");
        if let Ok(dir) = std::env::var("DUMP_ALL_ONE_TASK_XML_DIR") {
            if !dir.trim().is_empty() {
                let _ = std::fs::create_dir_all(&dir);
                let mut lol = sample_opts();
                lol.default_icode_git_url = String::new();
                lol.default_deepseek_model = "deepseek-flash".into();
                std::fs::write(
                    format!("{dir}/lolbench_one_task.xml"),
                    job_config_xml(&lol),
                )
                .expect("write lolbench xml");
                let mut ds = deepswe_opts(vec![
                    "deepseek-api-key".into(),
                    "gitcode-pat".into(),
                ]);
                ds.default_deepseek_model = "deepseek-flash".into();
                std::fs::write(
                    format!("{dir}/deepswe_one_task.xml"),
                    deepswe_one_task_job_xml(&ds),
                )
                .expect("write deepswe xml");
            }
        }
    }
}
