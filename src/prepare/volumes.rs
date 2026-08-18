use std::path::{Path, PathBuf};

use crate::error::Result;

/// A mounted volume with available space.
#[derive(Debug, Clone)]
pub struct VolumeCandidate {
    pub mount_point: PathBuf,
    pub available_bytes: u64,
    pub suggested_base: PathBuf,
}

/// Scan mounted volumes and rank by free space.
pub fn scan_volumes() -> Result<Vec<VolumeCandidate>> {
    let mut candidates = Vec::new();
    let mut seen_bases = std::collections::HashSet::new();

    let home = std::env::var_os("HOME").map(PathBuf::from);
    if let Some(home) = home {
        let mount = PathBuf::from("/");
        let suggested = home.join("mac-k3d");
        if seen_bases.insert(suggested.clone()) {
            candidates.push(VolumeCandidate {
                mount_point: mount.clone(),
                available_bytes: available_bytes(&mount).unwrap_or(0),
                suggested_base: suggested,
            });
        }
    }

    if let Ok(entries) = std::fs::read_dir("/Volumes") {
        for entry in entries.flatten() {
            let mount = entry.path();
            if !mount.is_dir() {
                continue;
            }
            let suggested = mount.join("mac-k3d");
            if !seen_bases.insert(suggested.clone()) {
                continue;
            }
            candidates.push(VolumeCandidate {
                mount_point: mount.clone(),
                available_bytes: available_bytes(&mount).unwrap_or(0),
                suggested_base: suggested,
            });
        }
    }

    candidates.sort_by(|a, b| b.available_bytes.cmp(&a.available_bytes));
    Ok(candidates)
}

impl VolumeCandidate {
    pub fn display_label(&self) -> String {
        format!(
            "{}  ({} free on {})",
            self.suggested_base.display(),
            format_bytes(self.available_bytes),
            self.mount_point.display()
        )
    }
}

pub fn available_bytes(path: &Path) -> Option<u64> {
    use std::ffi::CString;
    use std::os::unix::ffi::OsStrExt;

    let bytes = path.as_os_str().as_bytes();
    let c_path = CString::new(bytes).ok()?;
    let mut stat: libc::statfs = unsafe { std::mem::zeroed() };
    let rc = unsafe { libc::statfs(c_path.as_ptr(), &mut stat) };
    if rc == 0 {
        Some(stat.f_bavail as u64 * stat.f_bsize as u64)
    } else {
        None
    }
}

pub fn format_bytes(bytes: u64) -> String {
    const GB: u64 = 1024 * 1024 * 1024;
    const MB: u64 = 1024 * 1024;
    if bytes >= GB {
        format!("{:.1} GB", bytes as f64 / GB as f64)
    } else if bytes >= MB {
        format!("{:.0} MB", bytes / MB)
    } else if bytes > 0 {
        format!("{bytes} B")
    } else {
        "unknown".into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scan_volumes_returns_at_least_home() {
        let volumes = scan_volumes().unwrap();
        assert!(!volumes.is_empty());
    }

    #[test]
    fn available_bytes_for_root() {
        assert!(available_bytes(Path::new("/")).unwrap_or(0) > 0);
    }
}
