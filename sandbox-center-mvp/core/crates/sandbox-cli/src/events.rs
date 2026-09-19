//! Structured events emitted by `sandbox-cli` on stderr as
//! `SC_EVENT|<json>` lines. Human-readable command output is never touched, so
//! an embedding host (the Electron main process) can consume the stream without
//! parsing terminal text.

use serde_json::json;

pub const PREFIX: &str = "SC_EVENT|";

pub fn emit(value: serde_json::Value) {
    eprintln!("{PREFIX}{}", serde_json::to_string(&value).unwrap());
}

pub fn error(code: &str, message: &str) -> serde_json::Value {
    json!({"type": "error", "code": code, "message": message})
}
