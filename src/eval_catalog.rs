//! Allow-lists for eval YAML / Jenkins parameters.
//!
//! v1 runners are `icode` + `deepseek` on `deepswe` | `lolbench` | `swebenchpro`, and
//! `jiuwenswarm` + `deepseek` on `deepswe`. Chat Completions ids live in `MODELS`
//! (not `LLMS`). Append here when a new harness, LLM, model, or benchmark is implemented.

use crate::error::{Error, Result};

/// The first is the default.
pub const HARNESSES: &[&str] = &["icode", "jiuwenswarm"];
/// Benchmarks a harness is limited to; one not listed runs on every benchmark.
/// Same rule as `pipeline/lib/harness.py`.
const HARNESS_BENCHMARKS: &[(&str, &[&str])] = &[("jiuwenswarm", &["deepswe"])];
pub const LLMS: &[&str] = &["deepseek"];
pub const BENCHMARKS: &[&str] = &["deepswe", "lolbench", "swebenchpro"];
/// DeepSeek Chat Completions ids (`DEEPSEEK_MODEL` / `--model`). First is the default.
/// Append only `data[].id` values from `GET https://api.deepseek.com/models`.
/// Product name "V4.1 Flash" is API id `deepseek-flash` (not `deepseek-v4.1-flash`).
pub const MODELS: &[&str] = &["deepseek-flash", "deepseek-v4-pro"];
/// Jenkins / CLI iCode inputs (`ICODE_MODE`). `binary` is a pipeline alias of `release`.
pub const ICODE_CI_MODES: &[&str] = &["release", "git"];
/// https hosts allowed for `ICODE_GIT_URL` / `get_bin_icode`.
pub const ICODE_GIT_HOSTS: &[&str] = &["github.com", "www.github.com", "gitcode.com"];
/// How `ICODE_GIT_REF` is checked out. Jenkins/CLI pick branch, tag, commit, or pr (`auto` is leftover).
pub const ICODE_GIT_REF_KINDS: &[&str] = &["branch", "tag", "commit", "pr"];
/// `JIUWENSWARM_MODE`: a git ref built in the task platform's image, or an
/// uploaded release (.whl, wheelhouse .tar.gz or mac-k3d bundle).
pub const JIUWENSWARM_MODES: &[&str] = &["git", "release"];
/// The single-agent modes `jiuwenswarm-process` runs headless; first is the
/// default. Same list as `pipeline/lib/jiuwenswarm_trial.py`.
pub const JIUWENSWARM_AGENT_MODES: &[&str] = &["agent.code.normal", "agent.code.plan"];
pub const JIUWENSWARM_MAX_TURNS_DEFAULT: u32 = 500;
/// JiuwenSwarm's own ceiling on `max_turns`.
pub const JIUWENSWARM_MAX_TURNS_LIMIT: u32 = 1000;

pub fn join_allowed(items: &[&str]) -> String {
    items.join(", ")
}

pub fn parse_task_list(raw: &str) -> Vec<String> {
    raw.split([',', '\n'])
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .map(|s| s.to_string())
        .collect()
}

pub fn require_harness(value: &str) -> Result<String> {
    require_in(value, HARNESSES, "harness")
}

/// Whether `harness` runs on `benchmark`.
pub fn harness_supports(harness: &str, benchmark: &str) -> bool {
    match HARNESS_BENCHMARKS.iter().find(|(h, _)| *h == harness) {
        Some((_, benches)) => benches.contains(&benchmark),
        None => HARNESSES.contains(&harness),
    }
}

/// `require_harness`, then refuse a benchmark the harness does not run on.
pub fn require_harness_for(value: &str, benchmark: &str) -> Result<String> {
    let harness = require_harness(value)?;
    if !harness_supports(&harness, benchmark) {
        let benches: Vec<&str> = BENCHMARKS.iter().copied().filter(|b| harness_supports(&harness, b)).collect();
        return Err(Error::Config(format!(
            "harness {harness} runs on {} only, not {benchmark}",
            join_allowed(&benches)
        )));
    }
    Ok(harness)
}

pub fn require_llm(value: &str) -> Result<String> {
    require_in(value, LLMS, "llm")
}

pub fn require_benchmark(value: &str) -> Result<String> {
    require_in(value, BENCHMARKS, "benchmark")
}

pub fn require_model(value: &str) -> Result<String> {
    require_in(value, MODELS, "model")
}

