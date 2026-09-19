//! Reclamation of kernel denials for one sandboxed run.
//!
//! `sandbox-exec` no longer reports violations on its stderr, and kernel sandbox
//! events are **not** delivered to `log stream` — they can only be read back
//! with `log show`. Every generated profile embeds a unique `SC_SBX_<hex>`
//! marker in `(deny default (with message …))`, so one query recovers exactly
//! the denials of this run.
//!
//! `log show` costs a fixed ~0.7 s (store open), independent of the time window,
//! so the CLI performs at most two queries: one after the run, plus one retry
//! when a failed command produced no records yet (log indexing can lag).

use sandbox_core::violation::{self, Violation};
use std::process::Command;
use std::time::Duration;

pub const LOG_BINARY: &str = "/usr/bin/log";

#[derive(Debug, Default)]
pub struct ReclaimOutcome {
    pub violations: Vec<Violation>,
    /// Set when the query itself failed (e.g. the unified log is unavailable).
    pub error: Option<String>,
    pub queries: u32,
}

pub fn reclaim(
    tag: &str,
    window_secs: u64,
    attempts: u32,
    retry_delay: Duration,
) -> ReclaimOutcome {
    let attempts = attempts.max(1);
    let mut outcome = ReclaimOutcome::default();
    for attempt in 0..attempts {
        outcome.queries += 1;
        match query_once(tag, window_secs) {
            Ok(found) => {
                outcome.error = None;
                outcome.violations = found;
                if !outcome.violations.is_empty() {
                    return outcome;
                }
            }
            Err(error) => {
                outcome.error = Some(error);
                outcome.violations.clear();
            }
        }
        if attempt + 1 < attempts {
            std::thread::sleep(retry_delay);
        }
    }
    outcome
}

fn query_once(tag: &str, window_secs: u64) -> Result<Vec<Violation>, String> {
    let predicate =
        format!("subsystem == \"com.apple.sandbox.reporting\" AND eventMessage CONTAINS \"{tag}\"");
    let output = Command::new(LOG_BINARY)
        .arg("show")
        .arg("--last")
        .arg(format!("{}s", window_secs.max(1)))
        .arg("--style")
        .arg("ndjson")
        .arg("--predicate")
        .arg(predicate)
        .output()
        .map_err(|error| format!("cannot run {LOG_BINARY}: {error}"))?;

    if !output.status.success() {
        return Err(format!(
            "{LOG_BINARY} show exited with {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    violation::parse_log_output(&String::from_utf8_lossy(&output.stdout), tag)
}
