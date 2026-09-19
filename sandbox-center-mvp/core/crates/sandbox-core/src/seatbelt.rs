//! Seatbelt (SBPL) profile materialisation.
//!
//! The generated profile is what `/usr/bin/sandbox-exec -f <file>` enforces. Two
//! Seatbelt properties shape the output:
//!
//! 1. **Last matching rule wins.** Delete protection is therefore expressed as
//!    *broad write allow → global unlink deny → narrow unlink re-allow*, exactly
//!    like the reference implementation's template.
//! 2. **`(deny default (with message "<tag>"))` tags every denial.** The kernel
//!    log entry then carries a marker unique to one sandboxed run, which is how
//!    [`crate::violation`] attributes denials back to a session.

use crate::policy::{Access, EffectivePolicy, NetworkMode};
use crate::TAG_PREFIX;

/// Paths every sandboxed command must be able to write to stay usable
/// (shells write to their tty, tools open `/dev/null`).
const DEVICE_WRITE_ALLOW: &[&str] = &[
    "/dev/null",
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/tty",
    "/dev/dtracehelper",
    "/dev/stdout",
    "/dev/stderr",
];

/// Mach services a plain `zsh`/`python3`/`node` needs. Denials here are noise,
/// not policy decisions; keeping them out of the profile keeps the violation
/// stream meaningful.
const MACH_BASELINE: &[&str] = &[
    "com.apple.system.notification_center",
    "com.apple.system.opendirectoryd.libinfo",
    "com.apple.system.logger",
    "com.apple.logd",
    "com.apple.diagnosticd",
    "com.apple.cfprefsd.agent",
    "com.apple.cfprefsd.daemon",
    "com.apple.distributed_notifications@Uv3",
    "com.apple.SecurityServer",
];

#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ProfileRequest {
    pub tag: String,
    pub read_full: bool,
    pub read_roots: Vec<String>,
    pub write_default: Access,
    pub write_roots: Vec<String>,
    pub delete_default: Access,
    pub delete_roots: Vec<String>,
    /// Extra roots granted after an `auto_grant` decision (retry run only).
    pub extra_write_grants: Vec<String>,
    pub extra_delete_grants: Vec<String>,
    pub network_mode: NetworkMode,
    pub unix_socket_outbound: bool,
}

impl ProfileRequest {
    pub fn from_policy(policy: &EffectivePolicy, tag: impl Into<String>) -> Self {
        Self {
            tag: tag.into(),
            read_full: policy.read_full,
            read_roots: policy.read_roots.clone(),
            write_default: policy.write_default,
            write_roots: policy.write_roots.clone(),
            delete_default: policy.delete_default,
            delete_roots: policy.delete_roots.clone(),
            extra_write_grants: Vec::new(),
            extra_delete_grants: Vec::new(),
            network_mode: policy.network_mode,
            unix_socket_outbound: policy.unix_socket_outbound,
        }
    }
}

/// A tag may only contain characters that are safe inside an SBPL string.
pub fn validate_tag(tag: &str) -> Result<(), String> {
    let Some(suffix) = tag.strip_prefix(TAG_PREFIX) else {
        return Err(format!("tag must start with {TAG_PREFIX}"));
    };
    if suffix.is_empty()
        || !suffix
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
    {
        return Err(format!("tag `{tag}` contains unsafe characters"));
    }
    Ok(())
}

/// Quote a value as an SBPL string literal.
fn quote(value: &str) -> String {
    let mut escaped = String::with_capacity(value.len() + 2);
    escaped.push('"');
    for ch in value.chars() {
        match ch {
            '"' => escaped.push_str("\\\""),
            '\\' => escaped.push_str("\\\\"),
            '\n' | '\r' => escaped.push(' '),
            _ => escaped.push(ch),
        }
    }
    escaped.push('"');
    escaped
}

fn subpath_rule(action: &str, operation: &str, roots: &[String]) -> Option<String> {
    if roots.is_empty() {
        return None;
    }
    let mut rule = format!("({action} {operation}");
    for root in roots {
        rule.push_str(&format!(" (subpath {})", quote(root)));
    }
    rule.push(')');
    Some(rule)
}

fn literal_rule(action: &str, operation: &str, paths: &[&str]) -> String {
    let mut rule = format!("({action} {operation}");
    for path in paths {
        rule.push_str(&format!(" (literal {})", quote(path)));
    }
    rule.push(')');
    rule
}