pub fn normalize_icode_mode(value: &str) -> Result<String> {
    let v = value.trim().to_ascii_lowercase();
    match v.as_str() {
        "binary" | "release" => Ok("release".into()),
        "git" => Ok("git".into()),
        "source" => Err(Error::Config(
            "icode-mode source is not supported. Push to GitHub/GitCode and use --icode-mode git, or download a *-full-* binary and use --icode-mode release".into(),
        )),
        _ => Err(Error::Config(format!(
            "unknown icode-mode '{value}' (allowed: release, git; binary is an alias of release)"
        ))),
    }
}

pub fn require_icode_git_url(value: &str) -> Result<String> {
    require_git_url("ICODE_GIT_URL", value)
}

/// An https clone URL on an allow-listed host with no userinfo. `field` names
/// it in errors: `ICODE_GIT_URL` or `JIUWENSWARM_GIT_URL`.
pub fn require_git_url(field: &str, value: &str) -> Result<String> {
    let u = value.trim();
    if u.is_empty() {
        return Err(Error::Config(format!("{field} is empty")));
    }
    let rest = u
        .strip_prefix("https://")
        .ok_or_else(|| Error::Config(format!("{field} must be https://")))?;
    if rest.contains('@') {
        return Err(Error::Config(format!(
            "{field} must not contain userinfo (no tokens in the URL)"
        )));
    }
    let host = rest
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    if !ICODE_GIT_HOSTS.iter().any(|h| *h == host) {
        return Err(Error::Config(format!(
            "{field} host '{host}' is not allow-listed ({})",
            join_allowed(ICODE_GIT_HOSTS)
        )));
    }
    Ok(u.to_string())
}

pub fn require_icode_git_ref(value: &str) -> Result<String> {
    require_git_ref("ICODE_GIT_REF", value)
}

pub fn require_git_ref(field: &str, value: &str) -> Result<String> {
    let r = value.trim();
    if r.is_empty() {
        return Err(Error::Config(format!("{field} is empty")));
    }
    if r.starts_with('-') || r.contains("..") {
        return Err(Error::Config(format!(
            "{field} must not start with - or contain .."
        )));
    }
    if !r
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || matches!(c, '.' | '_' | '/' | '+' | '-'))
    {
        return Err(Error::Config(format!(
            "{field} is not a tag, commit, or branch name"
        )));
    }
    Ok(r.to_string())
}

pub fn looks_like_git_commit(value: &str) -> bool {
    let r = value.trim();
    (7..=40).contains(&r.len()) && r.chars().all(|c| c.is_ascii_hexdigit())
}

/// Pull-request number: positive decimal integer, no leading zero.
pub fn looks_like_pr_number(value: &str) -> bool {
    let r = value.trim();
    let mut chars = r.chars();
    match chars.next() {
        Some(c) if c.is_ascii_digit() && c != '0' => chars.all(|c| c.is_ascii_digit()),
        _ => false,
    }
}

/// Normalize `ICODE_GIT_REF_KIND`. `auto` infers commit vs branch from the ref.
pub fn normalize_icode_git_ref_kind(kind: &str, git_ref: &str) -> Result<String> {
    let k = kind.trim().to_ascii_lowercase();
    match k.as_str() {
        "branch" | "tag" | "commit" | "pr" => Ok(k),
        "auto" | "" => {
            if looks_like_git_commit(git_ref) {
                Ok("commit".into())
            } else {
                Ok("branch".into())
            }
        }
        _ => Err(Error::Config(format!(
            "unknown icode-git-ref-kind '{kind}' (allowed: {}; leftover auto still maps from the ref)",
            join_allowed(ICODE_GIT_REF_KINDS)
        ))),
    }
}

pub fn require_icode_git_ref_for_kind(kind: &str, git_ref: &str) -> Result<(String, String)> {
    require_git_ref_for_kind("ICODE_GIT_REF", kind, git_ref)
}

