//! Embed `pipeline/` in the Release binary and extract it to the user share dir.

use std::path::{Path, PathBuf};

use include_dir::{include_dir, Dir};
use serde::Serialize;

use crate::build_info::BuildInfo;
use crate::error::{Error, Result};

static PIPELINE: Dir<'_> = include_dir!("$CARGO_MANIFEST_DIR/pipeline");

const RUN_ALL: &str = "pipeline/stages/run_all.sh";
/// The phases `run_all.sh` runs, in order (its PHASES array). Each is
/// `pipeline/stages/<phase>.sh` and one Jenkins stage.
pub const PHASES: [&str; 7] = ["env", "tasks", "evaluate", "anticheat", "score", "report", "archive"];
/// Written next to the extracted scripts; `provenance.py` reads it into the artifact.
const BUILD_JSON: &str = "pipeline/BUILD.json";

#[derive(Serialize)]
struct BuildRecord<'a> {
    #[serde(flatten)]
    info: &'a BuildInfo,
    binary: String,
}

fn current_binary() -> String {
    std::env::current_exe()
        .map(|p| p.display().to_string())
        .unwrap_or_else(|_| "unknown".into())
}

pub fn run_all_rel() -> &'static str {
    RUN_ALL
}

pub fn looks_like_root(path: &Path) -> bool {
    path.join(RUN_ALL).is_file()
}

pub fn share_dir() -> PathBuf {
    if let Ok(p) = std::env::var("MAC_K3D_SHARE") {
        let t = p.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    let home = std::env::var_os("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("/tmp"));
    if let Ok(xdg) = std::env::var("XDG_DATA_HOME") {
        let t = xdg.trim();
        if !t.is_empty() {
            return PathBuf::from(t).join("mac-k3d");
        }
    }
    home.join(".local/share/mac-k3d")
}

pub fn icode_drop_hint(share: &Path) -> String {
    format!(
        "Place icode or icode-<os>-<arch>-full-vX.Y.Z (.tar.gz, same name, or unpacked folder) in {} (or /opt/mac-k3d/)",
        share.display()
    )
}

fn dir_has_named_icode(dir: &Path) -> bool {
    fn walk(d: &Path, depth: u32) -> bool {
        if depth > 6 {
            return false;
        }
        let Ok(rd) = std::fs::read_dir(d) else {
            return false;
        };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_file() && p.file_name().and_then(|s| s.to_str()) == Some("icode") {
                return true;
            }
            if p.is_dir() && walk(&p, depth + 1) {
                return true;
            }
        }
        false
    }
    dir.join("icode").is_file() || walk(dir, 0)
}

/// File named `icode`, archive, `*-full-*` file, or `*-full-*` directory with `icode` inside.
pub fn looks_like_icode_release(path: &Path) -> bool {
    let name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
    if path.is_dir() {
        return name.contains("-full-") && dir_has_named_icode(path);
    }
    if !path.is_file() {
        return false;
    }
    name == "icode"
        || name.ends_with(".tar.gz")
        || name.ends_with(".tgz")
        || name.contains("-full-")
}

fn first_full_drop(dir: &Path) -> Option<PathBuf> {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return None;
    };
    let mut found: Vec<PathBuf> = rd
        .flatten()
        .map(|e| e.path())
        .filter(|p| {
            let name = p.file_name().and_then(|s| s.to_str()).unwrap_or("");
            if name == "icode" || name == "pipeline" {
                return false;
            }
            looks_like_icode_release(p)
                && (name.contains("-full-") || name.ends_with(".tar.gz") || name.ends_with(".tgz"))
        })
        .collect();
    found.sort();
    found.into_iter().next()
}

/// Search one directory: official `*-full-*` / archives first, then a file named `icode`.
pub fn discover_icode_release_in(dir: &Path) -> Option<PathBuf> {
    if let Some(p) = first_full_drop(dir) {
        return Some(p);
    }
    let named = dir.join("icode");
    if looks_like_icode_release(&named) {
        return Some(named);
    }
    None
}

