//! Worker-local iCode release drop path (`~/.config/mac-k3d/icode-paths.yaml`).
//! Git clone URL/ref stay in env / Jenkins params, not this file.

use std::fs;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};

use crate::config::MacK3dConfig;
use crate::error::{Error, Result};

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct IcodePaths {
    #[serde(default, skip_serializing_if = "String::is_empty")]
    pub release: String,
}

pub fn path() -> PathBuf {
    if let Ok(p) = std::env::var("MAC_K3D_ICODE_PATHS") {
        let t = p.trim();
        if !t.is_empty() {
            return PathBuf::from(t);
        }
    }
    MacK3dConfig::config_dir().join("icode-paths.yaml")
}

pub fn load() -> IcodePaths {
    load_from(&path())
}

pub fn load_from(file: &Path) -> IcodePaths {
    let Ok(text) = fs::read_to_string(file) else {
        return IcodePaths::default();
    };
    serde_yaml::from_str(&text).unwrap_or_default()
}

pub fn save(paths: &IcodePaths) -> Result<()> {
    save_to(&path(), paths)
}

pub fn save_to(file: &Path, paths: &IcodePaths) -> Result<()> {
    if let Some(dir) = file.parent() {
        fs::create_dir_all(dir)
            .map_err(|e| Error::Config(format!("cannot create {}: {e}", dir.display())))?;
    }
    let text = serde_yaml::to_string(paths)
        .map_err(|e| Error::Config(format!("icode-paths.yaml: {e}")))?;
    fs::write(file, text)
        .map_err(|e| Error::Config(format!("cannot write {}: {e}", file.display())))?;
    Ok(())
}

pub fn set_release(raw: &str) -> Result<()> {
    let p = PathBuf::from(raw.trim());
    if !p.exists() {
        return Err(Error::Config(format!(
            "ICODE_RELEASE path does not exist: {}",
            p.display()
        )));
    }
    let abs = fs::canonicalize(&p).map_err(|e| Error::Config(format!("{}: {e}", p.display())))?;
    let mut cur = load();
    cur.release = abs.display().to_string();
    save(&cur)?;
    println!("Saved release path to {}", path().display());
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn roundtrip_tmp_file() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-icode-paths-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("icode-paths.yaml");
        let doc = IcodePaths {
            release: "/tmp/full".into(),
        };
        save_to(&file, &doc).unwrap();
        let got = load_from(&file);
        assert_eq!(got, doc);
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn load_ignores_legacy_source_key() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-icode-legacy-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let file = dir.join("icode-paths.yaml");
        fs::write(&file, "source: /tmp/iCode-main\nrelease: /tmp/full\n").unwrap();
        let got = load_from(&file);
        assert_eq!(got.release, "/tmp/full");
        let _ = fs::remove_dir_all(&dir);
    }

    #[test]
    fn set_release_writes_canonical_path() {
        let dir = std::env::temp_dir().join(format!("mac-k3d-icode-rel-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        let drop = dir.join("icode-linux-x86_64-full-v0.1.41");
        fs::write(&drop, b"stub").unwrap();
        let file = dir.join("icode-paths.yaml");
        let _serial = crate::test_support::global_state();
        let prev = std::env::var_os("MAC_K3D_ICODE_PATHS");
        std::env::set_var("MAC_K3D_ICODE_PATHS", &file);
        set_release(drop.to_str().unwrap()).unwrap();
        let got = load();
        match prev {
            Some(v) => std::env::set_var("MAC_K3D_ICODE_PATHS", v),
            None => std::env::remove_var("MAC_K3D_ICODE_PATHS"),
        }
        assert!(got.release.contains("icode-linux-x86_64-full-v0.1.41"));
        let _ = fs::remove_dir_all(&dir);
    }
}
