//! Resident sandbox-center state machine: sessions, auto-grant decisions, audit.

use crate::audit::AuditWriter;
use crate::logging::Logger;
use sandbox_core::policy::EffectivePolicy;
use sandbox_core::protocol::{
    AuditEventView, CloseSessionRequest, CloseSessionResult, ListSessionsResult,
    OpenSessionRequest, OpenSessionResult, PingResult, RecentEventsResult, ReportViolationRequest,
    ReportViolationResult, SessionSummary,
};
use sandbox_core::timefmt::now_unix_ms;
use sandbox_core::{TAG_PREFIX, VERSION};
use serde_json::{json, Value};
use std::collections::{BTreeMap, VecDeque};
use std::sync::Mutex;

const MAX_RECENT_SESSIONS: usize = 100;
const MAX_EVENTS: usize = 500;

#[derive(Debug, Clone)]
pub struct SessionRecord {
    pub session_id: String,
    pub tag: String,
    pub pid: u32,
    pub command: String,
    pub cwd: String,
    pub state: &'static str,
    pub violations: u32,
    pub grants: u32,
    pub started_ms: u64,
    pub ended_ms: Option<u64>,
    pub retried: bool,
}

impl SessionRecord {
    fn summary(&self) -> SessionSummary {
        SessionSummary {
            session_id: self.session_id.clone(),
            tag: self.tag.clone(),
            pid: self.pid,
            command: self.command.clone(),
            cwd: self.cwd.clone(),
            state: self.state.to_string(),
            violations: self.violations,
            grants: self.grants,
            started_ms: self.started_ms,
        }
    }
}

struct Sessions {
    next_id: u64,
    live: BTreeMap<String, SessionRecord>,
    closed: VecDeque<SessionRecord>,
}

struct EventRing {
    items: VecDeque<AuditEventView>,
    total: u64,
}

pub struct Center {
    pub started_ms: u64,
    pub policy: EffectivePolicy,
    pub auto_grant_enabled: bool,
    pub logger: Logger,
    audit: Mutex<AuditWriter>,
    sessions: Mutex<Sessions>,
    events: Mutex<EventRing>,
}

impl Center {
    pub fn new(
        policy: EffectivePolicy,
        auto_grant_enabled: bool,
        logger: Logger,
        audit: AuditWriter,
    ) -> Self {
        Self {
            started_ms: now_unix_ms(),
            policy,
            auto_grant_enabled,
            logger,
            audit: Mutex::new(audit),
            sessions: Mutex::new(Sessions {
                next_id: 0,
                live: BTreeMap::new(),
                closed: VecDeque::new(),
            }),
            events: Mutex::new(EventRing {
                items: VecDeque::new(),
                total: 0,
            }),
        }
    }

    /// Append to the audit trail and the in-memory ring the GUI reads.
    pub fn record(&self, kind: &str, session_id: Option<&str>, data: Value) {
        let record = match self.audit.lock() {
            Ok(mut audit) => match audit.append(kind, session_id, data) {
                Ok(record) => record,
                Err(error) => {
                    self.logger.error(format!("audit append failed: {error}"));
                    return;
                }
            },
            Err(_) => return,
        };
        let view = AuditEventView {
            seq: record.get("seq").and_then(Value::as_u64).unwrap_or(0),
            ts: record
                .get("ts")
                .and_then(Value::as_str)
                .unwrap_or_default()
                .to_string(),
            kind: kind.to_string(),
            session_id: session_id.map(str::to_string),
            data: record.get("data").cloned().unwrap_or(Value::Null),
        };
        if let Ok(mut ring) = self.events.lock() {
            ring.total += 1;
            ring.items.push_back(view);
            while ring.items.len() > MAX_EVENTS {
                ring.items.pop_front();
            }
        }
    }

