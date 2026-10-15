//! `mac-k3d eval --job one|some|full`: queue one of the Jenkins eval jobs with
//! the fields of its Build with Parameters page. Only the fields given on the
//! command line are sent; every other field keeps the job's default, which is
//! what pressing Build with the form untouched does. `--harness` sets HARNESS
//! on any of the three jobs.

use std::time::Duration;

use super::jenkins_api::{self, JenkinsAccess};
use super::EvalArgs;
use crate::config::MacK3dConfig;
use crate::error::{Error, Result};
use crate::eval_catalog;
use crate::prepare::jenkins_job::{self, JobParam, JobShape};

/// What the caller asked for: flags only, no environment or config defaults.
#[derive(Debug, Default, Clone)]
pub(super) struct QueueRequest {
    pub benchmark: String,
    pub shape: JobShape,
    pub task: Option<String>,
    pub tasks: Option<String>,
    pub n_tasks: Option<u32>,
    pub n_rollouts: Option<u32>,
    pub icode_mode: Option<String>,
    pub icode_git_url: Option<String>,
    pub icode_git_ref: Option<String>,
    pub icode_git_ref_kind: Option<String>,
    /// `--harness`, checked against the benchmark.
    pub harness: Option<String>,
    pub jiuwenswarm_git_url: Option<String>,
    pub jiuwenswarm_git_ref: Option<String>,
    pub jiuwenswarm_git_ref_kind: Option<String>,
    pub model: Option<String>,
    /// `--param NAME=VALUE`, in the order given.
    pub params: Vec<String>,
}

impl QueueRequest {
    pub(super) fn from_args(args: &EvalArgs, shape: JobShape) -> Result<Self> {
        let benchmark = eval_catalog::require_benchmark(args.benchmark.as_deref().unwrap_or("deepswe"))?;
        let harness = args
            .harness
            .as_deref()
            .map(|h| eval_catalog::require_harness_for(h, &benchmark))
            .transpose()?;
        Ok(Self {
            benchmark,
            shape,
            task: args.task.clone(),
            tasks: args.tasks.clone(),
            n_tasks: args.n_tasks,
            n_rollouts: args.n_rollouts,
            icode_mode: args.icode_mode.clone(),
            icode_git_url: args.icode_git_url.clone(),
            icode_git_ref: args.icode_git_ref.clone(),
            icode_git_ref_kind: args.icode_git_ref_kind.clone(),
            harness,
            jiuwenswarm_git_url: args.jiuwenswarm_git_url.clone(),
            jiuwenswarm_git_ref: args.jiuwenswarm_git_ref.clone(),
            jiuwenswarm_git_ref_kind: args.jiuwenswarm_git_ref_kind.clone(),
            model: args.model.clone(),
            params: args.param.clone(),
        })
    }

    fn jiuwenswarm(&self) -> bool {
        self.harness.as_deref() == Some("jiuwenswarm")
    }

    fn job(&self) -> String {
        jenkins_job::eval_job_name(&self.benchmark, self.shape)
    }
}

/// `--job` value to a shape, with the accepted spellings in the error.
pub(super) fn parse_shape(job: &str) -> Result<JobShape> {
    JobShape::from_cli(job).ok_or_else(|| {
        Error::Config(format!(
            "--job {job}: use one (<benchmark>_one_task), some (_some_task) or full (_full_suite_task)"
        ))
    })
}

/// Fields with a dedicated flag; `--param` refuses them so each value is checked once.
const FLAG_FIELDS: &[(&str, &str)] = &[
    ("HARNESS", "--harness"),
    ("TASK", "--task"),
    ("TASKS", "--tasks"),
    ("N_TASKS", "--n-tasks"),
    ("N_ROLLOUTS", "--n-rollouts"),
    ("ICODE_MODE", "--icode-mode"),
    ("ICODE_GIT_URL", "--icode-git-url"),
    ("ICODE_GIT_REF", "--icode-git-ref"),
    ("ICODE_GIT_REF_KIND", "--icode-git-ref-kind"),
    ("JIUWENSWARM_GIT_URL", "--jiuwenswarm-git-url"),
    ("JIUWENSWARM_GIT_REF", "--jiuwenswarm-git-ref"),
    ("JIUWENSWARM_GIT_REF_KIND", "--jiuwenswarm-git-ref-kind"),
    ("DEEPSEEK_MODEL", "--model"),
];