/// `field` is `ICODE_GIT_REF` or `JIUWENSWARM_GIT_REF`; its kind is `<field>_KIND`.
pub fn require_git_ref_for_kind(field: &str, kind: &str, git_ref: &str) -> Result<(String, String)> {
    let git_ref = require_git_ref(field, git_ref)?;
    let kind = normalize_icode_git_ref_kind(kind, &git_ref)?;
    let flag = format!("--{}-kind", field.to_ascii_lowercase().replace('_', "-"));
    if kind == "commit" && !looks_like_git_commit(&git_ref) {
        return Err(Error::Config(format!(
            "{field}_KIND=commit requires a git SHA (7–40 hex chars), not '{git_ref}'. Use {flag} branch or tag."
        )));
    }
    if kind == "pr" && !looks_like_pr_number(&git_ref) {
        return Err(Error::Config(format!(
            "{field}_KIND=pr requires a pull-request number (positive integer), not '{git_ref}'."
        )));
    }
    Ok((kind, git_ref))
}

pub fn require_jiuwenswarm_agent_mode(value: &str) -> Result<String> {
    require_in(value, JIUWENSWARM_AGENT_MODES, "JiuwenSwarm agent mode")
}

pub fn require_jiuwenswarm_max_turns(value: u32) -> Result<u32> {
    if (1..=JIUWENSWARM_MAX_TURNS_LIMIT).contains(&value) {
        return Ok(value);
    }
    Err(Error::Config(format!(
        "JIUWENSWARM_MAX_TURNS must be 1 to {JIUWENSWARM_MAX_TURNS_LIMIT} (JiuwenSwarm's own limit), not {value}"
    )))
}

/// Env / Jenkins binding for a private git clone. Never put the PAT in the URL.
pub struct GitPatSpec {
    pub env_var: &'static str,
    pub env_alias: &'static str,
    pub jenkins_id: &'static str,
    pub prompt: &'static str,
}

pub fn icode_git_host(url: &str) -> Result<String> {
    let u = require_icode_git_url(url)?;
    let host = u
        .trim_start_matches("https://")
        .split('/')
        .next()
        .unwrap_or("")
        .split(':')
        .next()
        .unwrap_or("");
    Ok(host.to_string())
}

pub fn git_pat_spec(url: &str) -> Result<GitPatSpec> {
    match icode_git_host(url)?.as_str() {
        "gitcode.com" => Ok(GitPatSpec {
            env_var: "GITCODE_TOKEN",
            env_alias: "MAC_K3D_GITCODE_PAT",
            jenkins_id: "gitcode-pat",
            prompt: "GitCode PAT (empty if the repo is public; saved to .env, never printed)",
        }),
        "github.com" | "www.github.com" => Ok(GitPatSpec {
            env_var: "GITHUB_TOKEN",
            env_alias: "MAC_K3D_GITHUB_PAT",
            jenkins_id: "github-pat",
            prompt: "GitHub PAT (empty if the repo is public; saved to .env, never printed)",
        }),
        other => Err(Error::Config(format!(
            "ICODE_GIT_URL host '{other}' is not allow-listed ({})",
            join_allowed(ICODE_GIT_HOSTS)
        ))),
    }
}

pub fn default_model() -> &'static str {
    MODELS[0]
}

fn require_in(value: &str, allowed: &[&str], kind: &str) -> Result<String> {
    let v = value.trim().to_ascii_lowercase();
    if allowed.iter().any(|a| *a == v) {
        return Ok(v);
    }
    Err(Error::Config(format!(
        "unknown {kind} '{value}' (allowed: {})",
        join_allowed(allowed)
    )))
}

