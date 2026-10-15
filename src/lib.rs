pub mod build_info;
pub mod cli;
pub mod commands;
pub mod config;
pub mod error;
pub mod eval_catalog;
pub mod jenkins_curl;
pub mod platform;
pub mod prepare;
pub mod runtime;

pub use cli::Cli;
pub use config::MacK3dConfig;
pub use error::{Error, Result};

#[cfg(test)]
pub(crate) mod test_support {
    use std::sync::{Mutex, MutexGuard, OnceLock};

    /// Hold this in any test that touches process-global state: an environment
    /// variable, or an ephemeral port it releases and expects to still be free.
    /// Run in parallel, such tests overwrite each other's value or take each
    /// other's port, which shows up as a rare, unrelated-looking failure.
    pub fn global_state() -> MutexGuard<'static, ()> {
        static LOCK: OnceLock<Mutex<()>> = OnceLock::new();
        LOCK.get_or_init(|| Mutex::new(()))
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
    }
}