/// The file uploads of a job page, which only Build with Parameters can attach.
const UPLOAD_FIELDS: &[&str] = &["ICODE_RELEASE_FILE", "JIUWENSWARM_RELEASE_FILE"];

/// A harness's git URL, ref and ref kind: field names, then their flags.
type GitFields = ([&'static str; 3], [&'static str; 3]);
const ICODE_GIT: GitFields = (
    ["ICODE_GIT_URL", "ICODE_GIT_REF", "ICODE_GIT_REF_KIND"],
    ["--icode-git-url", "--icode-git-ref", "--icode-git-ref-kind"],
);
const JIUWENSWARM_GIT: GitFields = (
    ["JIUWENSWARM_GIT_URL", "JIUWENSWARM_GIT_REF", "JIUWENSWARM_GIT_REF_KIND"],
    ["--jiuwenswarm-git-url", "--jiuwenswarm-git-ref", "--jiuwenswarm-git-ref-kind"],
);

/// The git fields from their flags, checked as the pipeline checks them.
fn git_fields(
    (names, flags): GitFields,
    url: Option<String>,
    git_ref: Option<String>,
    kind: Option<String>,
) -> Result<Vec<(&'static str, String, &'static str)>> {
    let mut set = Vec::new();
    if let Some(url) = url {
        set.push((names[0], eval_catalog::require_git_url(names[0], &url)?, flags[0]));
    }
    match (git_ref, kind) {
        (Some(git_ref), Some(kind)) => {
            let (kind, git_ref) = eval_catalog::require_git_ref_for_kind(names[1], &kind, &git_ref)?;
            set.push((names[1], git_ref, flags[1]));
            set.push((names[2], kind, flags[2]));
        }
        (Some(git_ref), None) => {
            set.push((names[1], eval_catalog::require_git_ref(names[1], &git_ref)?, flags[1]));
        }
        (None, Some(kind)) => {
            let kind = kind.to_ascii_lowercase();
            if !eval_catalog::ICODE_GIT_REF_KINDS.contains(&kind.as_str()) {
                return Err(Error::Config(format!(
                    "{} {kind}: use one of {}",
                    flags[2],
                    eval_catalog::join_allowed(eval_catalog::ICODE_GIT_REF_KINDS)
                )));
            }
            set.push((names[2], kind, flags[2]));
        }
        (None, None) => {}
    }
    Ok(set)
}

fn at_least_one(flag: &str, n: u32) -> Result<String> {
    if n < 1 {
        return Err(Error::Config(format!("{flag} must be at least 1")));
    }
    Ok(n.to_string())
}

