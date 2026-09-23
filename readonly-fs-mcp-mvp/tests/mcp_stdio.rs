//! End-to-end contract test: spawn the real binary, speak MCP over its stdio
//! pipes with the official client, and prove the workspace is never touched.
//!
//! Nothing here reaches into the library's internals — the assertions are made
//! on what a client actually receives: tool list, schemas, structured payloads,
//! tool-level error codes, and the on-disk state before and after.

use std::collections::BTreeMap;
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::fs::{MetadataExt, PermissionsExt, symlink};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use rmcp::ServiceExt;
use rmcp::model::{CallToolRequestParams, CallToolResponse, JsonObject, Tool};
use rmcp::transport::TokioChildProcess;
use serde_json::{Value, json};
use tempfile::TempDir;

const BINARY: &str = env!("CARGO_BIN_EXE_readonly-fs-mcp");

/// `<parent>/root` is the workspace; `<parent>/secret.txt` is a real file
/// outside it, and `escape` is a symlink leading out.
struct Fixture {
    parent: TempDir,
}

impl Fixture {
    fn new() -> Self {
        let parent = TempDir::new().expect("temp dir");
        let root = parent.path().join("root");
        fs::create_dir_all(root.join("docs/notes")).unwrap();
        fs::write(root.join("readme.md"), b"# Title\n\nbody line\n").unwrap();
        fs::write(root.join("docs/guide.md"), b"guide\nsecond\nthird\n").unwrap();
        fs::write(root.join(".hidden"), b"dot\n").unwrap();
        fs::write(root.join("bin.dat"), [0u8, 159, 146, 150]).unwrap();
        fs::write(parent.path().join("secret.txt"), b"outside the workspace\n").unwrap();
        symlink("/etc/hosts", root.join("escape")).unwrap();
        symlink("readme.md", root.join("alias")).unwrap();
        Self { parent }
    }

    fn root(&self) -> PathBuf {
        self.parent.path().join("root")
    }

    /// Everything that matters for "nothing was modified": names, sizes,
    /// modes, mtimes and the bytes of every file below the fixture.
    fn snapshot(&self) -> BTreeMap<String, (String, u64, u32, i64, Vec<u8>)> {
        let mut state = BTreeMap::new();
        collect(self.parent.path(), self.parent.path(), &mut state);
        state
    }
}

fn collect(
    base: &Path,
    dir: &Path,
    state: &mut BTreeMap<String, (String, u64, u32, i64, Vec<u8>)>,
) {
    let mut entries: Vec<_> = fs::read_dir(dir)
        .expect("read_dir")
        .map(|entry| entry.expect("entry"))
        .collect();
    entries.sort_by_key(|entry| entry.file_name());
    for entry in entries {
        let path = entry.path();
        let relative = path.strip_prefix(base).unwrap().display().to_string();
        let metadata = fs::symlink_metadata(&path).unwrap();
        let kind = if metadata.file_type().is_symlink() {
            "symlink".to_string()
        } else if metadata.is_dir() {
            "dir".to_string()
        } else {
            "file".to_string()
        };
        let body = if metadata.is_dir() {
            Vec::new()
        } else if metadata.file_type().is_symlink() {
            fs::read_link(&path)
                .unwrap()
                .display()
                .to_string()
                .into_bytes()
        } else {
            fs::read(&path).unwrap()
        };
        state.insert(
            relative,
            (
                kind,
                metadata.len(),
                metadata.permissions().mode(),
                metadata.mtime(),
                body,
            ),
        );
        if metadata.is_dir() {
            collect(base, &path, state);
        }
    }
}

async fn connect(
    root: &Path,
) -> (
    rmcp::service::RunningService<rmcp::RoleClient, ()>,
    tokio::process::ChildStderr,
) {
    let mut command = tokio::process::Command::new(BINARY);
    command.arg("--root").arg(root);
    let (transport, stderr) = TokioChildProcess::builder(command)
        .stderr(Stdio::piped())
        .spawn()
        .expect("spawn server");
    let client = ().serve(transport).await.expect("initialize");
    (client, stderr.expect("stderr is piped"))
}

