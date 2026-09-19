//! Shared logic for the sandbox-center MVP (WorkBuddy-style agent tool-call sandbox).
//!
//! The crate is deliberately dependency-light (serde / serde_json only) and holds the
//! three pieces both binaries must agree on:
//!
//! * [`protocol`] — the line-delimited JSON RPC spoken over the center's Unix socket.
//! * [`policy`] — the declarative policy file, placeholder expansion and the
//!   *effective* policy the center hands to a one-shot CLI.
//! * [`seatbelt`] — Seatbelt (SBPL) profile materialisation for `/usr/bin/sandbox-exec`.
//! * [`violation`] — reclamation and classification of kernel denial records
//!   (`Sandbox: <proc>(<pid>) deny(1) <op> <target>`).

pub mod ipc;
pub mod policy;
pub mod protocol;
pub mod seatbelt;
pub mod timefmt;
pub mod violation;

/// Crate version, surfaced in `hello`/`ping` responses.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

/// Every generated profile carries this message prefix so kernel denials can be
/// attributed to exactly one sandboxed run.
pub const TAG_PREFIX: &str = "SC_SBX_";
