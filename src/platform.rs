use crate::error::{Error, Result};

/// macOS-only guard used by commands that require Docker Desktop.
pub fn ensure_macos() -> Result<()> {
    if !cfg!(target_os = "macos") {
        return Err(Error::UnsupportedPlatform);
    }
    Ok(())
}
