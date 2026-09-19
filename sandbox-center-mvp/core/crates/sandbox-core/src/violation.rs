//! Reclamation of kernel denials.
//!
//! On current macOS, `sandbox-exec` no longer prints violations to its own
//! stderr: they land in the unified log as
//!
//! ```text
//! Sandbox: zsh(42889) deny(1) file-write-unlink /private/tmp/ws/report.txt
//! SC_SBX_deadbeef
//! ```
//!
//! The first line carries actor, pid, operation and target; the second line is
//! the unique marker emitted by `(deny default (with message "<tag>"))`, which
//! is what lets the CLI attribute a denial to exactly one sandboxed run.

use crate::policy::OperationClass;
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct Violation {
    /// Process that triggered the denial (`zsh`, `rm`, `python3`, …).
    pub actor: String,
    pub pid: u32,
    /// Seatbelt operation, e.g. `file-write-unlink`, `file-write-data`, `network-outbound`.
    pub operation: String,
    /// What the operation was attempted on (path, mach service, address).
    pub target: String,
}

impl Violation {
    pub fn class(&self) -> OperationClass {
        let op = self.operation.as_str();
        if op.starts_with("file-write-unlink") {
            OperationClass::Delete
        } else if op.starts_with("file-write") {
            OperationClass::Write
        } else if op.starts_with("file-read") {
            OperationClass::Read
        } else if op.starts_with("network") {
            OperationClass::Network
        } else {
            OperationClass::Other
        }
    }

    /// `mach-lookup`, `sysctl-read`, … produce noise on every run and are not
    /// policy decisions; only file and network denials are reported by default.
    pub fn is_policy_relevant(&self) -> bool {
        self.class() != OperationClass::Other
    }

    pub fn summary(&self) -> String {
        format!(
            "{} {} {} (pid {})",
            self.actor, self.operation, self.target, self.pid
        )
    }
}

/// Parse a single `Sandbox: …` violation line.
pub fn parse_line(line: &str) -> Option<Violation> {
    let rest = line.strip_prefix("Sandbox: ")?;
    let open = rest.find('(')?;
    let actor = rest[..open].trim();
    if actor.is_empty() {
        return None;
    }
    let after = &rest[open + 1..];
    let close = after.find(')')?;
    let pid: u32 = after[..close].trim().parse().ok()?;

    let after = after[close + 1..].trim_start();
    let after = after.strip_prefix("deny(")?;
    let close = after.find(')')?;
    let _decision: u32 = after[..close].trim().parse().ok()?;

    let after = after[close + 1..].trim();
    let (operation, target) = match after.split_once(' ') {
        Some((operation, target)) => (operation, target.trim()),
        None => (after, ""),
    };
    if operation.is_empty() {
        return None;
    }
    Some(Violation {
        actor: actor.to_string(),
        pid,
        operation: operation.to_string(),
        target: target.to_string(),
    })
}

/// Parse `log show` output, accepting both `--style json` (one JSON array) and
/// `--style ndjson` (one JSON object per line). Only records that carry `tag`
/// and look like Seatbelt violations are kept.
pub fn parse_log_output(blob: &str, tag: &str) -> Result<Vec<Violation>, String> {
    let trimmed = blob.trim();
    if trimmed.is_empty() {
        return Ok(Vec::new());
    }

    let mut violations = Vec::new();
    if trimmed.starts_with('[') {
        let value: serde_json::Value =
            serde_json::from_str(trimmed).map_err(|e| format!("log output is not JSON: {e}"))?;
        let entries = value
            .as_array()
            .ok_or_else(|| "log output is not a JSON array".to_string())?;
        for entry in entries {
            collect_violation(entry, tag, &mut violations);
        }
        return Ok(dedup(violations));
    }

    for line in trimmed.lines() {
        let line = line.trim();
        if !line.starts_with('{') {
            // `log stream` prints a human-readable header before the records.
            continue;
        }
        if let Ok(entry) = serde_json::from_str::<serde_json::Value>(line) {
            collect_violation(&entry, tag, &mut violations);
        }
    }
    Ok(dedup(violations))
}

fn collect_violation(entry: &serde_json::Value, tag: &str, out: &mut Vec<Violation>) {
    let Some(message) = entry.get("eventMessage").and_then(|value| value.as_str()) else {
        return;
    };
    if !message.contains(tag) {
        return;
    }
    let first_line = message.lines().next().unwrap_or_default();
    if let Some(violation) = parse_line(first_line) {
        out.push(violation);
    }
}

/// Collapse repeats: a single command may retry the same denied syscall dozens
/// of times (`rm -r`, build tools, …).
pub fn dedup(violations: Vec<Violation>) -> Vec<Violation> {
    let mut sorted = violations;
    sorted.sort();
    sorted.dedup();
    sorted
}

