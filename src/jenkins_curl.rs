//! Jenkins REST calls over curl, for `config` (agent, jobs, credentials, lockable
//! resources) and the CLI's `eval --job` / `eval record`.
//!
//! The API user and token, the CSRF crumb and any scriptText body go to curl on
//! stdin (`--config -`). Process arguments are readable by every user on a
//! shared host (`ps`), so nothing secret goes there, nor in a URL. Node and job
//! XML bind credentials by ID only, so those bodies may stay arguments.

use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};

use crate::error::{Error, Result};

/// A double-quoted curl config string body: `\`, `"`, newline, CR and tab escaped.
pub fn curl_quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len());
    for c in s.chars() {
        match c {
            '\\' => out.push_str("\\\\"),
            '"' => out.push_str("\\\""),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c => out.push(c),
        }
    }
    out
}

/// `user = "<user>:<token>"`, the only place the API credentials go.
pub fn auth_line(user: &str, token: &str) -> String {
    format!("user = \"{}:{}\"\n", curl_quote(user), curl_quote(token))
}

pub fn header_line(field: &str, value: &str) -> String {
    format!("header = \"{}: {}\"\n", curl_quote(field), curl_quote(value))
}

/// `--data-urlencode <name>=<body>` as a config line, for bodies that may hold secrets.
pub fn data_urlencode_line(name: &str, body: &str) -> String {
    format!("data-urlencode = \"{}={}\"\n", curl_quote(name), curl_quote(body))
}

/// One curl run with `config` on stdin. `args` holds `--config -` and nothing secret.
pub fn run_curl(args: &[String], config: &str) -> Result<Output> {
    run_program(Path::new("curl"), args, config)
}

fn run_program(curl: &Path, args: &[String], config: &str) -> Result<Output> {
    debug_assert!(
        args.windows(2).any(|w| w[0] == "--config" && w[1] == "-"),
        "curl reads its credentials from stdin"
    );
    let mut child = Command::new(curl)
        .args(args)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|e| Error::CommandFailed {
            cmd: "curl".into(),
            source: e.into(),
        })?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin
            .write_all(config.as_bytes())
            .map_err(|e| Error::Config(format!("curl stdin: {e}")))?;
    }
    child.wait_with_output().map_err(|e| Error::CommandFailed {
        cmd: "curl".into(),
        source: e.into(),
    })
}

/// A private (0600) cookie file at an unpredictable path, removed however the call ends.
pub struct CookieJar(PathBuf);

impl CookieJar {
    pub fn new() -> Result<Self> {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        let path = std::env::temp_dir().join(format!(
            "mac-k3d-jenkins-{}-{nanos}.cookies",
            std::process::id()
        ));
        let mut opts = std::fs::OpenOptions::new();
        opts.write(true).create_new(true);
        #[cfg(unix)]
        {
            use std::os::unix::fs::OpenOptionsExt;
            opts.mode(0o600);
        }
        opts.open(&path)
            .map_err(|e| Error::Config(format!("create {}: {e}", path.display())))?;
        Ok(Self(path))
    }

    pub fn path(&self) -> &Path {
        &self.0
    }
}

impl Drop for CookieJar {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.0);
    }
}

/// Whether `GET <base>/api/json` succeeds with these credentials.
pub fn reachable(base: &str, user: &str, token: &str) -> bool {
    let url = format!("{}/api/json", base.trim_end_matches('/'));
    let args = strings(&["-fsS", "--max-time", "30", "-o", "/dev/null", "--config", "-", &url]);
    run_curl(&args, &auth_line(user, token))
        .map(|o| o.status.success())
        .unwrap_or(false)
}

/// Calls to one controller with the same credentials, cookie jar and, when the
/// controller issues one, the CSRF crumb that belongs to that session.
pub struct Session {
    curl: PathBuf,
    base: String,
    auth: String,
    crumb: Option<String>,
    jar: CookieJar,
}

/// What a POST returned: the HTTP code (empty when curl got none) and the body.
pub struct Reply {
    pub code: String,
    pub body: String,
}

impl Reply {
    pub fn is(&self, codes: &[&str]) -> bool {
        codes.contains(&self.code.as_str())
    }
}

