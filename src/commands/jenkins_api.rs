//! Jenkins REST calls the CLI makes (`eval --job`, `eval record`).
//!
//! The API user and token go to curl on stdin (`--config -`), never in its
//! arguments, a URL, a file or a log, so they do not show in `ps` on a shared
//! host. The crumb header goes the same way (see `crate::jenkins_curl`).

use std::path::Path;
use std::process::Output;

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};
use crate::jenkins_curl::{auth_line, header_line, CookieJar};

pub(super) struct JenkinsAccess {
    pub base: String,
    pub user: String,
    pub token: String,
    pub source: String,
}

/// The loaded config's `jenkins_agent` API user/token, else worker.yaml's (a
/// PC that is both a standalone controller and a worker keeps them there).
pub(super) fn jenkins_access(config: &MacK3dConfig) -> Result<JenkinsAccess> {
    let worker_path = MacK3dConfig::default_worker_path();
    let worker = MacK3dConfig::load_file(&worker_path).ok();
    let candidates = [
        Some((config, "the loaded config".to_string())),
        worker.as_ref().map(|w| (w, worker_path.display().to_string())),
    ];
    for (cfg, source) in candidates.into_iter().flatten() {
        let Some((user, token)) = cfg.jenkins_agent.api_credentials() else {
            continue;
        };
        let base = cfg
            .jenkins_agent
            .controller_url
            .clone()
            .filter(|s| !s.trim().is_empty())
            .unwrap_or_else(|| format!("http://localhost:{}", cfg.jenkins.host_port));
        return Ok(JenkinsAccess {
            base: base.trim_end_matches('/').to_string(),
            user: user.to_string(),
            token: token.to_string(),
            source,
        });
    }
    Err(Error::Config(format!(
        "no Jenkins API user/token: set jenkins_agent.api_user and api_token in the config \
         or in {}",
        worker_path.display()
    )))
}

/// One curl run with the credentials (and any extra config lines) on stdin.
fn run_curl(access: &JenkinsAccess, args: &[String], extra_config: &str) -> Result<Output> {
    let config = format!("{}{extra_config}", auth_line(&access.user, &access.token));
    crate::jenkins_curl::run_curl(args, &config)
}

fn failure(method: &str, url: &str, stderr: &[u8]) -> Error {
    let err = String::from_utf8_lossy(stderr);
    let first = err.lines().next().unwrap_or("curl failed").trim();
    let hint = if first.contains("error: 404") {
        " (no such job or build on this controller)"
    } else if first.contains("error: 401") || first.contains("error: 403") {
        " (Jenkins rejected the API user/token)"
    } else {
        ""
    };
    Error::Config(format!("{method} {url} failed: {first}{hint}"))
}

/// curl arguments for one GET into `out`; credentials come from stdin, never from here.
fn curl_get_args(url: &str, out: &Path) -> Vec<String> {
    vec![
        "-fsS".into(),
        "-g".into(),
        "--max-time".into(),
        "300".into(),
        "--config".into(),
        "-".into(),
        "-o".into(),
        out.display().to_string(),
        url.into(),
    ]
}

pub(super) fn curl_get(access: &JenkinsAccess, url: &str, out: &Path) -> Result<()> {
    let done = run_curl(access, &curl_get_args(url, out), "")?;
    if !done.status.success() {
        return Err(failure("GET", url, &done.stderr));
    }
    Ok(())
}

pub(super) fn get_json(access: &JenkinsAccess, url: &str) -> Result<serde_json::Value> {
    let args: Vec<String> = ["-fsS", "-g", "--max-time", "60", "--config", "-", url]
        .iter()
        .map(|s| s.to_string())
        .collect();
    let done = run_curl(access, &args, "")?;
    if !done.status.success() {
        return Err(failure("GET", url, &done.stderr));
    }
    serde_json::from_slice(&done.stdout)
        .map_err(|e| Error::Config(format!("GET {url}: the reply is not JSON ({e})")))
}

/// What Jenkins answered to a POST.
#[derive(Debug, PartialEq, Eq)]
pub(super) struct PostReply {
    pub code: u16,
    /// `Location:` header, e.g. the queue item of a new build.
    pub location: Option<String>,
}

/// `curl -D - -o /dev/null -w '\n%{http_code}'` output: headers, then the code.
fn parse_post_reply(text: &str) -> Option<PostReply> {
    let code = text.lines().last()?.trim().parse().ok()?;
    let location = text.lines().find_map(|l| {
        let (name, value) = l.split_once(':')?;
        name.trim()
            .eq_ignore_ascii_case("location")
            .then(|| value.trim().to_string())
            .filter(|v| !v.is_empty())
    });
    Some(PostReply { code, location })
}