/// The fields to send, in the job page's order. Refuses a flag the job has no
/// field for, rather than letting Jenkins ignore it.
fn request_fields(req: &QueueRequest) -> Result<Vec<(&'static str, String)>> {
    let job = req.job();
    let page = jenkins_job::job_params(&req.benchmark, req.shape);
    let mut set: Vec<(&'static str, String, &'static str)> = Vec::new();
    let nonblank = |s: &Option<String>| s.as_deref().map(str::trim).filter(|s| !s.is_empty()).map(str::to_string);

    // Each harness takes its own inputs.
    let icode_flags = [
        (&req.icode_mode, "--icode-mode"),
        (&req.icode_git_url, "--icode-git-url"),
        (&req.icode_git_ref, "--icode-git-ref"),
        (&req.icode_git_ref_kind, "--icode-git-ref-kind"),
    ];
    let jiuwen_flags = [
        (&req.jiuwenswarm_git_url, "--jiuwenswarm-git-url"),
        (&req.jiuwenswarm_git_ref, "--jiuwenswarm-git-ref"),
        (&req.jiuwenswarm_git_ref_kind, "--jiuwenswarm-git-ref-kind"),
    ];
    if req.jiuwenswarm() {
        if let Some((_, flag)) = icode_flags.iter().find(|(v, _)| nonblank(v).is_some()) {
            return Err(Error::Config(format!(
                "{flag} is an iCode input; JiuwenSwarm takes --jiuwenswarm-git-url, --jiuwenswarm-git-ref \
                 and --jiuwenswarm-git-ref-kind"
            )));
        }
    } else if let Some((_, flag)) = jiuwen_flags.iter().find(|(v, _)| nonblank(v).is_some()) {
        return Err(Error::Config(format!("{flag} needs --harness jiuwenswarm")));
    }

    if let Some(harness) = &req.harness {
        set.push(("HARNESS", harness.clone(), "--harness"));
    }
    match req.shape {
        JobShape::One => {
            if nonblank(&req.tasks).is_some() {
                return Err(Error::Config(format!(
                    "{job} runs one question: use --task ID, or --job some --tasks a,b,c"
                )));
            }
            if req.n_tasks.is_some_and(|n| n > 1) {
                return Err(Error::Config(format!(
                    "{job} runs one question: use --job some --n-tasks N for several"
                )));
            }
            if let Some(task) = nonblank(&req.task) {
                set.push(("TASK", task, "--task"));
            }
        }
        JobShape::Some_ | JobShape::FullSuite => {
            if nonblank(&req.task).is_some() {
                return Err(Error::Config(format!(
                    "{job} takes a list: use --tasks a,b,c (--task is for --job one)"
                )));
            }
            if let Some(raw) = nonblank(&req.tasks) {
                if req.shape == JobShape::FullSuite {
                    return Err(Error::Config(format!(
                        "{job} runs the whole suite: use --job some --tasks a,b,c for a list"
                    )));
                }
                let ids = eval_catalog::parse_task_list(&raw);
                if ids.is_empty() {
                    return Err(Error::Config("--tasks has no question ids".into()));
                }
                set.push(("TASKS", ids.join(","), "--tasks"));
            }
            if let Some(n) = req.n_tasks {
                set.push(("N_TASKS", at_least_one("--n-tasks", n)?, "--n-tasks"));
            }
        }
    }
    if let Some(mode) = nonblank(&req.icode_mode) {
        if eval_catalog::normalize_icode_mode(&mode)? == "release" {
            return Err(Error::Config(format!(
                "{job} with ICODE_MODE=release needs an uploaded ICODE_RELEASE_FILE, which the CLI \
                 cannot attach. Open the job's Build with Parameters page, or use --icode-mode git"
            )));
        }
        set.push(("ICODE_MODE", "git".into(), "--icode-mode"));
    }
    if let Some(n) = req.n_rollouts {
        if req.shape == JobShape::One && n != 1 {
            return Err(Error::Config(format!(
                "{job} runs one rollout. For several, use --job some --tasks ID --n-rollouts {n}"
            )));
        }
        if req.shape != JobShape::One {
            set.push(("N_ROLLOUTS", at_least_one("--n-rollouts", n)?, "--n-rollouts"));
        }
    }
    set.extend(git_fields(
        ICODE_GIT,
        nonblank(&req.icode_git_url),
        nonblank(&req.icode_git_ref),
        nonblank(&req.icode_git_ref_kind),
    )?);
    set.extend(git_fields(
        JIUWENSWARM_GIT,
        nonblank(&req.jiuwenswarm_git_url),
        nonblank(&req.jiuwenswarm_git_ref),
        nonblank(&req.jiuwenswarm_git_ref_kind),
    )?);
    if let Some(model) = nonblank(&req.model) {
        set.push(("DEEPSEEK_MODEL", eval_catalog::require_model(&model)?, "--model"));
    }

    for raw in &req.params {
        let (name, value) = raw
            .split_once('=')
            .ok_or_else(|| Error::Config(format!("--param {raw}: write it as NAME=VALUE")))?;
        let name = name.trim().to_ascii_uppercase();
        let field = page_field(&page, &name).ok_or_else(|| {
            let names: Vec<&str> = page.iter().filter(|p| p.settable).map(|p| p.name).collect();
            Error::Config(format!("{job} has no field {name}. Its fields: {}", names.join(", ")))
        })?;
        if !field.settable {
            return Err(Error::Config(format!(
                "{name} is set by the dispatcher on each shard build; it cannot be set by hand"
            )));
        }
        if UPLOAD_FIELDS.contains(&name.as_str()) {
            return Err(Error::Config(format!(
                "{name} is a file upload: use the job's Build with Parameters page"
            )));
        }
        if let Some((_, flag)) = FLAG_FIELDS.iter().find(|(n, _)| *n == name) {
            return Err(Error::Config(format!("{name} has its own flag, which checks the value: use {flag}")));
        }
        if set.iter().any(|(n, _, _)| *n == field.name) {
            return Err(Error::Config(format!("--param {name} is given twice")));
        }
        set.push((field.name, param_value(&job, &name, value.trim())?, "--param"));
    }

    for (name, _, flag) in &set {
        if page_field(&page, name).is_none() {
            return Err(Error::Config(format!("{flag}: {job} has no {name} field")));
        }
    }
    let order = |name: &str| page.iter().position(|p| p.name == name).unwrap_or(usize::MAX);
    set.sort_by_key(|(name, _, _)| order(name));
    Ok(set.into_iter().map(|(name, value, _)| (name, value)).collect())
}

