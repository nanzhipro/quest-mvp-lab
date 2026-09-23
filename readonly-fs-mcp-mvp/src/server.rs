//! The MCP binding: three read-only tools wired to [`crate::tools`].
//!
//! The tool set is intentionally minimal — one way to look, one way to read,
//! one way to measure — so a model cannot pick a verb that changes anything.
//! Every tool advertises `readOnlyHint: true` / `destructiveHint: false`, and
//! the parameter and result schemas are derived from the Rust types in
//! [`crate::tools`], which is what a client sees in `tools/list`.

use std::sync::Arc;

use rmcp::handler::server::router::tool::ToolRouter;
use rmcp::handler::server::wrapper::{Json, Parameters};
use rmcp::model::{Implementation, ServerCapabilities, ServerConfig};
use rmcp::{ServerHandler, ServiceExt, tool, tool_handler, tool_router};

use crate::error::FsError;
use crate::tools::{
    self, FileMetadataOutput, FileMetadataParams, ListDirectoryOutput, ListDirectoryParams,
    ReadFileOutput, ReadFileParams,
};
use crate::workspace::Workspace;

pub const SERVER_NAME: &str = "readonly-fs-mcp";

/// The read-only MCP server: a workspace plus the tool router.
#[derive(Debug, Clone)]
pub struct FsServer {
    workspace: Arc<Workspace>,
    tool_router: ToolRouter<Self>,
}

impl FsServer {
    pub fn new(workspace: Workspace) -> Self {
        Self {
            workspace: Arc::new(workspace),
            tool_router: Self::tool_router(),
        }
    }

    pub fn workspace(&self) -> &Workspace {
        &self.workspace
    }

    /// Serve MCP over this process's stdin/stdout. The absence of a socket is
    /// the reason the server is unreachable from anywhere but its parent.
    pub async fn serve_stdio(self) -> anyhow::Result<()> {
        let running = self.serve(rmcp::transport::stdio()).await?;
        running.waiting().await?;
        Ok(())
    }

    /// What the model is told before it calls anything: the root it is confined
    /// to, the path rules, and that no write verb exists.
    pub fn instructions(&self) -> String {
        let limits = self.workspace.limits();
        format!(
            "Read-only filesystem access to the workspace `{root}`. \
             `list_directory` lists a directory (name, kind, size, modification time; optional recursion), \
             `read_file` returns a UTF-8 text file by line range, \
             `file_metadata` describes one entry (kind, size, times, POSIX permissions, owner, symlink target). \
             Every `path` is resolved against the workspace root and must stay inside it: relative paths resolve against the root, \
             absolute paths are accepted only inside the root, and a symlink leading outside the root is refused. \
             Nothing here can create, modify, move or delete a file. \
             Per-call budgets: {lines} lines, {entries} listing entries, {depth} recursion levels; a response that hits a budget reports `truncated: true`.",
            root = self.workspace.root().display(),
            lines = limits.max_read_lines,
            entries = limits.max_entries,
            depth = limits.max_depth,
        )
    }
}

