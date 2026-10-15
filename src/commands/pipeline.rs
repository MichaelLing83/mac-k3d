use std::path::PathBuf;

use clap::Args;

use crate::error::Result;
use crate::prepare::eval_assets;

#[derive(Debug, Args)]
pub struct PipelineArgs {
    /// Write this binary's pipeline/ (plus pipeline/BUILD.json) under DIR
    #[arg(long, value_name = "DIR")]
    pub extract_to: PathBuf,

    /// Fail unless the binary was built from a clean, known commit. Every Jenkins build passes this.
    #[arg(long)]
    pub require_clean: bool,
}

pub fn run(args: PipelineArgs) -> Result<()> {
    eval_assets::extract_to(&args.extract_to, args.require_clean)?;
    Ok(())
}