fn call(name: &str, arguments: Value) -> CallToolRequestParams {
    let arguments: JsonObject = arguments.as_object().cloned().unwrap_or_default();
    CallToolRequestParams::new(name.to_string()).with_arguments(arguments)
}

fn complete(result: CallToolResponse) -> rmcp::model::CallToolResult {
    match result {
        CallToolResponse::Complete(result) => result,
        other => panic!("expected a complete tool result, got {other:?}"),
    }
}

fn structured(result: &rmcp::model::CallToolResult) -> Value {
    result
        .structured_content
        .clone()
        .expect("structuredContent is always present")
}

fn tool_error_code(label: &str, result: &rmcp::model::CallToolResult) -> String {
    assert_eq!(
        result.is_error,
        Some(true),
        "{label} should have failed: {result:?}"
    );
    let text = result.content[0]
        .as_text()
        .expect("a text block accompanies the payload")
        .text
        .clone();
    assert_eq!(
        text,
        structured(result).to_string(),
        "the text block mirrors the structured payload"
    );
    structured(result)["code"].as_str().unwrap().to_string()
}

fn sorted_tools(tools: &[Tool]) -> Vec<&Tool> {
    let mut tools: Vec<&Tool> = tools.iter().collect();
    tools.sort_by(|left, right| left.name.cmp(&right.name));
    tools
}

#[tokio::test]
async fn publishes_three_read_only_tools_over_the_wire() {
    let fixture = Fixture::new();
    let (client, _stderr) = connect(&fixture.root()).await;

    let published = client.list_all_tools().await.expect("tools/list");
    let tools = sorted_tools(&published);
    assert_eq!(
        tools
            .iter()
            .map(|tool| tool.name.as_ref())
            .collect::<Vec<_>>(),
        ["file_metadata", "list_directory", "read_file"]
    );
    for tool in &tools {
        let annotations = tool.annotations.as_ref().expect("annotations");
        assert_eq!(annotations.read_only_hint, Some(true), "{}", tool.name);
        assert_eq!(annotations.destructive_hint, Some(false), "{}", tool.name);
        assert_eq!(annotations.idempotent_hint, Some(true), "{}", tool.name);
        assert_eq!(annotations.open_world_hint, Some(false), "{}", tool.name);
        assert!(tool.input_schema.get("properties").is_some());
        assert!(
            tool.output_schema.is_some(),
            "{} has no outputSchema",
            tool.name
        );
    }

    let info = client.peer().peer_info().expect("peer info");
    assert_eq!(
        info.server_info.as_ref().expect("server info").name,
        "readonly-fs-mcp"
    );
    let instructions = info.instructions.clone().unwrap_or_default();
    assert!(instructions.contains(&fixture.root().display().to_string()));
    assert!(instructions.contains("Read-only filesystem access"));

    client.cancel().await.expect("shutdown");
}

