//! Line-delimited JSON RPC between `sandbox-cli` (one process per tool call),
//! the Electron main process and the resident `sandbox-center`.

use crate::policy::EffectivePolicy;
use crate::violation::Violation;
use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};
use serde_json::Value;

pub const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct ClientInfo {
    pub name: String,
    pub pid: u32,
    pub version: String,
}

/// Requests are serialised as `{"op": "<name>", ...}`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Request {
    /// Liveness + counters.
    Ping,
    /// A tool call is about to run: the center mints a session id and a profile tag
    /// and answers with the effective policy.
    OpenSession(OpenSessionRequest),
    /// The kernel denied something; the center decides whether to auto-grant.
    ReportViolation(ReportViolationRequest),
    /// The tool call finished; the center seals the session record.
    CloseSession(CloseSessionRequest),
    /// Live + recently finished sessions (used by the GUI).
    ListSessions,
    /// Tail of the tamper-evident audit log (used by the GUI).
    RecentEvents { limit: u32 },
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OpenSessionRequest {
    pub client: ClientInfo,
    pub cwd: String,
    /// Full argv of the command that will run inside the sandbox.
    pub argv: Vec<String>,
    /// Working directory the agent declared as its workspace (completion of `${WORKSPACE}`).
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub workspace: Option<String>,
    /// Set when this session is a re-run after an auto-grant.
    #[serde(default)]
    pub retry: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct OpenSessionResult {
    pub session_id: String,
    /// Unique `SC_SBX_<hex>` marker embedded in the profile message.
    pub tag: String,
    pub policy: EffectivePolicy,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ReportViolationRequest {
    pub session_id: String,
    pub violation: Violation,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ReportViolationResult {
    /// `"deny"` or `"grant"`.
    pub action: String,
    pub reason: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub grant_root: Option<String>,
    /// `false` when the policy disabled auto-granting altogether.
    pub auto_grant_enabled: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CloseSessionRequest {
    pub session_id: String,
    pub exit_code: i32,
    pub duration_ms: u64,
    pub violations: u32,
    pub grants: u32,
    #[serde(default)]
    pub retried: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct CloseSessionResult {
    pub recorded: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct SessionSummary {
    pub session_id: String,
    pub tag: String,
    pub pid: u32,
    pub command: String,
    pub cwd: String,
    /// `"running"` | `"closed"`.
    pub state: String,
    pub violations: u32,
    pub grants: u32,
    pub started_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ListSessionsResult {
    pub sessions: Vec<SessionSummary>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct AuditEventView {
    pub seq: u64,
    pub ts: String,
    pub kind: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub session_id: Option<String>,
    pub data: Value,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct RecentEventsResult {
    pub events: Vec<AuditEventView>,
    pub total: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct PingResult {
    pub center_version: String,
    pub protocol_version: u32,
    pub policy_id: String,
    pub rules: usize,
    pub sessions: usize,
    pub uptime_ms: u64,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct ErrorBody {
    pub code: String,
    pub message: String,
}

/// Every response is `{"ok":true,"result":{...}}` or `{"ok":false,"error":{...}}`.
#[derive(Debug, Clone, Serialize, Deserialize, PartialEq)]
pub struct Response {
    pub ok: bool,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub error: Option<ErrorBody>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct WireError {
    pub code: String,
    pub message: String,
}

impl std::fmt::Display for WireError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: {}", self.code, self.message)
    }
}

impl std::error::Error for WireError {}

impl Response {
    pub fn ok(value: impl Serialize) -> Self {
        Self {
            ok: true,
            result: Some(serde_json::to_value(value).unwrap_or(Value::Null)),
            error: None,
        }
    }

    pub fn err(code: &str, message: impl Into<String>) -> Self {
        Self {
            ok: false,
            result: None,
            error: Some(ErrorBody {
                code: code.to_string(),
                message: message.into(),
            }),
        }
    }

    /// Unwrap a success payload, mapping a failure response onto [`WireError`].
    pub fn into_result<T: DeserializeOwned>(self) -> Result<T, WireError> {
        if self.ok {
            let value = self.result.unwrap_or(Value::Null);
            serde_json::from_value(value).map_err(|e| WireError {
                code: "bad_response".to_string(),
                message: e.to_string(),
            })
        } else {
            Err(match self.error {
                Some(e) => WireError {
                    code: e.code,
                    message: e.message,
                },
                None => WireError {
                    code: "unknown".to_string(),
                    message: "center returned an error without a body".to_string(),
                },
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn request_wire_shape_is_op_tagged() {
        let value = serde_json::to_value(Request::Ping).unwrap();
        assert_eq!(value, serde_json::json!({"op": "ping"}));

        let value = serde_json::to_value(Request::RecentEvents { limit: 5 }).unwrap();
        assert_eq!(
            value,
            serde_json::json!({"op": "recent_events", "limit": 5})
        );
    }

    #[test]
    fn open_session_request_round_trips() {
        let req = Request::OpenSession(OpenSessionRequest {
            client: ClientInfo {
                name: "sandbox-cli".to_string(),
                pid: 42,
                version: "0.1.0".to_string(),
            },
            cwd: "/tmp/ws".to_string(),
            argv: vec!["/bin/zsh".to_string(), "-c".to_string(), "ls".to_string()],
            workspace: Some("/tmp/ws".to_string()),
            retry: false,
        });
        let text = serde_json::to_string(&req).unwrap();
        assert!(text.contains(r#""op":"open_session""#));
        let back: Request = serde_json::from_str(&text).unwrap();
        assert_eq!(back, req);
    }

    #[test]
    fn response_into_result_maps_errors() {
        let ok = Response::ok(serde_json::json!({"a": 1}));
        let value: Value = ok.into_result().unwrap();
        assert_eq!(value["a"], 1);

        let err = Response::err("not_found", "no such session");
        let wire = err.into_result::<Value>().unwrap_err();
        assert_eq!(wire.code, "not_found");
        assert_eq!(wire.message, "no such session");
    }

    #[test]
    fn response_serialises_without_empty_fields() {
        let text = serde_json::to_string(&Response::ok(serde_json::json!(1))).unwrap();
        assert_eq!(text, r#"{"ok":true,"result":1}"#);
        let text = serde_json::to_string(&Response::err("boom", "nope")).unwrap();
        assert_eq!(
            text,
            r#"{"ok":false,"error":{"code":"boom","message":"nope"}}"#
        );
    }
}
