//! Command line: the working directory the server may read, and the per-call
//! budgets. The root is resolved and confined once, at startup.

use std::path::PathBuf;

use anyhow::Context;
use clap::Parser;

use crate::server::{FsServer, SERVER_NAME};
use crate::workspace::{Limits, Workspace};

#[derive(Debug, Parser)]
#[command(
    name = "readonly-fs-mcp",
    version,
    about = "Read-only, workspace-confined filesystem MCP server (stdio)",
    long_about = "Serves three read-only MCP tools (list_directory, read_file, file_metadata) over stdin/stdout, \
                  confined to one workspace directory. It opens no socket and holds no write path. \
                  Configure it as a stdio MCP server, passing --root <DIR>."
)]
pub struct Args {
    /// Working directory the server may read. Resolved and confined at startup;
    /// defaults to the current directory.
    #[arg(long, value_name = "DIR")]
    pub root: Option<PathBuf>,

    /// Hard cap on the lines one `read_file` call may return.
    #[arg(long, value_name = "N", default_value_t = 2000)]
    pub max_read_lines: u64,

    /// Hard cap on the entries one `list_directory` call may return.
    #[arg(long, value_name = "N", default_value_t = 2000)]
    pub max_entries: u64,

    /// Hard cap on the recursion depth one `list_directory` call may use.
    #[arg(long, value_name = "N", default_value_t = 8)]
    pub max_depth: u32,

    /// Byte budget for the text one `read_file` call may return.
    #[arg(long, value_name = "BYTES", default_value_t = 524_288)]
    pub max_read_bytes: usize,

    /// Files up to this size are read to the end so a read reports an exact
    /// `total_lines`.
    #[arg(long, value_name = "BYTES", default_value_t = 8_388_608)]
    pub max_scan_bytes: u64,
}

impl Args {
    /// Budgets, with zero-valued flags lifted to the smallest usable value.
    pub fn limits(&self) -> Limits {
        Limits {
            max_read_lines: self.max_read_lines.max(1),
            max_entries: self.max_entries.max(1),
            max_depth: self.max_depth.max(1),
            max_read_bytes: self.max_read_bytes.max(1),
            max_scan_bytes: self.max_scan_bytes.max(1),
        }
    }
}

/// Open the workspace the arguments ask for, or explain why they cannot be met.
pub fn open_workspace(args: &Args) -> anyhow::Result<Workspace> {
    let requested = args.root.clone().unwrap_or_else(|| PathBuf::from("."));
    Workspace::open(&requested, args.limits())
        .with_context(|| format!("cannot use `{}` as the workspace root", requested.display()))
}

/// Parse the arguments and serve until the client closes the pipe.
pub async fn run() -> anyhow::Result<()> {
    run_with(Args::parse()).await
}

pub async fn run_with(args: Args) -> anyhow::Result<()> {
    let workspace = open_workspace(&args)?;
    // Startup diagnostic on stderr only: stdout carries protocol bytes alone.
    eprintln!(
        "{SERVER_NAME} {} · workspace {} · read-only · stdio",
        env!("CARGO_PKG_VERSION"),
        workspace.root().display()
    );
    FsServer::new(workspace).serve_stdio().await
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;
    use tempfile::TempDir;

    #[test]
    fn root_defaults_to_the_current_directory() {
        let args = Args::try_parse_from(["readonly-fs-mcp"]).expect("parse");
        assert!(args.root.is_none());
        assert_eq!(args.limits(), Limits::default());
    }

    #[test]
    fn flags_reach_the_workspace_and_the_limits() {
        let args = Args::try_parse_from([
            "readonly-fs-mcp",
            "--root",
            "/tmp",
            "--max-read-lines",
            "50",
            "--max-entries",
            "10",
            "--max-depth",
            "3",
            "--max-read-bytes",
            "1024",
            "--max-scan-bytes",
            "4096",
        ])
        .expect("parse");
        assert_eq!(args.root.as_deref(), Some(std::path::Path::new("/tmp")));
        assert_eq!(
            args.limits(),
            Limits {
                max_read_lines: 50,
                max_entries: 10,
                max_depth: 3,
                max_read_bytes: 1024,
                max_scan_bytes: 4096,
            }
        );
    }

    #[test]
    fn zero_budgets_are_lifted_to_one() {
        let args = Args::try_parse_from([
            "readonly-fs-mcp",
            "--max-read-lines",
            "0",
            "--max-entries",
            "0",
            "--max-depth",
            "0",
            "--max-read-bytes",
            "0",
            "--max-scan-bytes",
            "0",
        ])
        .expect("parse");
        let limits = args.limits();
        assert_eq!(
            (
                limits.max_read_lines,
                limits.max_entries,
                limits.max_depth,
                limits.max_read_bytes,
                limits.max_scan_bytes
            ),
            (1, 1, 1, 1, 1)
        );
    }

    #[test]
    fn unknown_flags_are_rejected() {
        assert!(Args::try_parse_from(["readonly-fs-mcp", "--allow-write"]).is_err());
    }

    #[test]
    fn open_workspace_reports_the_root_it_rejected() {
        let dir = TempDir::new().unwrap();
        fs::create_dir(dir.path().join("inside")).unwrap();

        let args = Args::try_parse_from(["readonly-fs-mcp", "--root"]).unwrap_err();
        let _ = args; // clap's own "missing value" error path

        let good = Args::try_parse_from([
            "readonly-fs-mcp",
            "--root",
            dir.path().join("inside").to_str().unwrap(),
        ])
        .unwrap();
        assert!(open_workspace(&good).unwrap().root().ends_with("inside"));

        let bad = Args::try_parse_from([
            "readonly-fs-mcp",
            "--root",
            dir.path().join("gone").to_str().unwrap(),
        ])
        .unwrap();
        let error = open_workspace(&bad).unwrap_err();
        assert!(error.to_string().contains("cannot use"));
        assert!(error.to_string().contains("gone"));
    }

    #[test]
    fn version_and_help_are_available() {
        let help = Args::try_parse_from(["readonly-fs-mcp", "--help"]).unwrap_err();
        assert_eq!(help.kind(), clap::error::ErrorKind::DisplayHelp);
        let version = Args::try_parse_from(["readonly-fs-mcp", "--version"]).unwrap_err();
        assert_eq!(version.kind(), clap::error::ErrorKind::DisplayVersion);
    }
}