impl Session {
    pub fn open(base: &str, user: &str, token: &str) -> Result<Self> {
        let mut session = Self {
            curl: PathBuf::from("curl"),
            base: base.trim_end_matches('/').to_string(),
            auth: auth_line(user, token),
            crumb: None,
            jar: CookieJar::new()?,
        };
        session.crumb = session.fetch_crumb();
        Ok(session)
    }

    pub fn base(&self) -> &str {
        &self.base
    }

    fn url(&self, path: &str) -> String {
        format!("{}{path}", self.base)
    }

    /// curl arguments: `--config -` and the cookie jar first, then `rest`.
    fn args(&self, rest: &[&str]) -> Vec<String> {
        let jar = self.jar.path().display().to_string();
        let mut args = strings(&["--config", "-", "-b", &jar, "-c", &jar]);
        args.extend(rest.iter().map(|s| s.to_string()));
        args
    }

    fn config(&self, extra: &str) -> String {
        format!("{}{}{extra}", self.auth, self.crumb.as_deref().unwrap_or(""))
    }

    fn run(&self, rest: &[&str], extra_config: &str) -> Result<Output> {
        run_program(&self.curl, &self.args(rest), &self.config(extra_config))
    }

    fn fetch_crumb(&self) -> Option<String> {
        let out = run_program(
            &self.curl,
            &self.args(&["-fsS", &self.url("/crumbIssuer/api/json")]),
            &self.auth,
        )
        .ok()
        .filter(|o| o.status.success())?;
        let v: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
        Some(header_line(v["crumbRequestField"].as_str()?, v["crumb"].as_str()?))
    }

    /// HTTP status of `GET <path>`, None when curl itself failed.
    pub fn status(&self, path: &str) -> Option<u16> {
        let out = self
            .run(&["-sS", "-o", "/dev/null", "-w", "%{http_code}", &self.url(path)], "")
            .ok()?;
        String::from_utf8_lossy(&out.stdout).trim().parse().ok()
    }

    /// Body of `GET <path>`, None on an HTTP error.
    pub fn get(&self, path: &str) -> Option<String> {
        let out = self.run(&["-fsS", &self.url(path)], "").ok()?;
        out.status
            .success()
            .then(|| String::from_utf8_lossy(&out.stdout).to_string())
    }

    /// `POST <path>` with an XML body (node or job config: no secrets).
    pub fn post_xml(&self, path: &str, content_type: &str, xml: &str) -> Result<Reply> {
        let header = format!("Content-Type: {content_type}");
        let out = self.run(
            &[
                "-sS", "-H", &header, "-X", "POST", &self.url(path), "--data-binary", xml, "-w",
                "\n%{http_code}",
            ],
            "",
        )?;
        Ok(split_reply(&String::from_utf8_lossy(&out.stdout)))
    }

    /// `POST <path>` without a body (`doDelete`).
    pub fn post(&self, path: &str) -> Result<Reply> {
        let out = self.run(
            &["-sS", "-o", "/dev/null", "-X", "POST", &self.url(path), "-w", "\n%{http_code}"],
            "",
        )?;
        Ok(split_reply(&String::from_utf8_lossy(&out.stdout)))
    }

    /// Run a Groovy script through `/scriptText`. The script, which may embed a
    /// credential, goes on stdin with the API token.
    pub fn script_text(&self, script: &str) -> Result<Output> {
        self.run(
            &["-sS", "-X", "POST", &self.url("/scriptText")],
            &data_urlencode_line("script", script),
        )
    }
}

/// `<body>\n<code>` from `-w '\n%{http_code}'`.
fn split_reply(raw: &str) -> Reply {
    match raw.rsplit_once('\n') {
        Some((body, code)) => Reply {
            code: code.trim().to_string(),
            body: body.to_string(),
        },
        None => Reply {
            code: raw.trim().to_string(),
            body: String::new(),
        },
    }
}

