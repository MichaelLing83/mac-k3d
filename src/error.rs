use thiserror::Error;

pub type Result<T> = std::result::Result<T, Error>;

#[derive(Debug, Error)]
pub enum Error {
    #[error("configuration error: {0}")]
    Config(String),

    #[error("dependency not found: {0}")]
    DependencyMissing(String),

    #[error("validation failed: {0}")]
    Validation(String),

    #[error("setup cancelled by user")]
    Cancelled,

    #[error("platform not supported: macOS required")]
    UnsupportedPlatform,

    #[error("command failed: {cmd}")]
    CommandFailed {
        cmd: String,
        #[source]
        source: anyhow::Error,
    },

    #[error(transparent)]
    Other(#[from] anyhow::Error),
}
