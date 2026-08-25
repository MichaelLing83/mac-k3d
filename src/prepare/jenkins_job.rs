use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::Duration;

use crate::error::{Error, Result};

pub const LOLBENCH_ONE_TASK: &str = "lolbench_one_task";

/// Ensure Pipeline job `lolbench_one_task` exists on the controller (create or update).
///
/// Uses admin basic auth (typically the chart-generated password). When the job already
/// exists, refreshes the inline Pipeline definition so Jenkinsfile fixes ship on `config`.
pub fn ensure_lolbench_one_task(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    lolbench_git_url: &str,
) -> Result<()> {
    let base = jenkins_url.trim_end_matches('/');
    let auth = format!("{api_user}:{api_token_or_password}");

    wait_for_jenkins(base, &auth, Duration::from_secs(90))?;

    let cookie_file = tempfile_path("mac-k3d-job-cookies")?;
    let crumb = fetch_crumb(base, &auth, &cookie_file);

    let exists = curl_status(
        base,
        &auth,
        &format!("/job/{LOLBENCH_ONE_TASK}/api/json"),
        &crumb,
        &cookie_file,
    )
    .map(|c| c == 200)
    .unwrap_or(false);

    if exists {
        println!("Updating Jenkins job '{LOLBENCH_ONE_TASK}' Pipeline definition…");
        match update_job_script(base, &auth, &crumb, &cookie_file, lolbench_git_url) {
            Ok(()) => println!("Updated job '{LOLBENCH_ONE_TASK}'."),
            Err(err) => println!(
                "Warning: failed to update job '{LOLBENCH_ONE_TASK}' ({err}).\n\
                 Delete the job in the UI and re-run `mac-k3d config`, or see docs/lolbench-jenkins.md."
            ),
        }
        let _ = std::fs::remove_file(&cookie_file);
        return Ok(());
    }

    let xml = job_config_xml(lolbench_git_url);
    let create_url = format!(
        "{base}/createItem?name={}",
        urlencoding_simple(LOLBENCH_ONE_TASK)
    );
    println!("Creating Jenkins job '{LOLBENCH_ONE_TASK}' on {base} …");

    let mut cmd = Command::new("curl");
    cmd.args([
        "-sS",
        "-b",
        &cookie_file.display().to_string(),
        "-c",
        &cookie_file.display().to_string(),
        "-u",
        &auth,
        "-H",
        "Content-Type: text/xml",
        "-X",
        "POST",
        &create_url,
        "--data-binary",
        &xml,
        "-w",
        "\n%{http_code}",
    ]);
    if let Some((field, value)) = &crumb {
        cmd.args(["-H", &format!("{field}: {value}")]);
    }

    let output = cmd.output().map_err(|e| Error::CommandFailed {
        cmd: "curl createItem lolbench_one_task".into(),
        source: e.into(),
    })?;
    let _ = std::fs::remove_file(&cookie_file);

    let raw = String::from_utf8_lossy(&output.stdout);
    let code = raw.lines().last().unwrap_or("").trim().to_string();
    if code != "200" && code != "201" && code != "302" && code != "303" {
        let body: String = raw
            .lines()
            .rev()
            .skip(1)
            .collect::<Vec<_>>()
            .into_iter()
            .rev()
            .collect::<Vec<_>>()
            .join("\n");
        let snippet: String = body.chars().take(280).collect();
        println!(
            "Warning: failed to create job '{LOLBENCH_ONE_TASK}' (HTTP {code}). {snippet}\n\
             Create it manually — see docs/lolbench-jenkins.md."
        );
        return Ok(());
    }

    println!(
        "Created job '{LOLBENCH_ONE_TASK}'.\n\
         Trigger: {base}/job/{LOLBENCH_ONE_TASK}/buildWithParameters\n\
         Add secret-text credentials (openrouter-api-key, …) or export keys on the agent for non-oracle runs."
    );
    Ok(())
}

