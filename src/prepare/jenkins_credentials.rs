//! Jenkins Credentials (Secret text) for CI — configure once on the controller.
//!
//! Values may be collected at `prepare` into `credentials.pending.yaml` (mode 0600),
//! then created in Jenkins on `config`. They are never written to `config.yaml`.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::process::Command;

use dialoguer::{theme::ColorfulTheme, Confirm, Password};
use serde::{Deserialize, Serialize};

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};

/// Catalog of CI secrets mac-k3d can create in Jenkins.
pub struct CredDef {
    pub id: &'static str,
    pub env_var: &'static str,
    pub prompt: &'static str,
    pub env_key: &'static str,
}

pub const CREDENTIAL_DEFS: &[CredDef] = &[
    CredDef {
        id: "openrouter-api-key",
        env_var: "OPENROUTER_API_KEY",
        prompt: "OpenRouter API key (opencode / OpenRouter models)",
        env_key: "MAC_K3D_OPENROUTER_API_KEY",
    },
    CredDef {
        id: "deepseek-api-key",
        env_var: "DEEPSEEK_API_KEY",
        prompt: "DeepSeek API key (icode / dsh)",
        env_key: "MAC_K3D_DEEPSEEK_API_KEY",
    },
    CredDef {
        id: "openlux-api-key",
        env_var: "OPENLUX_API_KEY",
        prompt: "OpenLux API key",
        env_key: "MAC_K3D_OPENLUX_API_KEY",
    },
    CredDef {
        id: "openai-api-key",
        env_var: "OPENAI_API_KEY",
        prompt: "OpenAI API key",
        env_key: "MAC_K3D_OPENAI_API_KEY",
    },
    CredDef {
        id: "anthropic-api-key",
        env_var: "ANTHROPIC_API_KEY",
        prompt: "Anthropic API key",
        env_key: "MAC_K3D_ANTHROPIC_API_KEY",
    },
    CredDef {
        id: "gitcode-pat",
        env_var: "GITCODE_TOKEN",
        prompt: "GitCode PAT (icode install / private repos)",
        env_key: "MAC_K3D_GITCODE_PAT",
    },
    CredDef {
        id: "github-pat",
        env_var: "GITHUB_TOKEN",
        prompt: "GitHub PAT (private agent repos / gh)",
        env_key: "MAC_K3D_GITHUB_PAT",
    },
];

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct PendingCredentials {
    #[serde(default)]
    pub values: BTreeMap<String, String>,
}

impl PendingCredentials {
    pub fn path() -> PathBuf {
        MacK3dConfig::pending_credentials_path()
    }

    pub fn load() -> Result<Self> {
        let path = Self::path();
        if !path.exists() {
            return Ok(Self::default());
        }
        let text = std::fs::read_to_string(&path).map_err(|e| {
            Error::Config(format!("failed to read {}: {e}", path.display()))
        })?;
        // Accept either `{ values: { id: secret } }` or flat `{ id: secret }`.
        if let Ok(wrapped) = serde_yaml::from_str::<PendingCredentials>(&text) {
            if !wrapped.values.is_empty() || text.contains("values:") {
                return Ok(wrapped);
            }
        }
        let flat: BTreeMap<String, String> = serde_yaml::from_str(&text).map_err(|e| {
            Error::Config(format!("failed to parse {}: {e}", path.display()))
        })?;
        Ok(Self { values: flat })
    }

    pub fn save(&self) -> Result<()> {
        let path = Self::path();
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent).map_err(|e| {
                Error::Config(format!("failed to create {}: {e}", parent.display()))
            })?;
        }
        // Flat map is easier to edit by hand.
        let text = serde_yaml::to_string(&self.values)
            .map_err(|e| Error::Config(format!("serialize pending credentials: {e}")))?;
        std::fs::write(&path, text).map_err(|e| {
            Error::Config(format!("failed to write {}: {e}", path.display()))
        })?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mut perms = std::fs::metadata(&path)
                .map_err(|e| Error::Config(e.to_string()))?
                .permissions();
            perms.set_mode(0o600);
            std::fs::set_permissions(&path, perms)
                .map_err(|e| Error::Config(e.to_string()))?;
        }
        Ok(())
    }

    pub fn clear_ids(&mut self, ids: &[String]) -> Result<()> {
        for id in ids {
            self.values.remove(id);
        }
        if self.values.is_empty() {
            let path = Self::path();
            if path.exists() {
                let _ = std::fs::remove_file(&path);
            }
            Ok(())
        } else {
            self.save()
        }
    }
}