#[tool_router(router = tool_router)]
impl FsServer {
    #[tool(
        name = "list_directory",
        title = "List a directory",
        description = "List the entries of a directory inside the read-only workspace: name, workspace-relative path, kind (file/directory/symlink/other), size in bytes and modification time. \
                       Use `recursive: true` for a tree overview. Dotfiles are skipped unless `include_hidden` is true, and symlinks are listed but never followed while walking. \
                       This tool only reads; it cannot change anything. \
                       Errors carry a stable `code` (outside_workspace, not_found, not_a_directory, permission_denied).",
        annotations(
            title = "List a directory",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn list_directory(
        &self,
        Parameters(params): Parameters<ListDirectoryParams>,
    ) -> Result<Json<ListDirectoryOutput>, FsError> {
        tools::list_directory(&self.workspace, params).map(Json)
    }

    #[tool(
        name = "read_file",
        title = "Read a UTF-8 text file",
        description = "Read a range of lines from a UTF-8 text file inside the read-only workspace. \
                       Returns the lines plus `size_bytes`, `total_lines`, `start_line`/`end_line` and `truncated`, so a large file is paged by asking again with `start_line: end_line + 1`. \
                       Binary or non-UTF-8 files are refused with code `not_text` (use `file_metadata` for those, it never needs the contents). \
                       This tool only reads; it cannot change anything. \
                       Errors carry a stable `code` (outside_workspace, not_found, not_a_file, not_text, line_too_large, invalid_parameter, permission_denied).",
        annotations(
            title = "Read a UTF-8 text file",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn read_file(
        &self,
        Parameters(params): Parameters<ReadFileParams>,
    ) -> Result<Json<ReadFileOutput>, FsError> {
        tools::read_file(&self.workspace, params).map(Json)
    }

    #[tool(
        name = "file_metadata",
        title = "Describe one path",
        description = "Return filesystem metadata for one path inside the read-only workspace: kind, size, modification and creation time, POSIX permissions, owner uid/gid, inode and hard link count, \
                       and for a symlink its target when that target stays inside the workspace (`escapes_workspace` flags one that does not). \
                       `looks_like_text` reports whether `read_file` will accept the file. \
                       Use this to size something up before reading it. This tool only reads; it cannot change anything. \
                       Errors carry a stable `code` (outside_workspace, not_found, permission_denied).",
        annotations(
            title = "Describe one path",
            read_only_hint = true,
            destructive_hint = false,
            idempotent_hint = true,
            open_world_hint = false
        )
    )]
    async fn file_metadata(
        &self,
        Parameters(params): Parameters<FileMetadataParams>,
    ) -> Result<Json<FileMetadataOutput>, FsError> {
        tools::file_metadata(&self.workspace, params).map(Json)
    }
}

#[tool_handler(router = self.tool_router)]
impl ServerHandler for FsServer {
    fn get_info(&self) -> ServerConfig {
        ServerConfig::new(ServerCapabilities::builder().enable_tools().build())
            .with_server_info(Implementation::new(SERVER_NAME, env!("CARGO_PKG_VERSION")))
            .with_instructions(self.instructions())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::tools::{ListDirectoryParams, ReadFileParams};
    use crate::workspace::Limits;
    use rmcp::handler::server::tool::IntoCallToolResult;
    use rmcp::model::Tool;
    use std::fs;
    use tempfile::TempDir;

    fn server() -> (TempDir, FsServer) {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("readme.md"), b"# Title\nbody\n").unwrap();
        let workspace = Workspace::open(dir.path(), Limits::default()).unwrap();
        (dir, FsServer::new(workspace))
    }

    fn tools_of(server: &FsServer) -> Vec<Tool> {
        let mut tools = server.tool_router.list_all();
        tools.sort_by(|left, right| left.name.cmp(&right.name));
        tools
    }

    fn error_code(response: &rmcp::model::CallToolResponse) -> String {
        let rmcp::model::CallToolResponse::Complete(result) = response else {
            panic!("expected a complete result");
        };
        assert_eq!(result.is_error, Some(true));
        result.structured_content.clone().unwrap()["code"]
            .as_str()
            .unwrap()
            .to_string()
    }

    #[test]
    fn publishes_exactly_three_read_only_tools() {
        let (_dir, server) = server();
        let tools = tools_of(&server);
        assert_eq!(
            tools
                .iter()
                .map(|tool| tool.name.as_ref())
                .collect::<Vec<_>>(),
            ["file_metadata", "list_directory", "read_file"]
        );
        for tool in &tools {
            let annotations = tool.annotations.as_ref().expect("annotations");
            assert_eq!(annotations.read_only_hint, Some(true));
            assert_eq!(annotations.destructive_hint, Some(false));
            assert_eq!(annotations.idempotent_hint, Some(true));
            assert_eq!(annotations.open_world_hint, Some(false));
            assert!(tool.description.as_ref().unwrap().contains("only reads"));
            assert!(
                tool.output_schema.is_some(),
                "{} has no output schema",
                tool.name
            );
        }
    }