fn strings(args: &[&str]) -> Vec<String> {
    args.iter().map(|s| s.to_string()).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    const TOKEN: &str = "11abcdef0123456789abcdef0123456789";

    fn session() -> Session {
        Session {
            curl: PathBuf::from("curl"),
            base: "http://ctl:17070".into(),
            auth: auth_line("admin", TOKEN),
            crumb: Some(header_line("Jenkins-Crumb", "c0ffee")),
            jar: CookieJar::new().unwrap(),
        }
    }

    #[test]
    fn credentials_and_crumb_go_on_stdin_not_in_the_arguments() {
        let s = session();
        let args = s.args(&["-sS", &s.url("/computer/n/api/json")]);
        assert!(args.windows(2).any(|w| w[0] == "--config" && w[1] == "-"));
        for arg in &args {
            assert!(!arg.contains(TOKEN) && !arg.contains("admin") && !arg.contains("c0ffee"), "{arg}");
        }
        let config = s.config(&data_urlencode_line("script", "println('x')"));
        assert!(config.starts_with(&format!("user = \"admin:{TOKEN}\"\n")), "{config}");
        assert!(config.contains("header = \"Jenkins-Crumb: c0ffee\"\n"), "{config}");
        assert!(config.ends_with("data-urlencode = \"script=println('x')\"\n"), "{config}");
    }

    #[test]
    fn quoting_round_trips_through_curls_config_parser_rules() {
        assert_eq!(auth_line("a\"b", "c\\d"), "user = \"a\\\"b:c\\\\d\"\n");
        let script = "def s = new String(\"YWJj\")\n\tprintln(s) // \\ \r\nend";
        let line = data_urlencode_line("script", script);
        assert_eq!(line.lines().count(), 1, "{line}");
        // curl's unescaping: \\ \" \n \r \t.
        let inner = line
            .strip_prefix("data-urlencode = \"script=")
            .and_then(|s| s.strip_suffix("\"\n"))
            .unwrap();
        let mut out = String::new();
        let mut chars = inner.chars();
        while let Some(c) = chars.next() {
            if c == '\\' {
                out.push(match chars.next().unwrap() {
                    'n' => '\n',
                    'r' => '\r',
                    't' => '\t',
                    other => other,
                });
            } else {
                assert_ne!(c, '"', "an unescaped quote ends the string early");
                out.push(c);
            }
        }
        assert_eq!(out, script);
    }

    /// Accept one request on `listener`; return its header block and body.
    fn serve_once(listener: std::net::TcpListener) -> (String, Vec<u8>) {
        use std::io::{BufRead, BufReader, Read};
        let (mut stream, _) = listener.accept().unwrap();
        let mut reader = BufReader::new(stream.try_clone().unwrap());
        let mut headers = String::new();
        loop {
            let mut line = String::new();
            reader.read_line(&mut line).unwrap();
            if line == "\r\n" || line.is_empty() {
                break;
            }
            headers.push_str(&line);
        }
        let length = headers
            .lines()
            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap()))
            .unwrap_or(0);
        let mut body = vec![0; length];
        reader.read_exact(&mut body).unwrap();
        stream
            .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\nok")
            .unwrap();
        (headers, body)
    }

    /// Decode a form body the way Jenkins' servlet container does.
    fn form_decode(s: &[u8]) -> String {
        let mut out = Vec::new();
        let mut i = 0;
        while i < s.len() {
            match s[i] {
                b'%' => {
                    out.push(u8::from_str_radix(std::str::from_utf8(&s[i + 1..i + 3]).unwrap(), 16).unwrap());
                    i += 3;
                }
                b'+' => {
                    out.push(b' ');
                    i += 1;
                }
                byte => {
                    out.push(byte);
                    i += 1;
                }
            }
        }
        String::from_utf8(out).unwrap()
    }

    #[test]
    fn real_curl_sends_the_script_and_credentials_it_read_from_stdin() {
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://127.0.0.1:{}/scriptText", listener.local_addr().unwrap().port());
        let server = std::thread::spawn(move || serve_once(listener));
        let script = "def s = new String('YW\"Jj')\n\tprintln(s) // \\ 100% & = + \r\nend";
        let config = format!(
            "{}{}{}",
            auth_line("admin", "tok"),
            header_line("Jenkins-Crumb", "c0ffee"),
            data_urlencode_line("script", script)
        );
        let args = strings(&["-sS", "--max-time", "20", "--config", "-", "-X", "POST", &url]);
        let out = run_curl(&args, &config).unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let (headers, body) = server.join().unwrap();
        // base64("admin:tok")
        assert!(headers.contains("Authorization: Basic YWRtaW46dG9r\r\n"), "{headers}");
        assert!(headers.contains("Jenkins-Crumb: c0ffee\r\n"), "{headers}");
        let body = String::from_utf8(body).unwrap();
        let value = body.strip_prefix("script=").expect("one script= field");
        assert_eq!(form_decode(value.as_bytes()), script);
    }

    #[cfg(unix)]
    #[test]
    fn no_session_call_puts_a_credential_crumb_or_script_in_curls_arguments() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("mac-k3d-fake-curl-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let fake = dir.join("curl");
        std::fs::write(
            &fake,
            "#!/bin/sh\n\
             d=$(dirname \"$0\")\n\
             n=$(ls \"$d\" | grep -c '^argv\\.')\n\
             printf '%s\\0' \"$@\" > \"$d/argv.$n\"\n\
             cat > \"$d/stdin.$n\"\n\
             printf '\\n200'\n",
        )
        .unwrap();
        std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut s = session();
        s.curl = fake;
        let encoded_secret = "c2VjcmV0LWtleQ==";

        let _ = s.fetch_crumb();
        let _ = s.status("/computer/n/api/json");
        let _ = s.get("/computer/n/slave-agent.jnlp");
        s.post_xml("/createItem?name=n", "application/xml", "<slave/>").unwrap();
        s.post("/job/j/doDelete").unwrap();
        s.script_text(&format!("new String('{encoded_secret}'.decodeBase64())")).unwrap();

        let read = |name: String| std::fs::read_to_string(dir.join(name)).unwrap();
        for call in 0..6 {
            let argv = read(format!("argv.{call}"));
            let args: Vec<&str> = argv.split('\0').filter(|a| !a.is_empty()).collect();
            assert!(args.windows(2).any(|w| w == ["--config", "-"]), "call {call}: {args:?}");
            for arg in &args {
                assert!(
                    !arg.contains(TOKEN)
                        && !arg.contains("admin:")
                        && !arg.contains("c0ffee")
                        && !arg.contains(encoded_secret)
                        && *arg != "-u"
                        && !arg.starts_with("script="),
                    "call {call} has a secret in its arguments: {arg}"
                );
            }
            let stdin = read(format!("stdin.{call}"));
            assert!(stdin.starts_with(&format!("user = \"admin:{TOKEN}\"\n")), "call {call}");
        }
        assert!(!dir.join("argv.6").exists());
        assert!(read("stdin.5".into()).contains(&format!("data-urlencode = \"script=new String('{encoded_secret}'")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_prepare_source_passes_curl_credentials_or_a_script_as_arguments() {
        fn scan(dir: &Path, hits: &mut Vec<String>) {
            for entry in std::fs::read_dir(dir).unwrap() {
                let path = entry.unwrap().path();
                if path.is_dir() {
                    scan(&path, hits);
                } else if path.extension().is_some_and(|e| e == "rs") {
                    let text = std::fs::read_to_string(&path).unwrap();
                    for (n, line) in text.lines().enumerate() {
                        if line.contains("\"-u\",") || line.contains("script={") || line.contains("\"script=") {
                            hits.push(format!("{}:{}: {}", path.display(), n + 1, line.trim()));
                        }
                    }
                }
            }
        }
        let mut hits = Vec::new();
        scan(&Path::new(env!("CARGO_MANIFEST_DIR")).join("src/prepare"), &mut hits);
        assert!(hits.is_empty(), "use jenkins_curl::Session instead:\n{}", hits.join("\n"));
    }

    #[test]
    fn replies_split_into_body_and_code() {
        let r = split_reply("<html>bad</html>\n400");
        assert_eq!((r.code.as_str(), r.body.as_str()), ("400", "<html>bad</html>"));
        assert!(split_reply("\n204").is(&["200", "204"]));
        assert_eq!(split_reply("").code, "");
    }

    #[test]
    fn the_cookie_jar_is_private_and_removed() {
        let jar = CookieJar::new().unwrap();
        let path = jar.path().to_path_buf();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = std::fs::metadata(&path).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o600);
        }
        drop(jar);
        assert!(!path.exists());
    }
}