/// Interactive prepare: collect optional secrets into the pending file (not config.yaml).
pub fn prompt_pending_credentials() -> Result<()> {
    println!(
        "\nCI secrets (stored for Jenkins Credentials — not written to config.yaml).\n\
         Leave empty to skip. See docs/secrets.md.\n"
    );
    if !Confirm::with_theme(&ColorfulTheme::default())
        .with_prompt("Enter CI secrets now? (or skip and set them on first `mac-k3d config`)")
        .default(true)
        .interact()
        .map_err(|_| Error::Cancelled)?
    {
        println!("Skipping secret entry — `mac-k3d config` can prompt later.");
        return Ok(());
    }

    let mut pending = PendingCredentials::load().unwrap_or_default();
    for def in CREDENTIAL_DEFS {
        if let Ok(env_val) = std::env::var(def.env_key) {
            if !env_val.trim().is_empty() {
                pending.values.insert(def.id.to_string(), env_val);
                println!("  {} ← ${}", def.id, def.env_key);
                continue;
            }
        }
        let value: String = Password::with_theme(&ColorfulTheme::default())
            .with_prompt(format!("{} [{}]", def.prompt, def.id))
            .allow_empty_password(true)
            .interact()
            .map_err(|_| Error::Cancelled)?;
        if value.trim().is_empty() {
            continue;
        }
        pending.values.insert(def.id.to_string(), value);
    }
    if pending.values.is_empty() {
        println!("No secrets entered.");
        return Ok(());
    }
    pending.save()?;
    println!(
        "Saved {} pending credential(s) to {} (mode 0600).",
        pending.values.len(),
        PendingCredentials::path().display()
    );
    Ok(())
}

/// Ensure Jenkins Credentials exist: pending file + optional interactive fill + env fallbacks.
pub fn ensure_credentials_on_controller(
    jenkins_url: &str,
    api_user: &str,
    api_token_or_password: &str,
    force_prompt: bool,
) -> Result<Vec<String>> {
    let base = jenkins_url.trim_end_matches('/');
    let auth = format!("{api_user}:{api_token_or_password}");

    let existing = list_credential_ids(base, &auth).unwrap_or_default();
    let mut pending = PendingCredentials::load().unwrap_or_default();

    // Env fallbacks into pending.
    for def in CREDENTIAL_DEFS {
        if pending.values.contains_key(def.id) {
            continue;
        }
        if let Ok(v) = std::env::var(def.env_key) {
            if !v.trim().is_empty() {
                pending.values.insert(def.id.to_string(), v);
            }
        }
    }

    let interactive = atty::is(atty::Stream::Stdin);
    if interactive && (force_prompt || pending.values.is_empty()) {
        let missing: Vec<_> = CREDENTIAL_DEFS
            .iter()
            .filter(|d| {
                !existing.iter().any(|e| e == d.id) && !pending.values.contains_key(d.id)
            })
            .collect();
        if force_prompt || !missing.is_empty() {
            println!("\nJenkins Credentials (controller — used by all agents):");
            let ask = force_prompt
                || Confirm::with_theme(&ColorfulTheme::default())
                    .with_prompt("Create / update missing CI credentials in Jenkins now?")
                    .default(true)
                    .interact()
                    .unwrap_or(false);
            if ask {
                for def in CREDENTIAL_DEFS {
                    if !force_prompt
                        && existing.iter().any(|e| e == def.id)
                        && !pending.values.contains_key(def.id)
                    {
                        continue;
                    }
                    let value: String = Password::with_theme(&ColorfulTheme::default())
                        .with_prompt(format!(
                            "{} [{}]{}",
                            def.prompt,
                            def.id,
                            if existing.iter().any(|e| e == def.id) {
                                " (Enter keeps existing)"
                            } else {
                                ""
                            }
                        ))
                        .allow_empty_password(true)
                        .interact()
                        .map_err(|_| Error::Cancelled)?;
                    if !value.trim().is_empty() {
                        pending.values.insert(def.id.to_string(), value);
                    }
                }
            }
        }
    }

    let mut created_or_present = existing.clone();
    let mut uploaded = Vec::new();
    for (id, secret) in pending.values.clone() {
        if secret.trim().is_empty() {
            continue;
        }
        let desc = CREDENTIAL_DEFS
            .iter()
            .find(|d| d.id == id)
            .map(|d| d.prompt)
            .unwrap_or(id.as_str());
        match upsert_string_credential(base, &auth, &id, desc, &secret) {
            Ok(status) => {
                println!("Jenkins credential '{id}': {status}");
                if !created_or_present.contains(&id) {
                    created_or_present.push(id.clone());
                }
                uploaded.push(id);
            }
            Err(err) => {
                println!("Warning: could not create credential '{id}' ({err})");
            }
        }
    }
    if !uploaded.is_empty() {
        let _ = pending.clear_ids(&uploaded);
    }

    // Return IDs that exist in Jenkins after this pass (for job binding).
    let after = list_credential_ids(base, &auth).unwrap_or(created_or_present);
    Ok(after)
}