/// Render violations as a compact, human-readable block for terminal output.
pub fn render_report(violations: &[Violation]) -> String {
    if violations.is_empty() {
        return String::new();
    }
    let mut out = String::from("[sandbox] kernel denials reclaimed:\n");
    for violation in violations {
        out.push_str(&format!(
            "  - {:<12} {:<22} {} (pid {})\n",
            format!("{:?}", violation.class()).to_lowercase(),
            violation.operation,
            violation.target,
            violation.pid
        ));
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const SAMPLE: &str = r#"[
      {"process":null,"subsystem":"com.apple.sandbox.reporting","category":"violation",
       "eventMessage":"Sandbox: zsh(42889) deny(1) file-write-unlink /private/tmp/ws/report.txt\nSC_SBX_deadbeef"},
      {"process":null,"subsystem":"com.apple.sandbox.reporting","category":"violation",
       "eventMessage":"Sandbox: rm(42890) deny(1) file-write-unlink /private/tmp/ws/report.txt\nSC_SBX_deadbeef"},
      {"process":null,"subsystem":"com.apple.sandbox.reporting","category":"violation",
       "eventMessage":"Sandbox: python3(1) deny(1) mach-lookup com.apple.diagnosticd\nSC_SBX_deadbeef"},
      {"eventMessage":"Sandbox: zsh(2) deny(1) file-write-data /other/path\nSC_SBX_ffffffff"},
      {"eventMessage":"log run noninteractively, parent: 1 (bash), args: 'log' 'show' '--predicate' 'SC_SBX_deadbeef'"},
      {"eventMessage":"Sandbox: zsh(3) allow(1) file-write-data /whatever\nSC_SBX_deadbeef"}
    ]"#;

    #[test]
    fn parses_violation_lines() {
        let violation = parse_line("Sandbox: zsh(42889) deny(1) file-write-unlink /tmp/x").unwrap();
        assert_eq!(violation.actor, "zsh");
        assert_eq!(violation.pid, 42889);
        assert_eq!(violation.operation, "file-write-unlink");
        assert_eq!(violation.target, "/tmp/x");
        assert_eq!(violation.class(), OperationClass::Delete);
        assert!(violation.is_policy_relevant());
    }

    #[test]
    fn ignores_non_violation_lines() {
        assert!(parse_line("Sandbox: zsh(1) allow(1) file-read* /x").is_none());
        assert!(parse_line("completely different").is_none());
        assert!(parse_line("Sandbox: zsh deny(1) file-read* /x").is_none());
    }

    #[test]
    fn parses_log_json_and_filters_by_tag() {
        let violations = parse_log_output(SAMPLE, "SC_SBX_deadbeef").unwrap();
        assert_eq!(violations.len(), 3);
        assert!(violations
            .iter()
            .all(|violation| violation.target != "/other/path"));
        assert!(violations
            .iter()
            .any(|v| v.class() == OperationClass::Other));
    }

    #[test]
    fn parses_ndjson_log_output_and_skips_headers() {
        let sample = concat!(
            "Filtering the log data using \"subsystem == \"com.apple.sandbox.reporting\"\"\n",
            "{\"eventMessage\":\"Sandbox: rm(7) deny(1) file-write-unlink /private/tmp/ws/x.txt\\nSC_SBX_deadbeef\"}\n",
            "not json at all\n",
            "{\"eventMessage\":\"Sandbox: nc(8) deny(1) network-outbound remote:*:80\\nSC_SBX_deadbeef\"}\n",
            "{\"eventMessage\":\"Sandbox: zsh(9) deny(1) file-write-data /elsewhere\\nSC_SBX_ffffffff\"}\n"
        );
        let violations = parse_log_output(sample, "SC_SBX_deadbeef").unwrap();
        assert_eq!(violations.len(), 2);
        // `dedup` sorts by (actor, pid, operation, target).
        assert!(violations
            .iter()
            .any(|v| v.class() == OperationClass::Delete));
        assert!(violations
            .iter()
            .any(|v| v.class() == OperationClass::Network));
    }

    #[test]
    fn empty_log_output_yields_no_violations() {
        assert!(parse_log_output("", "SC_SBX_deadbeef").unwrap().is_empty());
        assert!(parse_log_output("   \n", "SC_SBX_deadbeef")
            .unwrap()
            .is_empty());
    }

    #[test]
    fn dedups_repeated_denials() {
        let violations = parse_log_output(SAMPLE, "SC_SBX_deadbeef").unwrap();
        let unlink: Vec<_> = violations
            .iter()
            .filter(|v| v.operation == "file-write-unlink")
            .collect();
        // Same operation+target from two different actors stays distinct.
        assert_eq!(unlink.len(), 2);
    }

    #[test]
    fn classifies_operations() {
        let class = |op: &str| {
            parse_line(&format!("Sandbox: x(1) deny(1) {op} /t"))
                .unwrap()
                .class()
        };
        assert_eq!(class("file-write-data"), OperationClass::Write);
        assert_eq!(class("file-write-unlink"), OperationClass::Delete);
        assert_eq!(class("file-read-data"), OperationClass::Read);
        assert_eq!(class("network-outbound"), OperationClass::Network);
        assert_eq!(class("mach-lookup"), OperationClass::Other);
    }

    #[test]
    fn renders_report() {
        let violations = parse_log_output(SAMPLE, "SC_SBX_deadbeef").unwrap();
        let report = render_report(&violations);
        assert!(report.contains("kernel denials reclaimed"));
        assert!(report.contains("file-write-unlink"), "{report}");
        assert!(report.contains("/private/tmp/ws/report.txt"), "{report}");
        assert!(report.contains("delete"), "{report}");
    }
}
