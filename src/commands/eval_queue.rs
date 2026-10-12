//! `mac-k3d eval --job one|some|full`: queue one of the nine Jenkins eval jobs
//! with the fields of its Build with Parameters page. Only the fields given on
//! the command line are sent; every other field keeps the job's default, which
//! is what pressing Build with the form untouched does.

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
    pub model: Option<String>,
    /// `--param NAME=VALUE`, in the order given.
    pub params: Vec<String>,
}

impl QueueRequest {
    pub(super) fn from_args(args: &EvalArgs, shape: JobShape) -> Result<Self> {
        Ok(Self {
            benchmark: eval_catalog::require_benchmark(args.benchmark.as_deref().unwrap_or("deepswe"))?,
            shape,
            task: args.task.clone(),
            tasks: args.tasks.clone(),
            n_tasks: args.n_tasks,
            n_rollouts: args.n_rollouts,
            icode_mode: args.icode_mode.clone(),
            icode_git_url: args.icode_git_url.clone(),
            icode_git_ref: args.icode_git_ref.clone(),
            icode_git_ref_kind: args.icode_git_ref_kind.clone(),
            model: args.model.clone(),
            params: args.param.clone(),
        })
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
    ("TASK", "--task"),
    ("TASKS", "--tasks"),
    ("N_TASKS", "--n-tasks"),
    ("N_ROLLOUTS", "--n-rollouts"),
    ("ICODE_MODE", "--icode-mode"),
    ("ICODE_GIT_URL", "--icode-git-url"),
    ("ICODE_GIT_REF", "--icode-git-ref"),
    ("ICODE_GIT_REF_KIND", "--icode-git-ref-kind"),
    ("DEEPSEEK_MODEL", "--model"),
];

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
            if let Some(mode) = nonblank(&req.icode_mode) {
                if eval_catalog::normalize_icode_mode(&mode)? == "release" {
                    return Err(Error::Config(format!(
                        "{job} with ICODE_MODE=release needs an uploaded ICODE_RELEASE_FILE, which the CLI \
                         cannot attach. Open the job's Build with Parameters page, or use --icode-mode git"
                    )));
                }
                set.push(("ICODE_MODE", "git".into(), "--icode-mode"));
            }
        }
        JobShape::Some_ | JobShape::FullSuite => {
            if nonblank(&req.task).is_some() {
                return Err(Error::Config(format!(
                    "{job} takes a list: use --tasks a,b,c (--task is for --job one)"
                )));
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
    if let Some(url) = nonblank(&req.icode_git_url) {
        set.push(("ICODE_GIT_URL", eval_catalog::require_icode_git_url(&url)?, "--icode-git-url"));
    }
    match (nonblank(&req.icode_git_ref), nonblank(&req.icode_git_ref_kind)) {
        (Some(git_ref), Some(kind)) => {
            let (kind, git_ref) = eval_catalog::require_icode_git_ref_for_kind(&kind, &git_ref)?;
            set.push(("ICODE_GIT_REF", git_ref, "--icode-git-ref"));
            set.push(("ICODE_GIT_REF_KIND", kind, "--icode-git-ref-kind"));
        }
        (Some(git_ref), None) => {
            set.push(("ICODE_GIT_REF", eval_catalog::require_icode_git_ref(&git_ref)?, "--icode-git-ref"));
        }
        (None, Some(kind)) => {
            let kind = kind.to_ascii_lowercase();
            if !eval_catalog::ICODE_GIT_REF_KINDS.contains(&kind.as_str()) {
                return Err(Error::Config(format!(
                    "--icode-git-ref-kind {kind}: use one of {}",
                    eval_catalog::join_allowed(eval_catalog::ICODE_GIT_REF_KINDS)
                )));
            }
            set.push(("ICODE_GIT_REF_KIND", kind, "--icode-git-ref-kind"));
        }
        (None, None) => {}
    }
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
                "{name} is set by some_task / full_suite_task on each shard build; it cannot be set by hand"
            )));
        }
        if name == "ICODE_RELEASE_FILE" {
            return Err(Error::Config(
                "ICODE_RELEASE_FILE is a file upload: use the job's Build with Parameters page".into(),
            ));
        }
        if let Some((_, flag)) = FLAG_FIELDS.iter().find(|(n, _)| *n == name) {
            return Err(Error::Config(format!("{name} has its own flag, which checks the value: use {flag}")));
        }
        if set.iter().any(|(n, _, _)| *n == field.name) {
            return Err(Error::Config(format!("--param {name} is given twice")));
        }
        set.push((field.name, value.trim().to_string(), "--param"));
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
    if let Some((_, url)) = fields.iter().find(|(n, _)| *n == "ICODE_GIT_URL") {
        if let Ok(spec) = eval_catalog::git_pat_spec(url) {
            println!(
                "The git clone uses controller credential {} ({}); for a private repo add it with \
                 `mac-k3d config --update-secrets` on the controller, never in ICODE_GIT_URL.",
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