    #[test]
    fn schemas_describe_paths_and_require_what_matters() {
        let (_dir, server) = server();
        let tools = tools_of(&server);
        let find = |name: &str| {
            tools
                .iter()
                .find(|tool| tool.name.as_ref() == name)
                .unwrap()
                .clone()
        };

        let listed = find("list_directory");
        let schema = serde_json::to_value(&listed.input_schema).unwrap();
        println!("{}", serde_json::to_string_pretty(&schema).unwrap());
        let kinds = &schema["properties"]["path"]["type"];
        assert!(
            kinds == "string" || kinds == &serde_json::json!(["string", "null"]),
            "path must be described as a string: {schema}"
        );
        assert_eq!(schema["properties"]["recursive"]["type"], "boolean");
        assert!(
            schema["properties"]["path"]["description"]
                .as_str()
                .unwrap()
                .contains("inside")
        );
        assert_ne!(schema.get("required"), Some(&serde_json::json!([])));

        for name in ["read_file", "file_metadata"] {
            let tool = find(name);
            let schema = serde_json::to_value(&tool.input_schema).unwrap();
            println!("{}", serde_json::to_string_pretty(&schema).unwrap());
            assert_eq!(schema["required"], serde_json::json!(["path"]), "{name}");
        }

        let read = find("read_file");
        let schema = serde_json::to_value(&read.input_schema).unwrap();
        assert!(
            schema["properties"]["start_line"]["description"]
                .as_str()
                .unwrap()
                .contains("1-based")
        );
    }

    #[test]
    fn instructions_name_the_root_and_the_rules() {
        let (_dir, server) = server();
        let instructions = server.instructions();
        assert!(instructions.contains("Read-only filesystem access to the workspace"));
        assert!(instructions.contains(&server.workspace().root().display().to_string()));
        assert!(instructions.contains("Nothing here can create, modify, move or delete a file"));
        assert!(instructions.contains("truncated: true"));
        assert!(instructions.contains("2000 lines"));
    }

    #[test]
    fn get_info_reports_capabilities_and_identity() {
        let (_dir, server) = server();
        let info = server.get_info();
        assert_eq!(info.server_info.name, SERVER_NAME);
        assert_eq!(info.server_info.version, env!("CARGO_PKG_VERSION"));
        assert!(info.capabilities.tools.is_some());
        assert_eq!(
            info.instructions.as_deref(),
            Some(server.instructions().as_str())
        );
    }

    #[tokio::test]
    async fn tools_return_structured_success_payloads() {
        let (_dir, server) = server();
        let listing = server
            .list_directory(Parameters(ListDirectoryParams {
                path: None,
                include_hidden: false,
                recursive: false,
                max_depth: None,
                max_entries: None,
            }))
            .await
            .expect("listing");
        assert_eq!(listing.0.entry_count, 1);
        assert_eq!(listing.0.entries[0].name, "readme.md");

        let read = server
            .read_file(Parameters(ReadFileParams {
                path: "readme.md".into(),
                start_line: None,
                max_lines: None,
            }))
            .await
            .expect("read");
        assert_eq!(read.0.content, "# Title\nbody");
        assert!(!read.0.truncated);
    }

    #[tokio::test]
    async fn tool_failures_are_tool_errors_with_a_code() {
        let (_dir, server) = server();
        let outside = server
            .list_directory(Parameters(ListDirectoryParams {
                path: Some("../../".into()),
                include_hidden: false,
                recursive: false,
                max_depth: None,
                max_entries: None,
            }))
            .await
            .err()
            .expect("outside workspace");
        let response = outside.into_call_tool_result().expect("infallible");
        assert_eq!(error_code(&response), "outside_workspace");

        let binary = server
            .read_file(Parameters(ReadFileParams {
                path: "readme.md".into(),
                start_line: Some(0),
                max_lines: None,
            }))
            .await
            .err()
            .expect("bad line");
        let response = binary.into_call_tool_result().expect("infallible");
        assert_eq!(error_code(&response), "invalid_parameter");
    }

    #[test]
    fn unknown_parameter_names_are_rejected() {
        let params: Result<ListDirectoryParams, _> = serde_json::from_value(serde_json::json!({
            "path": ".",
            "includeHidden": true
        }));
        assert!(params.is_err(), "a misnamed flag must not be ignored");
    }

    #[test]
    fn router_exposes_every_route() {
        let (_dir, server) = server();
        assert!(server.tool_router.has_route("list_directory"));
        assert!(server.tool_router.has_route("read_file"));
        assert!(server.tool_router.has_route("file_metadata"));
        assert!(!server.tool_router.has_route("write_file"));
        assert!(!server.tool_router.has_route("delete_path"));
    }
}