/// POST `url` with a CSRF crumb when the controller issues one.
pub(super) fn post_with_crumb(access: &JenkinsAccess, url: &str) -> Result<PostReply> {
    let jar = CookieJar::new()?;
    let jar_path = jar.path().display().to_string();
    let crumb_url = format!("{}/crumbIssuer/api/json", access.base);
    let crumb_args: Vec<String> = [
        "-fsS", "--max-time", "30", "--config", "-", "-c", &jar_path, "-b", &jar_path, &crumb_url,
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let crumb_header = run_curl(access, &crumb_args, "")
        .ok()
        .filter(|o| o.status.success())
        .and_then(|o| serde_json::from_slice::<serde_json::Value>(&o.stdout).ok())
        .and_then(|v| {
            Some(header_line(v["crumbRequestField"].as_str()?, v["crumb"].as_str()?))
        })
        .unwrap_or_default();
    let args: Vec<String> = [
        "-sS", "-g", "--max-time", "60", "--config", "-", "-b", &jar_path, "-c", &jar_path, "-X",
        "POST", "-o", "/dev/null", "-D", "-", "-w", "\n%{http_code}", url,
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    let done = run_curl(access, &args, &crumb_header)?;
    if !done.status.success() {
        return Err(failure("POST", url, &done.stderr));
    }
    parse_post_reply(&String::from_utf8_lossy(&done.stdout))
        .ok_or_else(|| Error::Config(format!("POST {url}: no HTTP status in the reply")))
}

/// Percent-encode a path segment (keeps `/`).
pub(super) fn url_path(s: &str) -> String {
    encode(s, b"-_.~/")
}

/// Percent-encode a query value (keeps only unreserved characters).
pub(super) fn query_value(s: &str) -> String {
    encode(s, b"-_.~")
}

fn encode(s: &str, keep: &[u8]) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || keep.contains(&b) {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

/// Jenkins reports URLs with its own idea of its host (`http://jenkins:8080/…`
/// inside the cluster). Keep the path, use the base this CLI reached it on.
pub(super) fn public_url(base: &str, reported: &str) -> String {
    let path = match reported.split_once("://") {
        Some((_, rest)) => rest.split_once('/').map_or("", |(_, p)| p),
        None => reported.trim_start_matches('/'),
    };
    format!("{}/{path}", base.trim_end_matches('/'))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn credentials_go_to_the_curl_config_not_the_arguments() {
        let token = "11abcdef0123456789abcdef0123456789";
        let args = curl_get_args(
            "http://ctl:17070/job/deepswe_one_task/54/consoleText",
            Path::new("/tmp/x"),
        );
        assert!(args.iter().all(|a| !a.contains(token) && !a.contains("admin")));
        assert!(args.windows(2).any(|w| w[0] == "--config" && w[1] == "-"));
        assert!(args.contains(&"-g".to_string()), "brackets in ?tree= must not glob");
        assert_eq!(auth_line("admin", token), format!("user = \"admin:{token}\"\n"));
    }

    #[test]
    fn post_reply_reads_the_code_and_the_queue_location() {
        let text = "HTTP/1.1 201 Created\r\nDate: x\r\nLocation: http://jenkins:8080/queue/item/211/\r\n\r\n\n201";
        assert_eq!(
            parse_post_reply(text),
            Some(PostReply {
                code: 201,
                location: Some("http://jenkins:8080/queue/item/211/".into()),
            })
        );
        assert_eq!(
            parse_post_reply("HTTP/1.1 400 Bad Request\r\n\r\n\n400"),
            Some(PostReply { code: 400, location: None })
        );
        assert_eq!(parse_post_reply(""), None);
    }

    #[test]
    fn reported_urls_use_the_controller_this_cli_reached() {
        assert_eq!(
            public_url("http://43.107.42.252:17070", "http://jenkins:8080/queue/item/211/"),
            "http://43.107.42.252:17070/queue/item/211/"
        );
        assert_eq!(
            public_url("http://ctl:17070/", "http://jenkins:8080/job/deepswe_some_task/3/"),
            "http://ctl:17070/job/deepswe_some_task/3/"
        );
        assert_eq!(public_url("http://ctl:17070", "/queue/item/5/"), "http://ctl:17070/queue/item/5/");
    }

    #[test]
    fn query_values_are_fully_encoded() {
        assert_eq!(query_value("a,b c&d=e/f:g"), "a%2Cb%20c%26d%3De%2Ff%3Ag");
        assert_eq!(query_value("deepseek-flash"), "deepseek-flash");
        assert_eq!(url_path("eval-runs/54 x"), "eval-runs/54%20x");
    }
}
