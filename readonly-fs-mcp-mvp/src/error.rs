//! Failure vocabulary of the read-only filesystem tools.
//!
//! Every failure a caller can cause is a *tool-level* error: the tool ran, the
//! request was understood, and the answer is "no". Reporting it as a JSON-RPC
//! protocol error would hide the message from the model, so each error becomes a
//! [`CallToolResult`] with `isError: true` carrying a stable [`FsError::code`] in
//! `structuredContent` — an LLM branches on the code, humans read the message.

use std::io;

use rmcp::handler::server::tool::IntoCallToolResult;
use rmcp::model::{CallToolResponse, CallToolResult};
use serde_json::json;

/// Stable error codes. These strings are part of the tool contract (SPEC.md §5).
pub const CODE_OUTSIDE_WORKSPACE: &str = "outside_workspace";
pub const CODE_NOT_FOUND: &str = "not_found";
pub const CODE_NOT_A_FILE: &str = "not_a_file";
pub const CODE_NOT_A_DIRECTORY: &str = "not_a_directory";
pub const CODE_PERMISSION_DENIED: &str = "permission_denied";
pub const CODE_NOT_TEXT: &str = "not_text";
pub const CODE_LINE_TOO_LARGE: &str = "line_too_large";
pub const CODE_INVALID_PARAMETER: &str = "invalid_parameter";
pub const CODE_IO: &str = "io_error";

#[derive(Debug, thiserror::Error)]
pub enum FsError {
    #[error(
        "`{path}` resolves outside the workspace root `{root}`; only paths inside the root can be inspected"
    )]
    OutsideWorkspace { path: String, root: String },

    #[error("`{path}` does not exist inside the workspace")]
    NotFound { path: String },

    #[error("`{path}` is not a regular file; use list_directory or file_metadata for it")]
    NotAFile { path: String },

    #[error("`{path}` is not a directory; use read_file or file_metadata for it")]
    NotADirectory { path: String },

    #[error("`{path}` cannot be read: permission denied")]
    PermissionDenied { path: String },

    #[error(
        "`{path}` is not UTF-8 text (binary content or a non-UTF-8 encoding) at line {line}; file_metadata still describes it"
    )]
    NotText { path: String, line: u64 },

    #[error(
        "line {line} of `{path}` is {line_bytes} bytes, above the {budget}-byte per-call budget; this tool cannot return it (file_metadata still describes the file)"
    )]
    LineTooLarge {
        path: String,
        line: u64,
        line_bytes: usize,
        budget: usize,
    },

    #[error("invalid parameter `{parameter}`: {reason}")]
    InvalidParameter {
        parameter: &'static str,
        reason: String,
    },

    #[error("I/O error on `{path}`: {source}")]
    Io { path: String, source: io::Error },
}

impl FsError {
    /// Map an OS error onto the contract vocabulary, keeping the caller's path
    /// string in the message so the model sees what it asked for.
    pub fn from_io(path: &str, source: io::Error) -> Self {
        match source.kind() {
            io::ErrorKind::NotFound => FsError::NotFound {
                path: path.to_string(),
            },
            io::ErrorKind::PermissionDenied => FsError::PermissionDenied {
                path: path.to_string(),
            },
            _ => FsError::Io {
                path: path.to_string(),
                source,
            },
        }
    }

    pub fn code(&self) -> &'static str {
        match self {
            FsError::OutsideWorkspace { .. } => CODE_OUTSIDE_WORKSPACE,
            FsError::NotFound { .. } => CODE_NOT_FOUND,
            FsError::NotAFile { .. } => CODE_NOT_A_FILE,
            FsError::NotADirectory { .. } => CODE_NOT_A_DIRECTORY,
            FsError::PermissionDenied { .. } => CODE_PERMISSION_DENIED,
            FsError::NotText { .. } => CODE_NOT_TEXT,
            FsError::LineTooLarge { .. } => CODE_LINE_TOO_LARGE,
            FsError::InvalidParameter { .. } => CODE_INVALID_PARAMETER,
            FsError::Io { .. } => CODE_IO,
        }
    }

    /// Structured payload handed to the model alongside the prose message.
    pub fn payload(&self) -> serde_json::Value {
        json!({
            "code": self.code(),
            "message": self.to_string(),
        })
    }
}

impl IntoCallToolResult for FsError {
    fn into_call_tool_result(self) -> Result<CallToolResponse, rmcp::ErrorData> {
        Ok(CallToolResult::structured_error(self.payload()).into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codes_are_stable_and_unique() {
        let samples = [
            (
                FsError::OutsideWorkspace {
                    path: "x".into(),
                    root: "/r".into(),
                },
                "outside_workspace",
            ),
            (FsError::NotFound { path: "x".into() }, "not_found"),
            (FsError::NotAFile { path: "x".into() }, "not_a_file"),
            (
                FsError::NotADirectory { path: "x".into() },
                "not_a_directory",
            ),
            (
                FsError::PermissionDenied { path: "x".into() },
                "permission_denied",
            ),
            (
                FsError::NotText {
                    path: "x".into(),
                    line: 3,
                },
                "not_text",
            ),
            (
                FsError::LineTooLarge {
                    path: "x".into(),
                    line: 1,
                    line_bytes: 10,
                    budget: 4,
                },
                "line_too_large",
            ),
            (
                FsError::InvalidParameter {
                    parameter: "path",
                    reason: "empty".into(),
                },
                "invalid_parameter",
            ),
            (
                FsError::Io {
                    path: "x".into(),
                    source: io::Error::other("boom"),
                },
                "io_error",
            ),
        ];
        for (error, code) in samples {
            assert_eq!(error.code(), code);
            let payload = error.payload();
            assert_eq!(payload["code"], code);
            assert!(!payload["message"].as_str().unwrap().is_empty());
            assert!(payload["message"].as_str().unwrap().contains('`') || code == "io_error");
        }
    }

    #[test]
    fn io_kinds_map_to_specific_codes() {
        let missing = FsError::from_io("a.txt", io::Error::from(io::ErrorKind::NotFound));
        assert!(matches!(missing, FsError::NotFound { .. }));

        let denied = FsError::from_io("a.txt", io::Error::from(io::ErrorKind::PermissionDenied));
        assert!(matches!(denied, FsError::PermissionDenied { .. }));

        let other = FsError::from_io("a.txt", io::Error::other("disk on fire"));
        assert_eq!(other.code(), CODE_IO);
        assert!(other.to_string().contains("disk on fire"));
    }

    #[test]
    fn error_decodes_as_tool_error_with_structured_payload() {
        let error = FsError::NotText {
            path: "bin.dat".into(),
            line: 7,
        };
        let result = error.into_call_tool_result().expect("infallible");
        let CallToolResponse::Complete(result) = result else {
            panic!("expected a complete tool result");
        };
        assert_eq!(result.is_error, Some(true));
        assert_eq!(result.structured_content.unwrap()["code"], "not_text");
        // Clients that ignore structuredContent still see the prose.
        let text = result.content[0]
            .as_text()
            .expect("text block")
            .text
            .clone();
        assert!(text.contains("not UTF-8 text"), "{text}");
    }
}
