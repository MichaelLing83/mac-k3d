//! Bake the commit, a dirty flag and a fingerprint of `pipeline/` into the binary.
//!
//! Workers run the pipeline embedded in their installed binary, so a result can
//! only name the code that produced it if the binary carries that name itself.

use std::path::{Path, PathBuf};
use std::process::Command;

/// What ends up in the binary. Edits elsewhere (docs, scripts) do not make it dirty.
const BINARY_INPUTS: &[&str] = &["src", "pipeline", "Cargo.toml", "Cargo.lock", "build.rs"];

fn main() {
    let root = PathBuf::from(std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR"));

    let commit = git(&root, &["rev-parse", "HEAD"]).unwrap_or_else(|| "unknown".into());
    // --no-optional-locks: a plain `git status` may rewrite .git/index, which is
    // watched below and would rerun this script on every build.
    let mut status = vec!["--no-optional-locks", "status", "--porcelain", "--"];
    status.extend_from_slice(BINARY_INPUTS);
    let dirty = match git(&root, &status) {
        Some(out) if out.is_empty() => "false",
        Some(_) => "true",
        None => "unknown",
    };

    let mut files = Vec::new();
    collect_files(&root.join("pipeline"), &mut files);
    files.sort();
    let mut hash = Fnv64::new();
    for path in &files {
        println!("cargo:rerun-if-changed={}", path.display());
        let rel = path.strip_prefix(&root).unwrap_or(path);
        hash.write(rel.to_string_lossy().replace('\\', "/").as_bytes());
        hash.write(&[0]);
        hash.write(&std::fs::read(path).unwrap_or_default());
        hash.write(&[0]);
    }

    for input in ["src", "Cargo.toml", "Cargo.lock", "build.rs"] {
        println!("cargo:rerun-if-changed={input}");
    }
    if let Some(git_dir) = git(&root, &["rev-parse", "--git-dir"]) {
        let git_dir = root.join(git_dir);
        for name in ["HEAD", "logs/HEAD", "index"] {
            let path = git_dir.join(name);
            if path.exists() {
                println!("cargo:rerun-if-changed={}", path.display());
            }
        }
    }

    let short: String = commit.chars().take(12).collect();
    let version = std::env::var("CARGO_PKG_VERSION").unwrap_or_default();
    let line = match dirty {
        "false" => format!("{version} ({short})"),
        "true" => format!("{version} ({short}, dirty)"),
        _ => format!("{version} ({short}, dirty unknown)"),
    };
    println!("cargo:rustc-env=MAC_K3D_GIT_COMMIT={commit}");
    println!("cargo:rustc-env=MAC_K3D_GIT_DIRTY={dirty}");
    println!("cargo:rustc-env=MAC_K3D_PIPELINE_HASH={:016x}", hash.finish());
    println!("cargo:rustc-env=MAC_K3D_VERSION_LINE={line}");
}

fn git(root: &Path, args: &[&str]) -> Option<String> {
    let out = Command::new("git").current_dir(root).args(args).output().ok()?;
    if !out.status.success() {
        return None;
    }
    Some(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Every file under `dir`, minus Python bytecode caches that tests write there.
fn collect_files(dir: &Path, out: &mut Vec<PathBuf>) {
    let Ok(entries) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        let name = entry.file_name();
        let name = name.to_string_lossy();
        if path.is_dir() {
            if name != "__pycache__" {
                collect_files(&path, out);
            }
        } else if !name.ends_with(".pyc") {
            out.push(path);
        }
    }
}

/// FNV-1a, 64-bit: a stable fingerprint without a build dependency.
struct Fnv64(u64);

impl Fnv64 {
    fn new() -> Self {
        Self(0xcbf2_9ce4_8422_2325)
    }

    fn write(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 ^= u64::from(*byte);
            self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }

    fn finish(&self) -> u64 {
        self.0
    }
}