/// Replace the WorkflowJob inline script via Script Console (more reliable than config.xml POST).
fn update_job_script(
    base: &str,
    auth: &str,
    crumb: &Option<(String, String)>,
    cookie_file: &Path,
    lolbench_git_url: &str,
) -> Result<()> {
    let script = jenkinsfile(lolbench_git_url);
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

    let mut cmd = Command::new("curl");
    cmd.args([
        "-sS",
        "-b",
        &cookie_file.display().to_string(),
        "-c",
        &cookie_file.display().to_string(),
        "-u",
        auth,
        "-X",
        "POST",
        &format!("{base}/scriptText"),
        "--data-urlencode",
        &format!("script={groovy}"),
    ]);
    if let Some((field, value)) = crumb {
        cmd.args(["-H", &format!("{field}: {value}")]);
    }
    let output = cmd.output().map_err(|e| Error::CommandFailed {
        cmd: "curl scriptText update job".into(),
        source: e.into(),
    })?;
    let body = String::from_utf8_lossy(&output.stdout).to_string();
    if !output.status.success() || !body.contains("updated-script") {
        return Err(Error::Config(truncate(&body, 300)));
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

/// Create the job using the in-cluster Jenkins admin password from kubectl.
pub async fn ensure_lolbench_one_task_from_cluster(
    kubectl: &Path,
    config: &crate::config::MacK3dConfig,
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
    let git_url = if config.lolbench.git_url.trim().is_empty() {
        crate::config::MacK3dConfig::default().lolbench.git_url
    } else {
        config.lolbench.git_url.clone()
    };
    ensure_lolbench_one_task(&url, "admin", &password, &git_url)
}

fn wait_for_jenkins(base: &str, auth: &str, timeout: Duration) -> Result<()> {
    let start = std::time::Instant::now();
    loop {
        let cookie = tempfile_path("mac-k3d-job-wait")?;
        let ok = Command::new("curl")
            .args([
                "-fsS",
                "-o",
                "/dev/null",
                "-u",
                auth,
                "-b",
                &cookie.display().to_string(),
                "-c",
                &cookie.display().to_string(),
                &format!("{base}/api/json"),
            ])
            .status()
            .map(|s| s.success())
            .unwrap_or(false);
        let _ = std::fs::remove_file(&cookie);
        if ok {
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

fn job_config_xml(lolbench_git_url: &str) -> String {
    let script = jenkinsfile(lolbench_git_url);
    let git_xml = xml_escape(lolbench_git_url.trim());
    format!(
        r#"<?xml version='1.1' encoding='UTF-8'?>
<flow-definition plugin="workflow-job">
  <description>One LoLBench task per build (created by mac-k3d). See docs/lolbench-jenkins.md.</description>
  <keepDependencies>false</keepDependencies>
  <properties>
    <hudson.model.ParametersDefinitionProperty>
      <parameterDefinitions>
        <hudson.model.StringParameterDefinition>
          <name>TASK</name>
          <description>LoLBench task id (harbor_tasks/TASK)</description>
          <defaultValue>ruff_1</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.ChoiceParameterDefinition>
          <name>HARNESS</name>
          <description>Harbor agent</description>
          <choices class="java.util.Arrays$ArrayList">
            <a class="string-array">
              <string>opencode</string>
              <string>codex</string>
              <string>claude-code</string>
              <string>oracle</string>
              <string>nop</string>
            </a>
          </choices>
        </hudson.model.ChoiceParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>MODEL</name>
          <description>provider/model (ignored for oracle/nop)</description>
          <defaultValue>openrouter/deepseek/deepseek-v4-pro</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.ChoiceParameterDefinition>
          <name>SUITE</name>
          <choices class="java.util.Arrays$ArrayList">
            <a class="string-array">
              <string>union</string>
              <string>orig</string>
              <string>aug</string>
            </a>
          </choices>
        </hudson.model.ChoiceParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>AGENT_LABEL</name>
          <defaultValue>lolbench</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>MAX_RETRIES</name>
          <defaultValue>2</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>CPU_LOCK_QTY</name>
          <description>CPU_CORES lock quantity</description>
          <defaultValue>4</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>LOLBENCH_GIT_URL</name>
          <defaultValue>{git_xml}</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
        <hudson.model.StringParameterDefinition>
          <name>LOLBENCH_GIT_REF</name>
          <defaultValue>main</defaultValue>
          <trim>true</trim>
        </hudson.model.StringParameterDefinition>
      </parameterDefinitions>
    </hudson.model.ParametersDefinitionProperty>
  </properties>
  <definition class="org.jenkinsci.plugins.workflow.cps.CpsFlowDefinition" plugin="workflow-cps">
    <script><![CDATA[{script}]]></script>
    <sandbox>true</sandbox>
  </definition>
  <triggers/>
  <disabled>false</disabled>
</flow-definition>
"#
    )
}

fn xml_escape(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
        .replace('\'', "&apos;")
}

/// Declarative Pipeline embedded in the job (inline; no SCM required for the job itself).
fn jenkinsfile(lolbench_git_url: &str) -> String {
    // Escape nothing special inside CDATA except "]]>" which we avoid.
    let git = lolbench_git_url.trim();
    format!(
        r#"pipeline {{
  agent {{ label params.AGENT_LABEL }}

  options {{
    timeout(time: 7, unit: 'HOURS')
  }}

  parameters {{
    string(name: 'TASK', defaultValue: 'ruff_1', description: 'LoLBench task id (harbor_tasks/<TASK>)')
    choice(name: 'HARNESS', choices: ['opencode', 'codex', 'claude-code', 'oracle', 'nop'], description: 'Harbor agent')
    string(name: 'MODEL', defaultValue: 'openrouter/deepseek/deepseek-v4-pro', description: 'provider/model (ignored for oracle/nop)')
    choice(name: 'SUITE', choices: ['union', 'orig', 'aug'])
    string(name: 'AGENT_LABEL', defaultValue: 'lolbench')
    string(name: 'MAX_RETRIES', defaultValue: '2')
    string(name: 'CPU_LOCK_QTY', defaultValue: '4', description: 'CPU_CORES lock quantity')
    string(name: 'LOLBENCH_GIT_URL', defaultValue: '{git}')
    string(name: 'LOLBENCH_GIT_REF', defaultValue: 'main')
  }}

  environment {{
    JOBS_DIR = "harbor_runs/jenkins-${{env.BUILD_NUMBER}}/${{params.TASK}}"
    HARBOR_JOB_NAME = "${{params.TASK}}_${{params.HARNESS}}_${{params.SUITE}}_${{env.BUILD_NUMBER}}"
  }}

  stages {{
    stage('Checkout') {{
      steps {{
        checkout([
          $class: 'GitSCM',
          branches: [[name: "*/${{params.LOLBENCH_GIT_REF}}"]],
          userRemoteConfigs: [[url: params.LOLBENCH_GIT_URL]],
          extensions: [[$class: 'CloneOption', shallow: true, depth: 1, noTags: true]]
        ])
      }}
    }}
    stage('Evaluate') {{
      steps {{
        // resource: null — Declarative requires the DataBound field even when locking by label.
        lock(label: 'CPU_CORES', quantity: params.CPU_LOCK_QTY as Integer, resource: null) {{
          sh '''
            set -euo pipefail
            # LaunchAgent PATH can be minimal; keep Docker/Harbor visible in the build shell.
            export PATH="/usr/local/bin:/opt/homebrew/bin:${{HOME}}/.local/bin:${{HOME}}/homebrew/bin:/Applications/Docker.app/Contents/Resources/bin:${{PATH}}"
            test -d "harbor_tasks/${{TASK}}"
            command -v docker >/dev/null
            command -v harbor >/dev/null
            docker info >/dev/null
            harbor --version

            mkdir -p "${{JOBS_DIR}}"
            model_args=()
            if [ "$HARNESS" != oracle ] && [ "$HARNESS" != nop ]; then
              model_args=(-m "$MODEL")
            fi
            extra=()
            [ "$MAX_RETRIES" != "0" ] && extra+=(--max-retries "$MAX_RETRIES")

            harbor run \
              -p "harbor_tasks/${{TASK}}" \
              -a "${{HARNESS}}" ${{model_args[@]+"${{model_args[@]}}"}} \
              --job-name "${{HARBOR_JOB_NAME}}" \
              --jobs-dir "${{JOBS_DIR}}" \
              --no-delete -n 1 -y \
              --ve "LOLBENCH_SUITE=${{SUITE}}" \
              "${{extra[@]}}"
          '''
        }}
      }}
    }}
    stage('Report') {{
      steps {{
        sh '''
          python3 - <<'PY'
import json, glob, os, pathlib
jobs_dir = os.environ["JOBS_DIR"]
paths = glob.glob(f"{{jobs_dir}}/**/verifier/reward.json", recursive=True)
if not paths:
    raise SystemExit(f"no reward.json under {{jobs_dir}}")
data = json.load(open(paths[0]))
reward = data.get("reward")
pathlib.Path("lolbench.properties").write_text(f"REWARD={{reward}}\n")
print(data)
PY
        '''
        script {{
          def line = readFile('lolbench.properties').trim()
          def reward = line.contains('=') ? line.split('=', 2)[1].trim() : line
          currentBuild.description = "${{params.TASK}} | ${{params.HARNESS}} | ${{params.MODEL}} | reward=${{reward}}"
          if (reward != '1.0') {{
            currentBuild.result = 'UNSTABLE'
          }}
        }}
      }}
    }}
  }}

  post {{
    always {{
      archiveArtifacts artifacts: "${{env.JOBS_DIR}}/**/verifier/*.json", allowEmptyArchive: true
    }}
  }}
}}
"#,
        git = git.replace('\\', "\\\\").replace('\'', "\\'")
    )
}

fn tempfile_path(prefix: &str) -> Result<PathBuf> {
    let path = std::env::temp_dir().join(format!("{prefix}-{}", std::process::id()));
    std::fs::File::create(&path).map_err(|e| Error::Config(e.to_string()))?;
    Ok(path)
}

fn fetch_crumb(base: &str, auth: &str, cookie_file: &Path) -> Option<(String, String)> {
    let output = Command::new("curl")
        .args([
            "-fsS",
            "-b",
            &cookie_file.display().to_string(),
            "-c",
            &cookie_file.display().to_string(),
            "-u",
            auth,
            &format!("{base}/crumbIssuer/api/json"),
        ])
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&output.stdout);
    let field = extract_json_str(&text, "crumbRequestField")?;
    let crumb = extract_json_str(&text, "crumb")?;
    Some((field, crumb))
}

fn curl_status(
    base: &str,
    auth: &str,
    path: &str,
    crumb: &Option<(String, String)>,
    cookie_file: &Path,
) -> Option<i32> {
    let mut args = vec![
        "-sS".into(),
        "-o".into(),
        "/dev/null".into(),
        "-w".into(),
        "%{http_code}".into(),
        "-b".into(),
        cookie_file.display().to_string(),
        "-c".into(),
        cookie_file.display().to_string(),
        "-u".into(),
        auth.to_string(),
        format!("{base}{path}"),
    ];
    if let Some((field, value)) = crumb {
        args.push("-H".into());
        args.push(format!("{field}: {value}"));
    }
    let output = Command::new("curl").args(&args).output().ok()?;
    String::from_utf8_lossy(&output.stdout).trim().parse().ok()
}

fn extract_json_str(json: &str, key: &str) -> Option<String> {
    let needle = format!("\"{key}\"");
    let idx = json.find(&needle)?;
    let after = &json[idx + needle.len()..];
    let colon = after.find(':')?;
    let rest = after[colon + 1..].trim_start();
    if !rest.starts_with('"') {
        return None;
    }
    let rest = &rest[1..];
    let end = rest.find('"')?;
    Some(rest[..end].to_string())
}

fn urlencoding_simple(s: &str) -> String {
    s.chars()
        .map(|c| match c {
            'A'..='Z' | 'a'..='z' | '0'..='9' | '-' | '_' | '.' | '~' => c.to_string(),
            _ => format!("%{:02X}", c as u8),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn jenkinsfile_contains_isolation_and_lock() {
        let jf = jenkinsfile("https://github.com/example/LoLBench-Preview.git");
        assert!(jf.contains("harbor_runs/jenkins-"));
        assert!(jf.contains("lock(label: 'CPU_CORES'"));
        assert!(jf.contains("resource: null"));
        assert!(!jf.contains("timestamps()"));
        assert!(jf.contains("LOLBENCH_GIT_URL"));
        assert!(jf.contains("https://github.com/example/LoLBench-Preview.git"));
        assert!(!jf.contains("credentials('openrouter-api-key')"));
    }

    #[test]
    fn job_xml_wraps_pipeline_in_cdata() {
        let xml = job_config_xml("https://example.com/lolbench.git");
        assert!(xml.contains("<![CDATA["));
        assert!(xml.contains("flow-definition"));
        assert!(xml.contains("ParametersDefinitionProperty"));
        assert!(xml.contains("<name>TASK</name>"));
        assert!(xml.contains("https://example.com/lolbench.git"));
        assert!(xml.contains("pipeline {"));
    }

    #[test]
    fn jenkinsfile_python_fstring_is_valid() {
        let jf = jenkinsfile("https://example.com/x.git");
        assert!(jf.contains(r#"glob.glob(f"{jobs_dir}/**/verifier/reward.json""#));
        assert!(jf.contains(r#"f"REWARD={reward}\n""#));
    }

    #[test]
    fn base64_roundtrip_ascii() {
        let s = "hello pipeline";
        let enc = base64_encode(s.as_bytes());
        assert_eq!(enc, "aGVsbG8gcGlwZWxpbmU=");
    }
}