#[tokio::test]
async fn listings_reads_and_metadata_round_trip_as_typed_payloads() {
    let fixture = Fixture::new();
    let (client, mut stderr) = connect(&fixture.root()).await;

    let listing = complete(
        client
            .call_tool_once(call(
                "list_directory",
                json!({"path": ".", "recursive": true, "max_depth": 3}),
            ))
            .await
            .expect("list_directory"),
    );
    assert_eq!(listing.is_error, Some(false));
    let listing: readonly_fs_mcp::ListDirectoryOutput =
        serde_json::from_value(structured(&listing)).expect("decodes into the contract type");
    assert_eq!(listing.path, ".");
    assert_eq!(
        listing
            .entries
            .iter()
            .map(|entry| entry.path.as_str())
            .collect::<Vec<_>>(),
        [
            "alias",
            "bin.dat",
            "docs",
            "escape",
            "readme.md",
            "docs/guide.md",
            "docs/notes",
        ]
    );
    assert!(listing.entries.iter().any(|e| e.name == "escape"));

    let read = complete(
        client
            .call_tool_once(call(
                "read_file",
                json!({"path": "docs/guide.md", "start_line": 2, "max_lines": 1}),
            ))
            .await
            .expect("read_file"),
    );
    let read: readonly_fs_mcp::ReadFileOutput =
        serde_json::from_value(structured(&read)).expect("decodes into the contract type");
    assert_eq!(read.content, "second");
    assert_eq!(read.start_line, 2);
    assert_eq!(read.end_line, Some(2));
    assert_eq!(read.total_lines, Some(3));
    assert!(read.truncated);

    let metadata = complete(
        client
            .call_tool_once(call("file_metadata", json!({"path": "alias"})))
            .await
            .expect("file_metadata"),
    );
    let metadata: readonly_fs_mcp::FileMetadataOutput =
        serde_json::from_value(structured(&metadata)).expect("decodes into the contract type");
    assert_eq!(metadata.symlink_target.as_deref(), Some("readme.md"));
    assert!(!metadata.escapes_workspace);
    assert_eq!(metadata.looks_like_text, Some(true));

    let escaping = complete(
        client
            .call_tool_once(call("file_metadata", json!({"path": "escape"})))
            .await
            .expect("file_metadata"),
    );
    let escaping: readonly_fs_mcp::FileMetadataOutput =
        serde_json::from_value(structured(&escaping)).expect("decodes into the contract type");
    assert!(escaping.escapes_workspace);
    assert_eq!(escaping.symlink_target, None);

    client.cancel().await.expect("shutdown");
    let mut banner = String::new();
    tokio::io::AsyncReadExt::read_to_string(&mut stderr, &mut banner)
        .await
        .unwrap();
    assert!(banner.contains("read-only"), "startup banner: {banner}");
}

#[tokio::test]
async fn failures_arrive_as_tool_errors_with_stable_codes() {
    let fixture = Fixture::new();
    let (client, _stderr) = connect(&fixture.root()).await;

    let cases = [
        (
            "list_directory",
            json!({"path": "../"}),
            "outside_workspace",
        ),
        (
            "list_directory",
            json!({"path": "escape"}),
            "outside_workspace",
        ),
        (
            "read_file",
            json!({"path": "../secret.txt"}),
            "outside_workspace",
        ),
        ("read_file", json!({"path": "escape"}), "outside_workspace"),
        ("read_file", json!({"path": "bin.dat"}), "not_text"),
        ("read_file", json!({"path": "docs"}), "not_a_file"),
        ("read_file", json!({"path": "ghost"}), "not_found"),
        (
            "read_file",
            json!({"path": "readme.md", "start_line": 0}),
            "invalid_parameter",
        ),
        (
            "file_metadata",
            json!({"path": "./../secret.txt"}),
            "outside_workspace",
        ),
        ("file_metadata", json!({"path": "ghost"}), "not_found"),
    ];

    for (tool, arguments, expected) in cases {
        let result = complete(
            client
                .call_tool_once(call(tool, arguments.clone()))
                .await
                .unwrap_or_else(|error| {
                    panic!("{tool} {arguments} failed at protocol level: {error}")
                }),
        );
        assert_eq!(
            tool_error_code(&format!("{tool} {arguments}"), &result),
            expected,
            "{tool} {arguments} produced {result:?}"
        );
    }

    client.cancel().await.expect("shutdown");
}

#[tokio::test]
async fn an_unknown_tool_is_a_protocol_error() {
    let fixture = Fixture::new();
    let (client, _stderr) = connect(&fixture.root()).await;

    let result = client
        .call_tool_once(call("write_file", json!({"path": "x", "content": "y"})))
        .await;
    assert!(result.is_err(), "a write verb must not exist at all");

    let result = client
        .call_tool_once(call("delete_path", json!({"path": "x"})))
        .await;
    assert!(result.is_err());

    client.cancel().await.expect("shutdown");
}

