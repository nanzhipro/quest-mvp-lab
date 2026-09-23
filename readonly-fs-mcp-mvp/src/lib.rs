//! Read-only, workspace-confined filesystem access, exposed over MCP on stdio.
//!
//! The server has no listening socket: it speaks JSON-RPC over the stdin/stdout
//! pipes of the process that spawns it, so it is reachable only by that process.
//! Every path a caller supplies is resolved against a single workspace root and
//! must stay inside it; no code path opens a file for writing.
//!
//! Layering: [`workspace`] owns path resolution and confinement, [`tools`] owns
//! the tool contract (parameter/result types and their semantics), [`server`] is
//! the thin MCP binding, [`cli`] wires the process together.

pub mod cli;
pub mod error;
pub mod server;
pub mod tools;
pub mod workspace;

pub use error::FsError;
pub use server::FsServer;
pub use tools::{
    FileKind, FileMetadataOutput, FileMetadataParams, ListDirectoryOutput, ListDirectoryParams,
    ReadFileOutput, ReadFileParams,
};
pub use workspace::{Limits, Workspace};
