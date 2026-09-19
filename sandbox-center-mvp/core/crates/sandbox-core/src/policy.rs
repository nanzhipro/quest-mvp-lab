//! Declarative policy file → *effective policy* resolution.
//!
//! The center is the only component that reads a policy file; the CLI only ever
//! sees the resolved [`EffectivePolicy`] it receives over IPC. Root entries use
//! `subpath` semantics (`/a/b` covers `/a/b` and everything below it) and are
//! canonicalised at resolve time so that symlinked scratch roots (`/tmp` →
//! `/private/tmp`) cannot widen or silently miss the Seatbelt rules.

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};

/// Access classes the policy and the profile generator agree on.
#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq, Hash)]
#[serde(rename_all = "lowercase")]
pub enum OperationClass {
    Read,
    Write,
    Delete,
    Network,
    Other,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "lowercase")]
pub enum Access {
    Allow,
    #[default]
    Deny,
}

#[derive(Debug, Clone, Copy, Default, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "kebab-case")]
pub enum NetworkMode {
    /// Outbound TCP is limited to loopback; everything else is denied.
    #[default]
    LoopbackOnly,
    /// No network at all (loopback included).
    Deny,
    /// Unrestricted outbound (only useful for comparison runs).
    Allow,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ReadPolicy {
    #[serde(default = "full_read")]
    pub allow: Vec<String>,
}

fn full_read() -> Vec<String> {
    vec!["/".to_string()]
}

impl Default for ReadPolicy {
    fn default() -> Self {
        Self { allow: full_read() }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct PathPolicy {
    #[serde(default)]
    pub default: Access,
    #[serde(default)]
    pub allow: Vec<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct NetworkPolicy {
    #[serde(default)]
    pub mode: NetworkMode,
    #[serde(default = "default_true")]
    pub unix_socket_outbound: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AutoGrantRule {
    pub root: String,
    pub operations: Vec<OperationClass>,
    #[serde(default)]
    pub reason: String,
}

/// On-disk policy format (`policy/*.json`).
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct PolicyFile {
    pub version: u32,
    pub name: String,
    #[serde(default)]
    pub read: ReadPolicy,
    #[serde(default)]
    pub write: PathPolicy,
    #[serde(default)]
    pub delete: PathPolicy,
    #[serde(default)]
    pub network: NetworkPolicy,
    #[serde(default)]
    pub auto_grant: Vec<AutoGrantRule>,
}

/// Values substituted into `${...}` placeholders.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct PolicyContext {
    pub workspace: String,
    pub app_home: String,
    pub home: String,
    pub tmpdir: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct AutoGrantRuleResolved {
    pub root: String,
    pub operations: Vec<OperationClass>,
    pub reason: String,
}

/// The fully resolved policy shipped to a one-shot CLI.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EffectivePolicy {
    pub policy_id: String,
    pub read_full: bool,
    pub read_roots: Vec<String>,
    pub write_default: Access,
    pub write_roots: Vec<String>,
    pub delete_default: Access,
    pub delete_roots: Vec<String>,
    pub network_mode: NetworkMode,
    pub unix_socket_outbound: bool,
    pub auto_grant: Vec<AutoGrantRuleResolved>,
}

impl EffectivePolicy {
    /// Number of rules in the resolved policy (surfaced by `ping`).
    pub fn rule_count(&self) -> usize {
        self.read_roots.len()
            + self.write_roots.len()
            + self.delete_roots.len()
            + usize::from(self.network_mode != NetworkMode::Deny)
            + usize::from(self.unix_socket_outbound)
            + self.auto_grant.len()
            + 1 // delete baseline
    }

    /// Which auto-grant rule (if any) covers `class` + `target`.
    ///
    /// Roots are normalised on both sides: a policy that reaches the center
    /// through IPC is not guaranteed to have been resolved against symlinked
    /// scratch paths (`/tmp` → `/private/tmp` on macOS).
    pub fn match_auto_grant(
        &self,
        class: OperationClass,
        target: &str,
    ) -> Option<&AutoGrantRuleResolved> {
        let normalized = normalize_path(target);
        self.auto_grant.iter().find(|rule| {
            rule.operations.contains(&class) && is_under(&normalize_path(&rule.root), &normalized)
        })
    }
}

/// Expand `${VAR}` placeholders and a leading `~`.
pub fn expand_placeholders(raw: &str, ctx: &PolicyContext) -> String {
    let mut out = raw.to_string();
    for (key, value) in [
        ("${WORKSPACE}", ctx.workspace.as_str()),
        ("${APP_HOME}", ctx.app_home.as_str()),
        ("${HOME}", ctx.home.as_str()),
        ("${TMPDIR}", ctx.tmpdir.as_str()),
        ("${USER}", ""),
    ] {
        if key == "${USER}" {
            continue;
        }
        out = out.replace(key, value);
    }
    if let Some(rest) = out.strip_prefix("~/") {
        out = format!("{}/{}", ctx.home.trim_end_matches('/'), rest);
    } else if out == "~" {
        out = ctx.home.clone();
    }
    out
}

/// Resolve `path` to a canonical absolute path, tolerating paths whose leaf does
/// not exist yet (the longest existing ancestor is canonicalised instead).
pub fn normalize_path(raw: &str) -> String {
    if raw.is_empty() {
        return String::new();
    }
    let path = PathBuf::from(raw);
    if let Ok(canonical) = path.canonicalize() {
        return canonical.to_string_lossy().into_owned();
    }
    let mut missing: Vec<std::ffi::OsString> = Vec::new();
    let mut cursor = path.clone();
    while let Some(name) = cursor.file_name() {
        missing.push(name.to_os_string());
        let Some(parent) = cursor.parent() else { break };
        if let Ok(canonical) = parent.canonicalize() {
            let mut rebuilt = canonical;
            for part in missing.iter().rev() {
                rebuilt.push(part);
            }
            return rebuilt.to_string_lossy().into_owned();
        }
        cursor = parent.to_path_buf();
    }
    path.to_string_lossy().into_owned()
}

/// `subpath` containment: `path` equals `root` or lives below it.
pub fn is_under(root: &str, path: &str) -> bool {
    if root.is_empty() {
        return false;
    }
    let root = root.trim_end_matches('/');
    let root = if root.is_empty() { "/" } else { root };
    if root == "/" {
        return path.starts_with('/');
    }
    path == root || path.starts_with(&format!("{root}/"))
}

fn resolve_roots(raw: &[String], ctx: &PolicyContext, field: &str) -> Result<Vec<String>, String> {
    let mut out = Vec::with_capacity(raw.len());
    for entry in raw {
        let expanded = expand_placeholders(entry, ctx);
        if expanded.trim().is_empty() {
            return Err(format!("{field}: empty path entry"));
        }
        if !expanded.starts_with('/') {
            return Err(format!(
                "{field}: `{expanded}` is not absolute — refusing to widen the sandbox"
            ));
        }
        let normalized = normalize_path(&expanded);
        if !out.contains(&normalized) {
            out.push(normalized);
        }
    }
    Ok(out)
}

/// Turn a policy file into the effective policy handed to a CLI session.
pub fn resolve(
    file: &PolicyFile,
    ctx: &PolicyContext,
    policy_id: &str,
) -> Result<EffectivePolicy, String> {
    if file.version != 1 {
        return Err(format!("unsupported policy version {}", file.version));
    }
    let read_roots = resolve_roots(&file.read.allow, ctx, "read.allow")?;
    let read_full = read_roots.iter().any(|root| root == "/");
    let write_roots = resolve_roots(&file.write.allow, ctx, "write.allow")?;
    let delete_roots = resolve_roots(&file.delete.allow, ctx, "delete.allow")?;

    let mut auto_grant = Vec::with_capacity(file.auto_grant.len());
    for rule in &file.auto_grant {
        let expanded = expand_placeholders(&rule.root, ctx);
        if !expanded.starts_with('/') {
            return Err(format!(
                "auto_grant: `{expanded}` is not absolute — refusing to widen the sandbox"
            ));
        }
        auto_grant.push(AutoGrantRuleResolved {
            root: normalize_path(&expanded),
            operations: rule.operations.clone(),
            reason: rule.reason.clone(),
        });
    }

    Ok(EffectivePolicy {
        policy_id: policy_id.to_string(),
        read_full,
        read_roots,
        write_default: file.write.default,
        write_roots,
        delete_default: file.delete.default,
        delete_roots,
        network_mode: file.network.mode,
        unix_socket_outbound: file.network.unix_socket_outbound,
        auto_grant,
    })
}

/// Parse a policy file from disk.
pub fn load_file(path: &Path) -> Result<PolicyFile, String> {
    let text = std::fs::read_to_string(path)
        .map_err(|e| format!("cannot read policy {}: {e}", path.display()))?;
    serde_json::from_str(&text).map_err(|e| format!("cannot parse policy {}: {e}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ctx() -> PolicyContext {
        PolicyContext {
            workspace: "/ws".to_string(),
            app_home: "/app-home".to_string(),
            home: "/Users/demo".to_string(),
            tmpdir: "/tmp".to_string(),
        }
    }

    fn policy() -> PolicyFile {
        serde_json::from_str(
            r#"{
              "version": 1,
              "name": "t",
              "read":  { "allow": ["/"] },
              "write": { "default": "deny", "allow": ["${WORKSPACE}", "${APP_HOME}/cache"] },
              "delete":{ "default": "deny", "allow": ["${WORKSPACE}/.sc-trash"] },
              "network": { "mode": "loopback-only" },
              "auto_grant": [{ "root": "${APP_HOME}/cache", "operations": ["write","delete"], "reason": "cache" }]
            }"#,
        )
        .unwrap()
    }

    #[test]
    fn placeholders_expand() {
        assert_eq!(expand_placeholders("${WORKSPACE}/x", &ctx()), "/ws/x");
        assert_eq!(expand_placeholders("~/x", &ctx()), "/Users/demo/x");
        assert_eq!(expand_placeholders("~", &ctx()), "/Users/demo");
    }

    #[test]
    fn resolve_produces_effective_policy() {
        let effective = resolve(&policy(), &ctx(), "t@1").unwrap();
        assert!(effective.read_full);
        assert_eq!(effective.write_default, Access::Deny);
        assert_eq!(effective.write_roots, vec!["/ws", "/app-home/cache"]);
        assert_eq!(effective.delete_roots, vec!["/ws/.sc-trash"]);
        assert_eq!(effective.network_mode, NetworkMode::LoopbackOnly);
    }

    #[test]
    fn relative_root_is_rejected() {
        let mut file = policy();
        file.write.allow = vec!["relative/path".to_string()];
        let err = resolve(&file, &ctx(), "t@1").unwrap_err();
        assert!(err.contains("not absolute"), "{err}");
    }

    #[test]
    fn subpath_containment() {
        assert!(is_under("/a/b", "/a/b"));
        assert!(is_under("/a/b", "/a/b/c/d"));
        assert!(!is_under("/a/b", "/a/bc"));
        assert!(is_under("/", "/anything"));
        assert!(!is_under("/a/b", "/a"));
    }

    #[test]
    fn auto_grant_matches_root_and_operation() {
        let effective = resolve(&policy(), &ctx(), "t@1").unwrap();
        let hit = effective
            .match_auto_grant(OperationClass::Delete, "/app-home/cache/demo/blob.bin")
            .expect("expected grant");
        assert_eq!(hit.root, "/app-home/cache");
        assert!(effective
            .match_auto_grant(OperationClass::Delete, "/app-home/other/blob.bin")
            .is_none());
        assert!(effective
            .match_auto_grant(OperationClass::Read, "/app-home/cache/blob.bin")
            .is_none());
    }

    #[test]
    fn normalize_path_keeps_missing_leaf() {
        let normalized = normalize_path("/tmp/definitely-missing-leaf-1234/file.txt");
        assert_eq!(
            normalized,
            "/private/tmp/definitely-missing-leaf-1234/file.txt"
        );
    }
}