/// Share dir first (`MAC_K3D_SHARE`), then `/opt/mac-k3d`.
pub fn discover_icode_release() -> Option<PathBuf> {
    let share = share_dir();
    if let Some(p) = discover_icode_release_in(&share) {
        return Some(p);
    }
    discover_icode_release_in(Path::new("/opt/mac-k3d"))
}

/// Extract embedded pipeline/ into `root/pipeline/` without touching `root/icode`.
pub fn extract_pipeline(root: &Path) -> Result<()> {
    extract_pipeline_with(root, &BuildInfo::current())
}

fn extract_pipeline_with(root: &Path, info: &BuildInfo) -> Result<()> {
    let dest = root.join("pipeline");
    std::fs::create_dir_all(&dest)
        .map_err(|e| Error::Config(format!("cannot create {}: {e}", dest.display())))?;
    PIPELINE.extract(&dest).map_err(|e| {
        Error::Config(format!(
            "failed to extract pipeline assets to {}: {e}",
            dest.display()
        ))
    })?;
    let record = BuildRecord {
        info,
        binary: current_binary(),
    };
    let path = root.join(BUILD_JSON);
    let body = serde_json::to_string_pretty(&record)
        .map_err(|e| Error::Config(format!("cannot encode {}: {e}", path.display())))?;
    std::fs::write(&path, body + "\n")
        .map_err(|e| Error::Config(format!("cannot write {}: {e}", path.display())))?;
    Ok(())
}

/// `mac-k3d pipeline --extract-to DIR`: a Jenkins build's own copy of the
/// scripts this binary was built with.
pub fn extract_to(root: &Path, require_clean: bool) -> Result<BuildInfo> {
    let info = BuildInfo::current();
    extract_to_with(root, require_clean, &info)?;
    Ok(info)
}

fn extract_to_with(root: &Path, require_clean: bool, info: &BuildInfo) -> Result<()> {
    if require_clean && !info.is_clean() {
        return Err(Error::Config(format!(
            "this mac-k3d binary was built from {} and every Jenkins build needs one clean commit. \
             Commit, push and run scripts/redeploy.sh, then rebuild.",
            describe_commit(info)
        )));
    }
    extract_pipeline_with(root, info)?;
    if !looks_like_root(root) {
        return Err(Error::Config(format!(
            "pipeline extract missing {RUN_ALL} under {}",
            root.display()
        )));
    }
    println!(
        "mac-k3d pipeline {} (mac-k3d {}, {})",
        describe_commit(info),
        info.version,
        current_binary()
    );
    Ok(())
}

fn describe_commit(info: &BuildInfo) -> String {
    match info.dirty {
        Some(false) => info.commit.clone(),
        Some(true) => format!("{} plus uncommitted changes", info.commit),
        None => format!("{} (dirty state unknown)", info.commit),
    }
}

pub fn ensure_share_pipeline() -> Result<PathBuf> {
    let share = share_dir();
    std::fs::create_dir_all(&share)
        .map_err(|e| Error::Config(format!("cannot create {}: {e}", share.display())))?;
    extract_pipeline(&share)?;
    if !looks_like_root(&share) {
        return Err(Error::Config(format!(
            "pipeline extract missing {RUN_ALL} under {}",
            share.display()
        )));
    }
    Ok(share)
}

fn icode_drop_present(share: &Path) -> bool {
    discover_icode_release_in(share).is_some()
        || discover_icode_release_in(Path::new("/opt/mac-k3d")).is_some()
}