fn list_credential_ids(base: &str, auth: &str) -> Result<Vec<String>> {
    let groovy = r#"
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.common.StandardCredentials
import jenkins.model.Jenkins
def ids = CredentialsProvider.lookupCredentials(
  StandardCredentials.class, Jenkins.instance, null, null
).collect { it.id }
println('IDS:' + ids.join(','))
"#;
    let body = run_script_text(base, auth, groovy)?;
    for line in body.lines() {
        if let Some(rest) = line.strip_prefix("IDS:") {
            return Ok(rest
                .split(',')
                .map(str::trim)
                .filter(|s| !s.is_empty())
                .map(String::from)
                .collect());
        }
    }
    Ok(Vec::new())
}

fn upsert_string_credential(
    base: &str,
    auth: &str,
    id: &str,
    description: &str,
    secret: &str,
) -> Result<&'static str> {
    // Base64 the secret so Groovy/string escaping is safe.
    let b64 = base64_encode(secret.as_bytes());
    let id_esc = groovy_escape(id);
    let desc_esc = groovy_escape(description);
    let groovy = format!(
        r#"
import jenkins.model.Jenkins
import com.cloudbees.plugins.credentials.SystemCredentialsProvider
import com.cloudbees.plugins.credentials.CredentialsScope
import com.cloudbees.plugins.credentials.domains.Domain
import com.cloudbees.plugins.credentials.CredentialsProvider
import com.cloudbees.plugins.credentials.common.StandardCredentials
import org.jenkinsci.plugins.plaincredentials.impl.StringCredentialsImpl
import hudson.util.Secret
import java.util.Base64

def id = '{id}'
def desc = '{desc}'
def secret = new String(Base64.decoder.decode('{b64}'), 'UTF-8')
def store = SystemCredentialsProvider.getInstance().getStore()
def domain = Domain.global()
def existing = CredentialsProvider.lookupCredentials(
  StandardCredentials.class, Jenkins.instance, null, null
).find {{ it.id == id }}
def cred = new StringCredentialsImpl(CredentialsScope.GLOBAL, id, desc, Secret.fromString(secret))
if (existing != null) {{
  store.updateCredentials(domain, existing, cred)
  println('updated')
}} else {{
  store.addCredentials(domain, cred)
  println('created')
}}
"#,
        id = id_esc,
        desc = desc_esc,
        b64 = b64,
    );
    let body = run_script_text(base, auth, &groovy)?;
    if body.contains("created") {
        Ok("created")
    } else if body.contains("updated") {
        Ok("updated")
    } else {
        Err(Error::Config(truncate(&body, 240)))
    }
}

fn groovy_escape(s: &str) -> String {
    s.replace('\\', "\\\\").replace('\'', "\\'")
}

fn run_script_text(base: &str, auth: &str, script: &str) -> Result<String> {
    let cookie_file = tempfile_path("mac-k3d-cred-cookies")?;
    let crumb = fetch_crumb(base, auth, &cookie_file);
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
        &format!("script={script}"),
    ]);
    if let Some((field, value)) = &crumb {
        cmd.args(["-H", &format!("{field}: {value}")]);
    }
    let output = cmd.output().map_err(|e| Error::CommandFailed {
        cmd: "curl scriptText credentials".into(),
        source: e.into(),
    })?;
    let _ = std::fs::remove_file(&cookie_file);
    let body = String::from_utf8_lossy(&output.stdout).to_string();
    if !output.status.success() {
        return Err(Error::CommandFailed {
            cmd: "curl scriptText credentials".into(),
            source: anyhow::anyhow!("exit {:?} {}", output.status.code(), truncate(&body, 160)),
        });
    }
    if body.contains("unable to resolve class")
        || body.contains("Unauthorized")
        || body.contains("Forbidden")
    {
        return Err(Error::Config(truncate(&body, 300)));
    }
    Ok(body)
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

fn truncate(s: &str, max: usize) -> String {
    let t: String = s.chars().take(max).collect();
    if s.chars().count() > max {
        format!("{t}…")
    } else {
        t
    }
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn catalog_has_icode_secrets() {
        let ids: Vec<_> = CREDENTIAL_DEFS.iter().map(|d| d.id).collect();
        assert!(ids.contains(&"deepseek-api-key"));
        assert!(ids.contains(&"gitcode-pat"));
        assert!(ids.contains(&"openrouter-api-key"));
    }
}