#[tokio::test]
async fn no_call_ever_changes_the_workspace() {
    let fixture = Fixture::new();
    let before = fixture.snapshot();
    assert!(!before.is_empty());

    let (client, _stderr) = connect(&fixture.root()).await;
    let attempts: Vec<(&str, Value)> = vec![
        ("list_directory", json!({})),
        ("list_directory", json!({"path": "docs", "recursive": true})),
        ("list_directory", json!({"path": "../", "recursive": true})),
        ("read_file", json!({"path": "readme.md"})),
        ("read_file", json!({"path": "escape"})),
        ("read_file", json!({"path": "ghost"})),
        ("file_metadata", json!({"path": "readme.md"})),
        ("file_metadata", json!({"path": "escape"})),
        ("file_metadata", json!({"path": "alias"})),
    ];
    for (tool, arguments) in attempts {
        client
            .call_tool_once(call(tool, arguments))
            .await
            .expect("call reaches the server");
    }
    client.cancel().await.expect("shutdown");

    assert_eq!(fixture.snapshot(), before, "the workspace changed");
}

#[tokio::test]
async fn reads_succeed_on_a_write_protected_workspace() {
    let fixture = Fixture::new();
    let root = fixture.root();
    let before = fixture.snapshot();
    fs::set_permissions(&root, fs::Permissions::from_mode(0o555)).expect("make root read-only");

    let (client, _stderr) = connect(&root).await;
    let listing = complete(
        client
            .call_tool_once(call("list_directory", json!({"path": "."})))
            .await
            .expect("list_directory"),
    );
    assert_eq!(listing.is_error, Some(false));
    let read = complete(
        client
            .call_tool_once(call("read_file", json!({"path": "readme.md"})))
            .await
            .expect("read_file"),
    );
    assert_eq!(read.is_error, Some(false));
    client.cancel().await.expect("shutdown");

    fs::set_permissions(&root, fs::Permissions::from_mode(0o755)).expect("restore permissions");
    assert_eq!(fixture.snapshot(), before);
}

#[tokio::test]
async fn a_root_that_cannot_be_used_fails_loudly() {
    let fixture = Fixture::new();
    let missing = fixture.parent.path().join("nope");

    let mut command = tokio::process::Command::new(BINARY);
    command.arg("--root").arg(&missing);
    let outcome = tokio::time::timeout(Duration::from_secs(10), command.output())
        .await
        .expect("the server must not start serving")
        .expect("spawn");
    assert_eq!(outcome.status.code(), Some(2));
    let stderr = String::from_utf8_lossy(&outcome.stderr);
    assert!(stderr.contains("cannot use"), "stderr: {stderr}");
    assert!(
        outcome.stdout.is_empty(),
        "stdout must stay protocol-only: {:?}",
        String::from_utf8_lossy(&outcome.stdout)
    );
}

/// The transport is a pipe, not a socket: the process writes newline-delimited
/// JSON-RPC to stdout and nothing else, and exits cleanly when stdin closes.
#[test]
fn stdout_carries_json_rpc_lines_only() {
    let fixture = Fixture::new();
    let mut child = Command::new(BINARY)
        .arg("--root")
        .arg(fixture.root())
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .expect("spawn server");

    let mut stdin = child.stdin.take().expect("stdin");
    let requests = [
        json!({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "raw-probe", "version": "0"}
        }}),
        json!({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
    ];
    for request in requests {
        writeln!(stdin, "{request}").expect("write request");
    }
    stdin.flush().unwrap();

    let mut stdout = BufReader::new(child.stdout.take().expect("stdout"));
    for expected_id in 1..=2 {
        let mut line = String::new();
        stdout.read_line(&mut line).expect("read response");
        let parsed: Value = serde_json::from_str(line.trim_end())
            .unwrap_or_else(|error| panic!("stdout line is not JSON ({error}): {line:?}"));
        assert_eq!(parsed["jsonrpc"], "2.0");
        assert_eq!(parsed["id"], expected_id);
    }

    // Closing stdin ends the session; the process must exit on its own.
    drop(stdin);
    let deadline = std::time::Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = child.try_wait().expect("try_wait") {
            assert!(status.success(), "exit status {status:?}");
            break;
        }
        assert!(
            std::time::Instant::now() < deadline,
            "server did not exit after stdin closed"
        );
        std::thread::sleep(Duration::from_millis(20));
    }
}