    pub fn ping(&self) -> PingResult {
        let sessions = self
            .sessions
            .lock()
            .map(|sessions| sessions.live.len())
            .unwrap_or(0);
        PingResult {
            center_version: VERSION.to_string(),
            protocol_version: sandbox_core::protocol::PROTOCOL_VERSION,
            policy_id: self.policy.policy_id.clone(),
            rules: self.policy.rule_count(),
            sessions,
            uptime_ms: now_unix_ms().saturating_sub(self.started_ms),
        }
    }

    pub fn open_session(&self, request: OpenSessionRequest) -> Result<OpenSessionResult, String> {
        let deadline_ms = now_unix_ms();
        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| "session table poisoned".to_string())?;
        sessions.next_id += 1;
        let sequence = sessions.next_id;
        let session_id = format!("s-{sequence}");
        let tag = make_tag(sequence, request.client.pid, deadline_ms);
        let record = SessionRecord {
            session_id: session_id.clone(),
            tag: tag.clone(),
            pid: request.client.pid,
            command: truncate(&request.argv.join(" "), 240),
            cwd: request.cwd.clone(),
            state: "running",
            violations: 0,
            grants: 0,
            started_ms: deadline_ms,
            ended_ms: None,
            retried: request.retry,
        };
        sessions.live.insert(session_id.clone(), record);
        drop(sessions);

        self.record(
            "session.opened",
            Some(&session_id),
            json!({
                "tag": tag,
                "pid": request.client.pid,
                "client": request.client.name,
                "cwd": request.cwd,
                "argv": request.argv,
                "retry": request.retry,
                "policy_id": self.policy.policy_id,
            }),
        );
        self.logger.info(format!(
            "SessionOpened {session_id} tag={tag} pid={} cwd={} cmd={}",
            request.client.pid,
            request.cwd,
            truncate(&request.argv.join(" "), 160)
        ));

        Ok(OpenSessionResult {
            session_id,
            tag,
            policy: self.policy.clone(),
        })
    }

    pub fn report_violation(
        &self,
        request: ReportViolationRequest,
    ) -> Result<ReportViolationResult, String> {
        let class = request.violation.class();
        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| "session table poisoned".to_string())?;
        let session = sessions
            .live
            .get_mut(&request.session_id)
            .ok_or_else(|| format!("unknown session {}", request.session_id))?;
        session.violations += 1;

        let grant = if self.auto_grant_enabled {
            self.policy
                .match_auto_grant(class, &request.violation.target)
                .cloned()
        } else {
            None
        };

        let result = match grant {
            Some(rule) => {
                session.grants += 1;
                self.record(
                    "grant.auto",
                    Some(&request.session_id),
                    json!({
                        "root": rule.root,
                        "reason": rule.reason,
                        "operation_class": class,
                        "operation": request.violation.operation,
                        "target": request.violation.target,
                    }),
                );
                self.logger.info(format!(
                    "AutoGrant {} {} -> {} ({})",
                    request.session_id, request.violation.target, rule.root, rule.reason
                ));
                ReportViolationResult {
                    action: "grant".to_string(),
                    reason: rule.reason.clone(),
                    grant_root: Some(rule.root.clone()),
                    auto_grant_enabled: true,
                }
            }
            None => {
                self.record(
                    "violation.denied",
                    Some(&request.session_id),
                    json!({
                        "operation_class": class,
                        "operation": request.violation.operation,
                        "target": request.violation.target,
                        "actor": request.violation.actor,
                        "pid": request.violation.pid,
                        "auto_grant_enabled": self.auto_grant_enabled,
                    }),
                );
                self.logger.warn(format!(
                    "Denied {} {} {}",
                    request.session_id, request.violation.operation, request.violation.target
                ));
                ReportViolationResult {
                    action: "deny".to_string(),
                    reason: "no auto-grant rule covers this violation".to_string(),
                    grant_root: None,
                    auto_grant_enabled: self.auto_grant_enabled,
                }
            }
        };
        Ok(result)
    }

    pub fn close_session(
        &self,
        request: CloseSessionRequest,
    ) -> Result<CloseSessionResult, String> {
        let mut sessions = self
            .sessions
            .lock()
            .map_err(|_| "session table poisoned".to_string())?;
        let mut record = sessions
            .live
            .remove(&request.session_id)
            .ok_or_else(|| format!("unknown session {}", request.session_id))?;
        record.state = "closed";
        record.violations = request.violations;
        record.grants = request.grants;
        record.retried = request.retried;
        record.ended_ms = Some(now_unix_ms());
        sessions.closed.push_back(record.clone());
        while sessions.closed.len() > MAX_RECENT_SESSIONS {
            sessions.closed.pop_front();
        }
        drop(sessions);

        self.record(
            "session.closed",
            Some(&request.session_id),
            json!({
                "exit_code": request.exit_code,
                "duration_ms": request.duration_ms,
                "violations": request.violations,
                "grants": request.grants,
                "retried": request.retried,
                "tag": record.tag,
            }),
        );
        self.logger.info(format!(
            "SessionClosed {} exit={} duration={}ms violations={} grants={}",
            request.session_id,
            request.exit_code,
            request.duration_ms,
            request.violations,
            request.grants
        ));
        Ok(CloseSessionResult { recorded: true })
    }

    pub fn list_sessions(&self) -> ListSessionsResult {
        let sessions = match self.sessions.lock() {
            Ok(sessions) => sessions,
            Err(_) => {
                return ListSessionsResult {
                    sessions: Vec::new(),
                }
            }
        };
        let mut out: Vec<SessionSummary> =
            sessions.live.values().map(SessionRecord::summary).collect();
        out.extend(sessions.closed.iter().rev().map(SessionRecord::summary));
        ListSessionsResult { sessions: out }
    }

    pub fn recent_events(&self, limit: u32) -> RecentEventsResult {
        let ring = match self.events.lock() {
            Ok(ring) => ring,
            Err(_) => {
                return RecentEventsResult {
                    events: Vec::new(),
                    total: 0,
                }
            }
        };
        let take = (limit as usize).min(ring.items.len());
        let events = ring
            .items
            .iter()
            .skip(ring.items.len() - take)
            .cloned()
            .collect();
        RecentEventsResult {
            events,
            total: ring.total,
        }
    }
}