/// Put `preferred` first when it is in `allowed` (Jenkins choice default).
pub fn choices_preferred_first<'a>(allowed: &'a [&'a str], preferred: &str) -> Vec<&'a str> {
    let pref = preferred.trim();
    let mut out = Vec::new();
    if let Some(hit) = allowed.iter().copied().find(|a| *a == pref) {
        out.push(hit);
    }
    for a in allowed {
        if !out.contains(a) {
            out.push(*a);
        }
    }
    out
}

pub fn print_catalog() {
    println!("harness:    {}", join_allowed(HARNESSES));
    println!("llm:        {}", join_allowed(LLMS));
    println!("model:      {}  (default first)", join_allowed(MODELS));
    println!("benchmark:  {}", join_allowed(BENCHMARKS));
    println!("questions:  --task ID  |  --n-tasks N  |  --tasks id,id,…  (mutually exclusive)");
    println!("icode:      release | git  (Jenkins + CLI; persist binary path with mac-k3d set --icode-release)");
    println!("jiuwenswarm: git | release on deepswe only; agent mode {}", join_allowed(JIUWENSWARM_AGENT_MODES));
    println!("git hosts:  {}", join_allowed(ICODE_GIT_HOSTS));
    println!(
        "git ref:    {}  (legacy auto: 7-40 hex -> commit, else branch)",
        join_allowed(ICODE_GIT_REF_KINDS)
    );
    println!(
        "provider:   GET {{ICODE_API_BASE or https://api.deepseek.com}}/models  (set --check-models)"
    );
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reject_unknown_harness() {
        let err = require_harness("oracle").unwrap_err().to_string();
        assert!(err.contains("allowed: icode, jiuwenswarm"), "{err}");
    }

    #[test]
    fn jiuwenswarm_runs_on_deepswe_only() {
        assert_eq!(HARNESSES[0], "icode");
        for bench in BENCHMARKS {
            assert!(harness_supports("icode", bench), "{bench}");
            assert_eq!(harness_supports("jiuwenswarm", bench), *bench == "deepswe", "{bench}");
        }
        assert!(!harness_supports("oracle", "deepswe"));
        assert_eq!(require_harness_for("JiuwenSwarm", "deepswe").unwrap(), "jiuwenswarm");
        let err = require_harness_for("jiuwenswarm", "lolbench").unwrap_err().to_string();
        assert!(err.contains("runs on deepswe only, not lolbench"), "{err}");
        // pipeline/lib/harness.py keeps the same table for the stages.
        let table = include_str!("../pipeline/lib/harness.py");
        assert!(table.contains("benchmarks=(\"deepswe\",)"), "harness.py limits jiuwenswarm to deepswe");
    }

    #[test]
    fn jiuwenswarm_settings_match_what_the_trial_accepts() {
        let trial = include_str!("../pipeline/lib/jiuwenswarm_trial.py");
        let modes = JIUWENSWARM_AGENT_MODES.iter().map(|m| format!("\"{m}\"")).collect::<Vec<_>>().join(", ");
        assert!(trial.contains(&format!("MODES = ({modes})")), "jiuwenswarm_trial.py MODES differ");
        assert!(trial.contains(&format!("_positive_int(\"JIUWENSWARM_MAX_TURNS\", {JIUWENSWARM_MAX_TURNS_DEFAULT})")));
        assert!(trial.contains(&format!("if turns > {JIUWENSWARM_MAX_TURNS_LIMIT}:")));
        let common = include_str!("../pipeline/stages/_common.sh");
        assert!(common.contains(&format!("JIUWENSWARM_AGENT_MODE:-{}", JIUWENSWARM_AGENT_MODES[0])));
        assert!(common.contains(&format!("JIUWENSWARM_MAX_TURNS:-{JIUWENSWARM_MAX_TURNS_DEFAULT}")));
        assert_eq!(require_jiuwenswarm_agent_mode("Agent.Code.Plan").unwrap(), "agent.code.plan");
        assert!(require_jiuwenswarm_agent_mode("team").is_err());
        assert_eq!(require_jiuwenswarm_max_turns(1000).unwrap(), 1000);
        assert!(require_jiuwenswarm_max_turns(0).is_err());
        assert!(require_jiuwenswarm_max_turns(1001).unwrap_err().to_string().contains("1 to 1000"));
    }

    #[test]
    fn git_checks_name_the_field_they_check() {
        let err = require_git_url("JIUWENSWARM_GIT_URL", "https://evil.example/x").unwrap_err().to_string();
        assert!(err.contains("JIUWENSWARM_GIT_URL host 'evil.example' is not allow-listed"), "{err}");
        let err = require_git_ref_for_kind("JIUWENSWARM_GIT_REF", "commit", "main").unwrap_err().to_string();
        assert!(err.contains("JIUWENSWARM_GIT_REF_KIND=commit") && err.contains("--jiuwenswarm-git-ref-kind"), "{err}");
        let err = require_icode_git_ref_for_kind("commit", "main").unwrap_err().to_string();
        assert!(err.contains("Use --icode-git-ref-kind branch or tag."), "{err}");
    }

    #[test]
    fn reject_unknown_model() {
        let err = require_model("gpt-4").unwrap_err().to_string();
        assert!(
            err.contains("allowed: deepseek-flash, deepseek-v4-pro"),
            "{err}"
        );
    }

    #[test]
    fn require_model_accepts_both_and_defaults_to_flash() {
        assert_eq!(require_model("deepseek-flash").unwrap(), "deepseek-flash");
        assert_eq!(require_model("deepseek-v4-pro").unwrap(), "deepseek-v4-pro");
        assert_eq!(default_model(), "deepseek-flash");
    }

    #[test]
    fn pipeline_fallback_model_is_the_catalog_default() {
        let common = include_str!("../pipeline/stages/_common.sh");
        let want = format!("export DEEPSEEK_MODEL=\"${{DEEPSEEK_MODEL:-{}}}\"", default_model());
        assert!(
            common.contains(&want),
            "pipeline/stages/_common.sh must default DEEPSEEK_MODEL to {} (eval_catalog::MODELS[0])",
            default_model()
        );
    }

    #[test]
    fn parse_comma_and_newline_list() {
        assert_eq!(
            parse_task_list(" abs-stepped-slices , abs-module-cache-flags\n"),
            vec!["abs-stepped-slices", "abs-module-cache-flags"]
        );
    }

    #[test]
    fn preferred_first_orders_choices() {
        assert_eq!(
            choices_preferred_first(BENCHMARKS, "lolbench"),
            vec!["lolbench", "deepswe", "swebenchpro"]
        );
        assert_eq!(
            choices_preferred_first(ICODE_CI_MODES, "git"),
            vec!["git", "release"]
        );
    }

    #[test]
    fn require_icode_git_url_rejects_file_and_userinfo() {
        assert!(require_icode_git_url("file:///tmp/icode").is_err());
        assert!(require_icode_git_url("https://user:token@github.com/org/icode.git").is_err());
        assert!(require_icode_git_url("https://github.com.evil.com/org/icode.git").is_err());
        assert!(require_icode_git_url("https://github.com/org/icode.git").is_ok());
    }

    #[test]
    fn require_icode_git_ref_rejects_flags() {
        assert!(require_icode_git_ref("--upload-pack").is_err());
        assert!(require_icode_git_ref("foo;rm").is_err());
        assert_eq!(require_icode_git_ref("v0.1.41").unwrap(), "v0.1.41");
        assert_eq!(normalize_icode_mode("binary").unwrap(), "release");
        assert_eq!(normalize_icode_mode("git").unwrap(), "git");
        let err = normalize_icode_mode("source").unwrap_err().to_string();
        assert!(err.contains("not supported"), "{err}");
    }

    #[test]
    fn git_ref_kind_auto_and_commit_sha() {
        assert_eq!(
            normalize_icode_git_ref_kind("auto", "main").unwrap(),
            "branch"
        );
        assert_eq!(
            normalize_icode_git_ref_kind("auto", "0123456789abcdef0123456789abcdef01234567")
                .unwrap(),
            "commit"
        );
        assert_eq!(
            normalize_icode_git_ref_kind("tag", "v0.1.41").unwrap(),
            "tag"
        );
        assert!(require_icode_git_ref_for_kind("commit", "main").is_err());
        let (k, r) = require_icode_git_ref_for_kind("commit", "deadbee").unwrap();
        assert_eq!(k, "commit");
        assert_eq!(r, "deadbee");
        assert!(normalize_icode_git_ref_kind("sha", "main").is_err());
        let (k, r) = require_icode_git_ref_for_kind("pr", "123").unwrap();
        assert_eq!(k, "pr");
        assert_eq!(r, "123");
        assert!(require_icode_git_ref_for_kind("pr", "main").is_err());
        assert!(require_icode_git_ref_for_kind("pr", "deadbee").is_err());
        assert!(require_icode_git_ref_for_kind("pr", "0").is_err());
        assert!(
            !ICODE_GIT_REF_KINDS.contains(&"auto"),
            "Jenkins/CLI catalog is branch, tag, commit, pr"
        );
    }

    #[test]
    fn git_pat_spec_maps_host_to_env_and_jenkins_id() {
        let gc = git_pat_spec("https://gitcode.com/org/icode.git").unwrap();
        assert_eq!(gc.env_var, "GITCODE_TOKEN");
        assert_eq!(gc.jenkins_id, "gitcode-pat");
        let gh = git_pat_spec("https://github.com/org/icode.git").unwrap();
        assert_eq!(gh.env_var, "GITHUB_TOKEN");
        assert_eq!(gh.jenkins_id, "github-pat");
        assert!(git_pat_spec("https://user:token@gitcode.com/org/icode.git").is_err());
    }
}