/// Build the full SBPL profile text.
pub fn generate(request: &ProfileRequest) -> Result<String, String> {
    validate_tag(&request.tag)?;
    for root in request
        .write_roots
        .iter()
        .chain(request.delete_roots.iter())
        .chain(request.extra_write_grants.iter())
        .chain(request.extra_delete_grants.iter())
    {
        if !root.starts_with('/') {
            return Err(format!("profile root `{root}` is not absolute"));
        }
    }

    let mut lines: Vec<String> = Vec::new();
    lines.push("(version 1)".to_string());
    lines.push("(debug deny)".to_string());
    lines.push(format!(
        "(deny default (with message {}))",
        quote(&request.tag)
    ));

    // --- execution basics -------------------------------------------------
    lines.push("(allow process-exec*)".to_string());
    lines.push("(allow process-fork)".to_string());
    lines.push("(allow signal (target self))".to_string());
    lines.push("(allow sysctl-read)".to_string());
    lines.push("(allow ipc-posix-shm)".to_string());
    let mut mach = String::from("(allow mach-lookup");
    for service in MACH_BASELINE {
        mach.push_str(&format!(" (global-name {})", quote(service)));
    }
    mach.push(')');
    lines.push(mach);

    // --- reads ------------------------------------------------------------
    if request.read_full {
        lines.push("(allow file-read*)".to_string());
    } else if let Some(rule) = subpath_rule("allow", "file-read*", &request.read_roots) {
        lines.push(rule);
    }

    // --- writes (create / modify) ----------------------------------------
    lines.push("(allow file-ioctl)".to_string());
    match request.write_default {
        Access::Allow => lines.push("(allow file-write* (subpath \"/\"))".to_string()),
        Access::Deny => {
            if let Some(rule) = subpath_rule("allow", "file-write*", &request.write_roots) {
                lines.push(rule);
            }
        }
    }
    if let Some(rule) = subpath_rule("allow", "file-write*", &request.extra_write_grants) {
        lines.push(rule);
    }
    lines.push(literal_rule("allow", "file-write*", DEVICE_WRITE_ALLOW));

    // --- deletes (unlink) -------------------------------------------------
    // Emitted after the write rules on purpose: last match wins, so this pair
    // revokes the unlink capability that `file-write*` would otherwise grant.
    match request.delete_default {
        // The tag matters here too: a denial produced by *this* rule is only
        // attributable to the run if it carries the same message as default.
        Access::Deny => lines.push(format!(
            "(deny file-write-unlink (subpath \"/\") (with message {}))",
            quote(&request.tag)
        )),
        Access::Allow => lines.push("(allow file-write-unlink (subpath \"/\"))".to_string()),
    }
    if let Some(rule) = subpath_rule("allow", "file-write-unlink", &request.delete_roots) {
        lines.push(rule);
    }
    if let Some(rule) = subpath_rule("allow", "file-write-unlink", &request.extra_delete_grants) {
        lines.push(rule);
    }

    // --- network ----------------------------------------------------------
    match request.network_mode {
        NetworkMode::Allow => lines.push("(allow network*)".to_string()),
        NetworkMode::LoopbackOnly => {
            lines.push("(allow network-outbound (remote ip \"localhost:*\"))".to_string());
            lines.push("(allow network-inbound (local ip \"localhost:*\"))".to_string());
        }
        NetworkMode::Deny => lines.push(format!(
            "(deny network* (with message {}))",
            quote(&request.tag)
        )),
    }
    if request.unix_socket_outbound {
        lines
            .push("(allow network-outbound (remote unix-socket (path-regex #\"^/\")))".to_string());
    }
    if request.network_mode != NetworkMode::Deny {
        lines.push("(allow system-socket (socket-domain AF_UNIX))".to_string());
    }

    Ok(lines.join("\n") + "\n")
}

