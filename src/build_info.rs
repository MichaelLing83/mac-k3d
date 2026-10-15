//! What `build.rs` baked into this binary: the commit, whether the tree was
//! dirty, and a fingerprint of the embedded `pipeline/`.

use serde::Serialize;

pub const VERSION: &str = env!("CARGO_PKG_VERSION");
pub const COMMIT: &str = env!("MAC_K3D_GIT_COMMIT");
pub const PIPELINE_HASH: &str = env!("MAC_K3D_PIPELINE_HASH");
/// `0.5.3 (<commit>)` or `0.5.3 (<commit>, dirty)`; what `--version` prints.
pub const VERSION_LINE: &str = env!("MAC_K3D_VERSION_LINE");
const DIRTY: &str = env!("MAC_K3D_GIT_DIRTY");

#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct BuildInfo {
    pub version: String,
    pub commit: String,
    /// `None` when the binary was built outside a git checkout.
    pub dirty: Option<bool>,
    pub pipeline_hash: String,
}

impl BuildInfo {
    pub fn current() -> Self {
        Self {
            version: VERSION.into(),
            commit: COMMIT.into(),
            dirty: match DIRTY {
                "false" => Some(false),
                "true" => Some(true),
                _ => None,
            },
            pipeline_hash: PIPELINE_HASH.into(),
        }
    }

    /// Built from exactly one commit, so a result can name it.
    pub fn is_clean(&self) -> bool {
        self.dirty == Some(false) && self.commit_known()
    }

    pub fn commit_known(&self) -> bool {
        !self.commit.is_empty() && self.commit != "unknown"
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn version_string_has_commit() {
        let info = BuildInfo::current();
        assert!(VERSION_LINE.starts_with(VERSION), "{VERSION_LINE}");
        let short: String = info.commit.chars().take(12).collect();
        assert!(VERSION_LINE.contains(&format!("({short}")), "{VERSION_LINE}");
        assert_eq!(VERSION_LINE.contains("dirty"), info.dirty != Some(false));
        assert_eq!(info.pipeline_hash.len(), 16);
    }

    #[test]
    fn unknown_or_dirty_builds_are_not_clean() {
        let mut info = BuildInfo::current();
        info.commit = "abc".into();
        info.dirty = Some(false);
        assert!(info.is_clean());
        info.dirty = Some(true);
        assert!(!info.is_clean());
        info.dirty = None;
        assert!(!info.is_clean());
        info.dirty = Some(false);
        info.commit = "unknown".into();
        assert!(!info.is_clean());
    }
}
