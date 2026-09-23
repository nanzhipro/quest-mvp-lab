//! Process entry point. All logic lives in the library so it can be tested;
//! this file only turns a failure into a diagnosable exit code.
//!
//! Exit codes: `0` clean shutdown, `2` startup failure (root not resolvable,
//! not a directory) — stdout carries protocol bytes only, diagnostics go to
//! stderr.

use std::process::ExitCode;

#[tokio::main]
async fn main() -> ExitCode {
    match readonly_fs_mcp::cli::run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("readonly-fs-mcp: {error:#}");
            ExitCode::from(2)
        }
    }
}