/// A `--param` value, checked here when the pipeline would refuse it later.
fn param_value(job: &str, name: &str, value: &str) -> Result<String> {
    match name {
        "JIUWENSWARM_MODE" => match value.to_ascii_lowercase().as_str() {
            "git" => Ok("git".into()),
            "release" => Err(Error::Config(format!(
                "{job} with JIUWENSWARM_MODE=release needs an uploaded JIUWENSWARM_RELEASE_FILE, which the CLI \
                 cannot attach. Open the job's Build with Parameters page"
            ))),
            other => Err(Error::Config(format!("JIUWENSWARM_MODE={other}: use git or release"))),
        },
        "JIUWENSWARM_AGENT_MODE" => eval_catalog::require_jiuwenswarm_agent_mode(value),
        "JIUWENSWARM_MAX_TURNS" => {
            let turns = value
                .parse::<u32>()
                .map_err(|_| Error::Config(format!("JIUWENSWARM_MAX_TURNS={value}: write a whole number")))?;
            Ok(eval_catalog::require_jiuwenswarm_max_turns(turns)?.to_string())
        }
        _ => Ok(value.to_string()),
    }
}

fn page_field<'a>(page: &'a [JobParam], name: &str) -> Option<&'a JobParam> {
    page.iter().find(|p| p.name == name)
}

/// `?A=1&B=2`, fully encoded; empty when every field keeps its default.
fn query(fields: &[(&str, String)]) -> String {
    if fields.is_empty() {
        return String::new();
    }
    let pairs: Vec<String> = fields
        .iter()
        .map(|(n, v)| format!("{n}={}", jenkins_api::query_value(v)))
        .collect();
    format!("?{}", pairs.join("&"))
}

fn plan_text(job: &str, fields: &[(&str, String)]) -> String {
    if fields.is_empty() {
        return format!("Queue {job} with every field at the job's default.");
    }
    let lines: Vec<String> = fields.iter().map(|(n, v)| format!("  {n}={v}")).collect();
    format!(
        "Queue {job} with:\n{}\nEvery other field keeps the job's default, as pressing Build does.",
        lines.join("\n")
    )
}

/// Every name to send must exist on the live job; an older controller job
/// would otherwise ignore it without a word.
fn check_live_fields(access: &JenkinsAccess, job: &str, fields: &[(&str, String)]) -> Result<()> {
    let url = format!(
        "{}/job/{}/api/json?tree=property[parameterDefinitions[name]]",
        access.base,
        jenkins_api::url_path(job)
    );
    let info = jenkins_api::get_json(access, &url)?;
    let live: Vec<String> = info["property"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|p| p["parameterDefinitions"].as_array().into_iter().flatten())
        .filter_map(|d| d["name"].as_str().map(str::to_string))
        .collect();
    let missing: Vec<&str> = fields
        .iter()
        .map(|(n, _)| *n)
        .filter(|n| !live.iter().any(|l| l == n))
        .collect();
    if !missing.is_empty() {
        return Err(Error::Config(format!(
            "{job} on {} has no {} field: its jobs are older than this mac-k3d. \
             Redeploy the controller (scripts/redeploy.sh --controller …) and try again",
            access.base,
            missing.join(", ")
        )));
    }
    Ok(())
}