/// Unique, log-greppable marker embedded in the generated profile.
fn make_tag(sequence: u64, pid: u32, timestamp_ms: u64) -> String {
    let mixed = (timestamp_ms as u32)
        .wrapping_mul(0x9E37_79B9)
        .wrapping_add(pid.wrapping_mul(0x85EB_CA6B))
        .wrapping_add((sequence as u32).wrapping_mul(0xC2B2_AE35));
    format!("{TAG_PREFIX}{:08x}", mixed ^ (mixed >> 13))
}

fn truncate(value: &str, max: usize) -> String {
    if value.chars().count() <= max {
        return value.to_string();
    }
    let mut out: String = value.chars().take(max.saturating_sub(1)).collect();
    out.push('…');
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use sandbox_core::policy::{
        Access, AutoGrantRuleResolved, EffectivePolicy, NetworkMode, OperationClass,
    };
    use sandbox_core::protocol::ClientInfo;
    use sandbox_core::violation::Violation;

    fn center(dir: &std::path::Path) -> Center {
        let policy = EffectivePolicy {
            policy_id: "test@1".to_string(),
            read_full: true,
            read_roots: vec!["/".to_string()],
            write_default: Access::Deny,
            write_roots: vec!["/tmp/ws".to_string()],
            delete_default: Access::Deny,
            delete_roots: vec!["/tmp/ws/.sc-trash".to_string()],
            network_mode: NetworkMode::LoopbackOnly,
            unix_socket_outbound: true,
            auto_grant: vec![AutoGrantRuleResolved {
                root: "/tmp/cache".to_string(),
                operations: vec![OperationClass::Delete, OperationClass::Write],
                reason: "regenerable cache".to_string(),
            }],
        };
        let logger = Logger::new("sandbox-center", None).without_stderr();
        Center::new(policy, true, logger, AuditWriter::open(dir).unwrap())
    }

    fn request(cwd: &str) -> OpenSessionRequest {
        OpenSessionRequest {
            client: ClientInfo {
                name: "sandbox-cli".to_string(),
                pid: 1234,
                version: "0.1.0".to_string(),
            },
            cwd: cwd.to_string(),
            argv: vec![
                "/bin/zsh".to_string(),
                "-c".to_string(),
                "rm -rf x".to_string(),
            ],
            workspace: Some(cwd.to_string()),
            retry: false,
        }
    }

    fn temp_dir(tag: &str) -> std::path::PathBuf {
        let dir = std::env::temp_dir().join(format!("sc-center-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn opens_and_closes_a_session() {
        let dir = temp_dir("lifecycle");
        let center = center(&dir);
        let opened = center.open_session(request("/tmp/ws")).unwrap();
        assert_eq!(opened.session_id, "s-1");
        assert!(opened.tag.starts_with(TAG_PREFIX));
        assert_eq!(center.ping().sessions, 1);

        let closed = center
            .close_session(CloseSessionRequest {
                session_id: opened.session_id.clone(),
                exit_code: 0,
                duration_ms: 12,
                violations: 0,
                grants: 0,
                retried: false,
            })
            .unwrap();
        assert!(closed.recorded);
        assert_eq!(center.ping().sessions, 0);
        let sessions = center.list_sessions().sessions;
        assert_eq!(sessions.len(), 1);
        assert_eq!(sessions[0].state, "closed");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn auto_grants_only_for_matching_roots_and_operations() {
        let dir = temp_dir("grant");
        let center = center(&dir);
        let opened = center.open_session(request("/tmp/ws")).unwrap();

        let denied = center
            .report_violation(ReportViolationRequest {
                session_id: opened.session_id.clone(),
                violation: Violation {
                    actor: "rm".to_string(),
                    pid: 5,
                    operation: "file-write-unlink".to_string(),
                    target: "/tmp/ws/report.txt".to_string(),
                },
            })
            .unwrap();
        assert_eq!(denied.action, "deny");
        assert!(denied.grant_root.is_none());

        let granted = center
            .report_violation(ReportViolationRequest {
                session_id: opened.session_id.clone(),
                violation: Violation {
                    actor: "rm".to_string(),
                    pid: 6,
                    operation: "file-write-unlink".to_string(),
                    target: "/tmp/cache/blob.bin".to_string(),
                },
            })
            .unwrap();
        assert_eq!(granted.action, "grant");
        assert_eq!(granted.grant_root.as_deref(), Some("/tmp/cache"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn reports_unknown_sessions() {
        let dir = temp_dir("unknown");
        let center = center(&dir);
        let error = center
            .report_violation(ReportViolationRequest {
                session_id: "s-404".to_string(),
                violation: Violation {
                    actor: "rm".to_string(),
                    pid: 1,
                    operation: "file-write-unlink".to_string(),
                    target: "/x".to_string(),
                },
            })
            .unwrap_err();
        assert!(error.contains("unknown session"));
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn recent_events_are_ring_buffered_and_counted() {
        let dir = temp_dir("events");
        let center = center(&dir);
        let opened = center.open_session(request("/tmp/ws")).unwrap();
        assert_eq!(center.recent_events(10).events.len(), 1);
        let _ = center.close_session(CloseSessionRequest {
            session_id: opened.session_id,
            exit_code: 1,
            duration_ms: 3,
            violations: 2,
            grants: 1,
            retried: true,
        });
        let events = center.recent_events(10);
        assert_eq!(events.events.len(), 2);
        assert_eq!(events.total, 2);
        assert_eq!(events.events[0].kind, "session.opened");
        assert_eq!(events.events[1].kind, "session.closed");
        let _ = std::fs::remove_dir_all(dir);
    }
}