/// Minimal profile used by `sandbox-cli --probe` to decide whether Seatbelt can
/// be applied in this process context at all (nested sandboxes cannot).
pub fn probe_profile(tag: &str) -> String {
    format!(
        "(version 1)\n(deny default (with message {}))\n(allow process-exec*)\n(allow file-read*)\n(allow file-write* (literal \"/dev/stdout\"))\n",
        quote(tag)
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn request() -> ProfileRequest {
        ProfileRequest {
            tag: "SC_SBX_deadbeef".to_string(),
            read_full: true,
            read_roots: vec!["/".to_string()],
            write_default: Access::Deny,
            write_roots: vec!["/private/tmp/ws".to_string()],
            delete_default: Access::Deny,
            delete_roots: vec!["/private/tmp/ws/.sc-trash".to_string()],
            extra_write_grants: Vec::new(),
            extra_delete_grants: Vec::new(),
            network_mode: NetworkMode::LoopbackOnly,
            unix_socket_outbound: true,
        }
    }

    fn index_of(profile: &str, needle: &str) -> usize {
        profile
            .lines()
            .position(|line| line == needle)
            .unwrap_or_else(|| panic!("missing line: {needle}\n{profile}"))
    }

    #[test]
    fn generates_expected_core_rules() {
        let profile = generate(&request()).unwrap();
        assert!(profile.starts_with("(version 1)\n(debug deny)\n"));
        assert!(profile.contains("(deny default (with message \"SC_SBX_deadbeef\"))"));
        assert!(profile.contains("(allow file-read*)"));
        assert!(profile.contains("(allow file-write* (subpath \"/private/tmp/ws\"))"));
        assert!(profile.contains(
            "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_deadbeef\"))"
        ));
        assert!(
            profile.contains("(allow file-write-unlink (subpath \"/private/tmp/ws/.sc-trash\"))")
        );
        assert!(profile.contains("(allow network-outbound (remote ip \"localhost:*\"))"));
    }

    #[test]
    fn unlink_deny_is_emitted_after_write_allows() {
        let profile = generate(&request()).unwrap();
        let write = index_of(
            &profile,
            "(allow file-write* (subpath \"/private/tmp/ws\"))",
        );
        let deny_unlink = index_of(
            &profile,
            "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_deadbeef\"))",
        );
        let reallow = index_of(
            &profile,
            "(allow file-write-unlink (subpath \"/private/tmp/ws/.sc-trash\"))",
        );
        assert!(
            write < deny_unlink,
            "write rules must precede the unlink deny"
        );
        assert!(deny_unlink < reallow, "narrow unlink grants must come last");
    }

    #[test]
    fn write_everywhere_policy_still_protects_deletes() {
        let mut req = request();
        req.write_default = Access::Allow;
        let profile = generate(&req).unwrap();
        let broad = index_of(&profile, "(allow file-write* (subpath \"/\"))");
        let deny_unlink = index_of(
            &profile,
            "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_deadbeef\"))",
        );
        assert!(broad < deny_unlink);
    }

    #[test]
    fn retry_grants_are_appended() {
        let mut req = request();
        req.extra_delete_grants = vec!["/app-home/cache-demo".to_string()];
        let profile = generate(&req).unwrap();
        let grant = index_of(
            &profile,
            "(allow file-write-unlink (subpath \"/app-home/cache-demo\"))",
        );
        let deny_unlink = index_of(
            &profile,
            "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_deadbeef\"))",
        );
        assert!(deny_unlink < grant);
    }

    #[test]
    fn restricted_reads_are_emitted_as_subpaths() {
        let mut req = request();
        req.read_full = false;
        req.read_roots = vec!["/private/tmp/ws".to_string()];
        let profile = generate(&req).unwrap();
        assert!(!profile.contains("(allow file-read*)"));
        assert!(profile.contains("(allow file-read* (subpath \"/private/tmp/ws\"))"));
    }

    #[test]
    fn network_modes_are_exclusive() {
        let mut req = request();
        req.network_mode = NetworkMode::Deny;
        let profile = generate(&req).unwrap();
        assert!(profile.contains("(deny network* (with message \"SC_SBX_deadbeef\"))"));
        assert!(!profile.contains("(remote ip \"localhost:*\")"));

        req.network_mode = NetworkMode::Allow;
        let profile = generate(&req).unwrap();
        assert!(profile.contains("(allow network*)"));
    }

    #[test]
    fn rejects_injection_through_tag_and_roots() {
        let mut req = request();
        req.tag = "SC_SBX_a\") (allow file-write* (subpath \"/\")".to_string();
        assert!(generate(&req).is_err());

        let mut req = request();
        req.write_roots = vec!["relative/path".to_string()];
        assert!(generate(&req).is_err());
    }

    #[test]
    fn quotes_paths_containing_spaces() {
        let mut req = request();
        req.write_roots = vec!["/private/tmp/my ws".to_string()];
        let profile = generate(&req).unwrap();
        assert!(profile.contains("(subpath \"/private/tmp/my ws\")"));
    }
}