pub(super) fn queue(config: &MacK3dConfig, req: &QueueRequest, dry_run: bool) -> Result<()> {
    let job = req.job();
    let fields = request_fields(req)?;
    println!("{}", plan_text(&job, &fields));
    if dry_run {
        println!("Dry run: nothing queued.");
        return Ok(());
    }
    if let Some((name, url)) = fields.iter().find(|(n, _)| matches!(*n, "ICODE_GIT_URL" | "JIUWENSWARM_GIT_URL")) {
        if let Ok(spec) = eval_catalog::git_pat_spec(url) {
            println!(
                "The git clone uses controller credential {} ({}); for a private repo add it with \
                 `mac-k3d config --update-secrets` on the controller, never in {name}.",
                spec.jenkins_id, spec.env_var
            );
        }
    }
    let access = jenkins_api::jenkins_access(config)?;
    println!("Controller {} (API user from {})", access.base, access.source);
    check_live_fields(&access, &job, &fields)?;

    let url = format!(
        "{}/job/{}/buildWithParameters{}",
        access.base,
        jenkins_api::url_path(&job),
        query(&fields)
    );
    let reply = jenkins_api::post_with_crumb(&access, &url)?;
    if !(200..400).contains(&reply.code) {
        let hint = match reply.code {
            401 | 403 => " (Jenkins rejected the API user/token)",
            404 => " (no such job on this controller; run `mac-k3d config` on the controller)",
            _ => "",
        };
        return Err(Error::Config(format!("Jenkins refused to queue {job}: HTTP {}{hint}", reply.code)));
    }
    let job_page = format!("{}/job/{}/", access.base, jenkins_api::url_path(&job));
    let Some(location) = reply.location else {
        println!("Queued {job}. Watch {job_page}");
        return Ok(());
    };
    follow_queue(&access, &job, &job_page, &jenkins_api::public_url(&access.base, &location))
}