/// Extract `pipeline/` into the share dir and print the path (plus an iCode drop hint if missing).
/// Never writes into `share/icode`.
pub fn ensure_share_pipeline_reported() -> Result<PathBuf> {
    let share = ensure_share_pipeline()?;
    let pipeline = share.join("pipeline");
    if icode_drop_present(&share) {
        println!("Extracted pipeline to {}", pipeline.display());
    } else {
        println!(
            "Extracted pipeline to {}. {}",
            pipeline.display(),
            icode_drop_hint(&share)
        );
    }
    Ok(share)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn phases_match_run_all_and_are_embedded() {
        let run_all = PIPELINE
            .get_file("stages/run_all.sh")
            .and_then(|f| f.contents_utf8())
            .unwrap();
        let line = format!("PHASES=({})", PHASES.join(" "));
        assert!(run_all.contains(&line), "run_all.sh must define {line}");
        for phase in PHASES {
            assert!(
                PIPELINE.get_file(format!("stages/{phase}.sh")).is_some(),
                "missing pipeline/stages/{phase}.sh"
            );
        }
    }

    #[test]
    fn embedded_pipeline_has_run_all() {
        assert!(
            PIPELINE.get_file("stages/run_all.sh").is_some(),
            "pipeline/stages/run_all.sh must be embedded"
        );
        assert!(
            PIPELINE.get_file("stages/evaluate/harbor_cmd.sh").is_some(),
            "phase steps under pipeline/stages/<phase>/ must be embedded"
        );
        assert!(PIPELINE.get_file("lib/icode_harbor_agent.py").is_some());
        assert!(PIPELINE.get_file("config/network-allowlist-v1.json").is_some());
        assert!(PIPELINE.get_file("lib/icode_pier_agent.py").is_none());
        assert!(
            PIPELINE.get_file("lib/openai_compat.py").is_some(),
            "pipeline/lib/openai_compat.py must be embedded"
        );
        assert!(
            PIPELINE.get_file("lib/icode_input.sh").is_some(),
            "pipeline/lib/icode_input.sh must be embedded"
        );
        assert!(
            PIPELINE.get_file("lib/swebenchpro_run.py").is_some(),
            "pipeline/lib/swebenchpro_run.py must be embedded"
        );
        assert!(
            PIPELINE.get_file("lib/swebenchpro_tasks.py").is_some(),
            "pipeline/lib/swebenchpro_tasks.py must be embedded"
        );
        // The harness store and JiuwenSwarm, which every worker runs from this binary.
        for file in [
            "stages/tasks/harness.sh",
            "tools/prepare_harness.sh",
            "lib/harness.py",
            "lib/harness_store.py",
            "lib/harness_lib.sh",
            "lib/harness_git.sh",
            "lib/harness_labels.py",
            "lib/jiuwenswarm_input.sh",
            "lib/jiuwenswarm_build.sh",
            "lib/jiuwenswarm_harbor_agent.py",
            "lib/jiuwenswarm_trial.py",
            "lib/jiuwenswarm_usage.py",
        ] {
            assert!(PIPELINE.get_file(file).is_some(), "pipeline/{file} must be embedded");
        }
    }

    #[test]
    fn openai_compat_parses_list_json_without_network() {
        let lib = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("pipeline/lib");
        let output = std::process::Command::new("python3")
            .current_dir(&lib)
            .args([
                "-c",
                "from openai_compat import parse_model_ids, model_in_ids, missing_model_message\n\
ids = parse_model_ids({'object':'list','data':[{'id':'deepseek-flash'},{'id':'deepseek-v4-pro'}]})\n\
assert ids == ['deepseek-flash', 'deepseek-v4-pro']\n\
assert model_in_ids('deepseek-flash', ids)\n\
assert not model_in_ids('deepseek-v4.1-flash', ids)\n\
msg = missing_model_message('deepseek-v4.1-flash', ids)\n\
assert \"is not returned by GET /models\" in msg\n\
print('ok')\n",
            ])
            .output()
            .expect("python3");
        assert!(
            output.status.success(),
            "stderr={} stdout={}",
            String::from_utf8_lossy(&output.stderr),
            String::from_utf8_lossy(&output.stdout)
        );
    }

    #[test]
    fn extract_pipeline_does_not_touch_icode() {
        let root =
            std::env::temp_dir().join(format!("mac-k3d-extract-icode-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        std::fs::create_dir_all(&root).unwrap();
        let icode = root.join("icode");
        std::fs::write(&icode, b"keep-me").unwrap();
        extract_pipeline(&root).unwrap();
        assert_eq!(std::fs::read(&icode).unwrap(), b"keep-me");
        assert!(looks_like_root(&root));
        let _ = std::fs::remove_dir_all(&root);
    }

    fn fake_build(dirty: Option<bool>, commit: &str) -> BuildInfo {
        BuildInfo {
            version: "9.9.9".into(),
            commit: commit.into(),
            dirty,
            pipeline_hash: "00000000deadbeef".into(),
        }
    }

    #[test]
    fn extract_writes_build_json() {
        let tmp = tempfile::tempdir().unwrap();
        let info = fake_build(Some(false), "abc123");
        extract_to_with(tmp.path(), true, &info).unwrap();
        assert!(looks_like_root(tmp.path()));
        let raw = std::fs::read_to_string(tmp.path().join(BUILD_JSON)).unwrap();
        let doc: serde_json::Value = serde_json::from_str(&raw).unwrap();
        assert_eq!(doc["version"], "9.9.9");
        assert_eq!(doc["commit"], "abc123");
        assert_eq!(doc["dirty"], false);
        assert_eq!(doc["pipeline_hash"], "00000000deadbeef");
        assert!(!doc["binary"].as_str().unwrap().is_empty());
    }

    #[test]
    fn require_clean_refuses_dirty_build() {
        for info in [
            fake_build(Some(true), "abc123"),
            fake_build(None, "abc123"),
            fake_build(Some(false), "unknown"),
        ] {
            let tmp = tempfile::tempdir().unwrap();
            let err = extract_to_with(tmp.path(), true, &info).unwrap_err();
            assert!(err.to_string().contains("clean commit"), "{err}");
            assert!(!tmp.path().join(BUILD_JSON).exists());
            extract_to_with(tmp.path(), false, &info).unwrap();
            assert!(tmp.path().join(BUILD_JSON).is_file());
        }
    }

    #[test]
    fn share_dir_honors_mac_k3d_share() {
        let _serial = crate::test_support::global_state();
        let prev = std::env::var_os("MAC_K3D_SHARE");
        std::env::set_var("MAC_K3D_SHARE", "/tmp/mac-k3d-share-test");
        let got = share_dir();
        match prev {
            Some(v) => std::env::set_var("MAC_K3D_SHARE", v),
            None => std::env::remove_var("MAC_K3D_SHARE"),
        }
        assert_eq!(got, PathBuf::from("/tmp/mac-k3d-share-test"));
    }

    #[test]
    fn discover_official_full_release_in_share() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-full-drop-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let tar = dir.join("icode-linux-x86_64-full-v0.1.41.tar.gz");
        std::fs::write(&tar, b"not-a-real-archive").unwrap();
        let got = discover_icode_release_in(&dir).expect("find *-full-*.tar.gz");
        assert_eq!(got, tar);

        let bare_dir = dir.join("icode-linux-x86_64-full-v0.1.41");
        std::fs::remove_file(&tar).unwrap();
        std::fs::create_dir_all(&bare_dir).unwrap();
        std::fs::write(bare_dir.join("icode"), b"#!/bin/sh\n").unwrap();
        let got = discover_icode_release_in(&dir).expect("find unpacked *-full-* dir");
        assert_eq!(got, bare_dir);

        std::fs::remove_dir_all(&bare_dir).unwrap();
        std::fs::write(dir.join("notes.txt"), b"nope").unwrap();
        assert!(discover_icode_release_in(&dir).is_none());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn looks_like_official_full_name_without_suffix() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-full-bare-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let bare = dir.join("icode-linux-x86_64-full-v0.1.41");
        std::fs::write(&bare, b"\x1f\x8bgzip-stub").unwrap();
        assert!(looks_like_icode_release(&bare));
        assert!(!looks_like_icode_release(&dir.join("notes.txt")));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn discover_prefers_full_release_over_named_icode() {
        let dir =
            std::env::temp_dir().join(format!("mac-k3d-full-vs-icode-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("icode"), b"#!/bin/sh\n# leftover wrapper\n").unwrap();
        let tar = dir.join("icode-linux-x86_64-full-v0.1.41.tar.gz");
        std::fs::write(&tar, b"stub").unwrap();
        let got = discover_icode_release_in(&dir).expect("prefer *-full-*");
        assert_eq!(got, tar);
        let _ = std::fs::remove_dir_all(&dir);
    }
}