/// Wait a little for the queue item to become a build, so the build URL can be printed.
fn follow_queue(access: &JenkinsAccess, job: &str, job_page: &str, queue_url: &str) -> Result<()> {
    let api = format!(
        "{}/api/json?tree=cancelled,why,executable[number]",
        queue_url.trim_end_matches('/')
    );
    let mut why = String::new();
    for attempt in 0..15 {
        if attempt > 0 {
            std::thread::sleep(Duration::from_secs(2));
        }
        let Ok(item) = jenkins_api::get_json(access, &api) else {
            continue;
        };
        if let Some(n) = item["executable"]["number"].as_u64() {
            println!("Started {job} #{n}: {job_page}{n}/");
            println!("When it finishes: mac-k3d eval record --job {job} --build {n}");
            return Ok(());
        }
        if item["cancelled"].as_bool() == Some(true) {
            return Err(Error::Config(format!("{job}: the queue item was cancelled ({queue_url})")));
        }
        why = item["why"].as_str().unwrap_or("").to_string();
    }
    let why = if why.is_empty() { String::new() } else { format!(" ({why})") };
    println!("Queued {job}, still waiting to start{why}. Queue item: {queue_url}\nWatch {job_page}");
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn some(tasks: &str) -> QueueRequest {
        QueueRequest {
            benchmark: "deepswe".into(),
            shape: JobShape::Some_,
            tasks: Some(tasks.into()),
            ..Default::default()
        }
    }

    fn names(fields: &[(&str, String)]) -> Vec<String> {
        fields.iter().map(|(n, v)| format!("{n}={v}")).collect()
    }

    #[test]
    fn only_the_given_fields_are_sent_in_page_order() {
        let mut req = some("wazero-multi-module-snapshots, ytt-jsonpath-query-api");
        req.n_rollouts = Some(1);
        req.model = Some("deepseek-flash".into());
        req.params = vec!["agent_label=lolbench".into(), "MAX_IN_FLIGHT=20".into()];
        assert_eq!(
            names(&request_fields(&req).unwrap()),
            [
                "TASKS=wazero-multi-module-snapshots,ytt-jsonpath-query-api",
                "N_ROLLOUTS=1",
                "DEEPSEEK_MODEL=deepseek-flash",
                "MAX_IN_FLIGHT=20",
                "AGENT_LABEL=lolbench",
            ]
        );
        let bare = QueueRequest {
            benchmark: "deepswe".into(),
            shape: JobShape::FullSuite,
            ..Default::default()
        };
        assert!(request_fields(&bare).unwrap().is_empty());
        assert_eq!(plan_text("deepswe_full_suite_task", &[]), "Queue deepswe_full_suite_task with every field at the job's default.");
    }

    #[test]
    fn git_fields_are_checked_like_the_local_run() {
        let mut req = some("a");
        req.icode_git_url = Some("https://gitcode.com/michaelling/jiuwenicode".into());
        req.icode_git_ref = Some("2".into());
        req.icode_git_ref_kind = Some("pr".into());
        assert_eq!(
            names(&request_fields(&req).unwrap())[1..],
            [
                "ICODE_GIT_URL=https://gitcode.com/michaelling/jiuwenicode",
                "ICODE_GIT_REF=2",
                "ICODE_GIT_REF_KIND=pr",
            ]
        );
        req.icode_git_ref = Some("main".into());
        assert!(request_fields(&req).unwrap_err().to_string().contains("pull-request number"));
        req.icode_git_ref = None;
        req.icode_git_ref_kind = Some("auto".into());
        assert!(request_fields(&req).is_err());
        req.icode_git_ref_kind = None;
        req.icode_git_url = Some("https://evil.example/x".into());
        assert!(request_fields(&req).is_err());
    }

    #[test]
    fn each_job_refuses_flags_it_has_no_field_for() {
        let one = |f: fn(&mut QueueRequest)| {
            let mut req = QueueRequest {
                benchmark: "deepswe".into(),
                shape: JobShape::One,
                ..Default::default()
            };
            f(&mut req);
            request_fields(&req).map_err(|e| e.to_string())
        };
        assert!(one(|r| r.tasks = Some("a,b".into())).unwrap_err().contains("--job some --tasks"));
        assert!(one(|r| r.n_tasks = Some(3)).unwrap_err().contains("--job some --n-tasks"));
        assert!(one(|r| r.n_rollouts = Some(4)).unwrap_err().contains("runs one rollout"));
        assert!(one(|r| r.icode_mode = Some("release".into())).unwrap_err().contains("ICODE_RELEASE_FILE"));
        assert_eq!(
            one(|r| {
                r.task = Some("ipython-session-bundle-replay".into());
                r.n_tasks = Some(1);
                r.icode_mode = Some("git".into());
            })
            .unwrap(),
            vec![
                ("TASK", "ipython-session-bundle-replay".to_string()),
                ("ICODE_MODE", "git".to_string()),
            ]
        );

        let mut req = some("a");
        req.task = Some("x".into());
        assert!(request_fields(&req).unwrap_err().to_string().contains("--tasks a,b,c"));
        let mut req = some("a");
        req.icode_mode = Some("release".into());
        assert!(request_fields(&req).unwrap_err().to_string().contains("ICODE_RELEASE_FILE"));
        let mut req = some("a");
        req.shape = JobShape::FullSuite;
        assert!(request_fields(&req).unwrap_err().to_string().contains("whole suite"));
        let mut req = some(" , ");
        req.n_rollouts = Some(0);
        assert!(request_fields(&req).is_err());
    }

    #[test]
    fn param_names_are_checked_against_the_job_page() {
        let err = |params: &[&str], shape: JobShape| {
            let req = QueueRequest {
                benchmark: "deepswe".into(),
                shape,
                params: params.iter().map(|s| s.to_string()).collect(),
                ..Default::default()
            };
            request_fields(&req).unwrap_err().to_string()
        };
        assert!(err(&["NO_SUCH=1"], JobShape::Some_).contains("has no field NO_SUCH. Its fields: HARNESS"));
        assert!(err(&["CANARY"], JobShape::Some_).contains("NAME=VALUE"));
        for plumbing in ["TASK_OFFSET=3", "RUN_GROUP=x", "SHARD=1/2", "TASKS=a"] {
            assert!(err(&[plumbing], JobShape::One).contains("cannot be set by hand"), "{plumbing}");
        }
        assert!(err(&["TASKS=b"], JobShape::Some_).contains("use --tasks"));
        assert!(err(&["DEEPSEEK_MODEL=gpt-4"], JobShape::FullSuite).contains("use --model"));
        assert!(err(&["AGENT_LABEL=a", "agent_label=b"], JobShape::Some_).contains("given twice"));
        assert!(err(&["OFFICIAL=1"], JobShape::Some_).contains("has no field OFFICIAL"));
        assert!(err(&["CANARY=on"], JobShape::Some_).contains("has no field CANARY"));
        assert!(err(&["ICODE_RELEASE_FILE=x"], JobShape::One).contains("file upload"));
        assert!(err(&["SHARD_SIZE=2"], JobShape::One).contains("has no field SHARD_SIZE"));
        let req = QueueRequest {
            benchmark: "lolbench".into(),
            shape: JobShape::One,
            params: vec!["agent_label=mac-Michael-Ubuntu".into()],
            ..Default::default()
        };
        assert_eq!(
            names(&request_fields(&req).unwrap()),
            ["AGENT_LABEL=mac-Michael-Ubuntu"]
        );
    }

    fn jiuwen(shape: JobShape, f: fn(&mut QueueRequest)) -> QueueRequest {
        let mut req = QueueRequest {
            benchmark: "deepswe".into(),
            shape,
            harness: Some("jiuwenswarm".into()),
            ..Default::default()
        };
        f(&mut req);
        req
    }

    #[test]
    fn harness_picks_the_harness_on_every_deepswe_job() {
        let one = jiuwen(JobShape::One, |r| r.task = Some("wazero-multi-module-snapshots".into()));
        assert_eq!(one.job(), "deepswe_one_task");
        assert_eq!(
            names(&request_fields(&one).unwrap()),
            ["HARNESS=jiuwenswarm", "TASK=wazero-multi-module-snapshots"]
        );
        let list = jiuwen(JobShape::Some_, |r| {
            r.tasks = Some("a, b".into());
            r.n_rollouts = Some(1);
            r.jiuwenswarm_git_ref = Some("main".into());
            r.jiuwenswarm_git_ref_kind = Some("branch".into());
            r.params = vec!["jiuwenswarm_agent_mode=Agent.Code.Plan".into(), "JIUWENSWARM_MAX_TURNS=40".into()];
        });
        assert_eq!(list.job(), "deepswe_some_task");
        assert_eq!(
            names(&request_fields(&list).unwrap()),
            [
                "HARNESS=jiuwenswarm",
                "TASKS=a,b",
                "N_ROLLOUTS=1",
                "JIUWENSWARM_GIT_REF=main",
                "JIUWENSWARM_GIT_REF_KIND=branch",
                "JIUWENSWARM_AGENT_MODE=agent.code.plan",
                "JIUWENSWARM_MAX_TURNS=40",
            ]
        );
        let full = jiuwen(JobShape::FullSuite, |_| {});
        assert_eq!(full.job(), "deepswe_full_suite_task");
        assert_eq!(names(&request_fields(&full).unwrap()), ["HARNESS=jiuwenswarm"]);
        // some_task has its own N_TASKS default, so no list is needed.
        let bare = jiuwen(JobShape::Some_, |_| {});
        assert_eq!(names(&request_fields(&bare).unwrap()), ["HARNESS=jiuwenswarm"]);
        let few = jiuwen(JobShape::Some_, |r| r.n_tasks = Some(3));
        assert_eq!(names(&request_fields(&few).unwrap()), ["HARNESS=jiuwenswarm", "N_TASKS=3"]);
        let mut icode = some("a");
        icode.harness = Some("icode".into());
        assert_eq!(icode.job(), "deepswe_some_task");
        assert_eq!(names(&request_fields(&icode).unwrap()), ["HARNESS=icode", "TASKS=a"]);
    }

    #[test]
    fn each_harness_takes_only_its_own_inputs() {
        let err = |req: QueueRequest| request_fields(&req).unwrap_err().to_string();
        assert!(err(jiuwen(JobShape::One, |r| r.icode_git_ref = Some("2".into())))
            .contains("--icode-git-ref is an iCode input"));
        assert!(err(jiuwen(JobShape::Some_, |r| r.icode_mode = Some("git".into())))
            .contains("--icode-mode is an iCode input"));
        let mut req = some("a");
        req.jiuwenswarm_git_url = Some("https://gitcode.com/michaelling/jiuwenswarm".into());
        assert!(err(req).contains("--jiuwenswarm-git-url needs --harness jiuwenswarm"));
        assert!(err(jiuwen(JobShape::One, |r| r.jiuwenswarm_git_url = Some("https://evil.example/x".into())))
            .contains("JIUWENSWARM_GIT_URL host 'evil.example'"));
        assert!(err(jiuwen(JobShape::One, |r| {
            r.jiuwenswarm_git_ref = Some("main".into());
            r.jiuwenswarm_git_ref_kind = Some("pr".into());
        }))
        .contains("JIUWENSWARM_GIT_REF_KIND=pr requires a pull-request number"));
        let param = |p: &str| {
            let mut req = jiuwen(JobShape::FullSuite, |_| {});
            req.params = vec![p.into()];
            err(req)
        };
        assert!(param("JIUWENSWARM_MODE=release").contains("needs an uploaded JIUWENSWARM_RELEASE_FILE"));
        assert!(param("JIUWENSWARM_RELEASE_FILE=x").contains("file upload"));
        assert!(param("JIUWENSWARM_MAX_TURNS=2000").contains("1 to 1000"));
        assert!(param("JIUWENSWARM_AGENT_MODE=team").contains("unknown JiuwenSwarm agent mode"));
        assert!(param("JIUWENSWARM_GIT_REF=2").contains("use --jiuwenswarm-git-ref"));
        assert!(param("ICODE_GIT_REF=2").contains("use --icode-git-ref"));
        assert!(param("HARNESS=icode").contains("use --harness"));
        // LoLBench has no JiuwenSwarm field to set.
        let lol = QueueRequest {
            benchmark: "lolbench".into(),
            shape: JobShape::Some_,
            tasks: Some("ruff_1".into()),
            params: vec!["JIUWENSWARM_MAX_TURNS=40".into()],
            ..Default::default()
        };
        assert!(err(lol).contains("has no field JIUWENSWARM_MAX_TURNS"));

        let args = EvalArgs {
            benchmark: Some("lolbench".into()),
            harness: Some("jiuwenswarm".into()),
            ..Default::default()
        };
        let err = QueueRequest::from_args(&args, JobShape::One).unwrap_err().to_string();
        assert!(err.contains("harness jiuwenswarm runs on deepswe only, not lolbench"), "{err}");
    }

    #[test]
    fn the_query_is_encoded_and_carries_no_credentials() {
        let fields = vec![
            ("TASKS", "a,b".to_string()),
            ("ICODE_GIT_URL", "https://gitcode.com/x/y".to_string()),
        ];
        assert_eq!(query(&fields), "?TASKS=a%2Cb&ICODE_GIT_URL=https%3A%2F%2Fgitcode.com%2Fx%2Fy");
        assert_eq!(query(&[]), "");
    }

    #[test]
    fn a_dry_run_needs_no_controller_or_credentials() {
        let req = some("ytt-jsonpath-query-api");
        queue(&MacK3dConfig::default(), &req, true).unwrap();
        assert_eq!(req.job(), "deepswe_some_task");
        assert!(parse_shape("all").unwrap_err().to_string().contains("use one"));
        assert_eq!(parse_shape("full").unwrap(), JobShape::FullSuite);
    }
}
