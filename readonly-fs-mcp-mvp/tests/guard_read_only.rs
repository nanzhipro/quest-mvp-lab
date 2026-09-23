//! Guard suite: the project's hard promise — "no mutation, no exposure" — as an
//! executable rule.
//!
//! This file scans `src/**/*.rs`, so it deliberately contains the very tokens it
//! forbids and therefore never scans itself. The forbidden lists are data, and
//! the rule for each list is one sentence.

use std::fs;
use std::path::{Path, PathBuf};

/// Filesystem APIs that create, modify, move or delete. A read-only server must
/// not name any of them, so a future edit cannot add a write path silently.
/// Function-like tokens carry the `(`, otherwise `fs::symlink` would also match
/// the read-only `fs::symlink_metadata`.
const MUTATING_APIS: &[&str] = &[
    "File::create",
    "OpenOptions",
    "fs::write(",
    "fs::remove_file(",
    "fs::remove_dir(",
    "fs::remove_dir_all(",
    "fs::rename(",
    "fs::copy(",
    "fs::hard_link(",
    "fs::soft_link(",
    "fs::symlink(",
    "fs::set_permissions(",
    "fs::set_times(",
    "fs::create_dir(",
    "create_dir_all(",
    "write_all(",
    "set_len(",
    ".truncate(",
    "remove_file(",
    "remove_dir(",
    "chmod(",
];

/// Networking APIs. The server talks over the pipes of its parent process only,
/// so no socket may ever be opened.
const NETWORK_APIS: &[&str] = &[
    "TcpListener",
    "TcpStream",
    "UnixListener",
    "UnixStream",
    "UdpSocket",
    "std::net",
    ".bind(",
    "listen(",
];

/// `unsafe` is never needed for reading files; keeping it out keeps the
/// confinement argument verifiable by reading the source.
const UNSAFE: &str = "unsafe";

fn source_files() -> Vec<PathBuf> {
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("src");
    let mut files = Vec::new();
    collect(&root, &mut files);
    files.sort();
    assert!(
        !files.is_empty(),
        "no sources found under {}",
        root.display()
    );
    files
}

fn collect(dir: &Path, files: &mut Vec<PathBuf>) {
    for entry in fs::read_dir(dir).expect("read_dir") {
        let path = entry.expect("entry").path();
        if path.is_dir() {
            collect(&path, files);
        } else if path.extension().is_some_and(|extension| extension == "rs") {
            files.push(path);
        }
    }
}

fn scan(token: &str) -> Vec<String> {
    let mut hits = Vec::new();
    for file in source_files() {
        let name = file.file_name().unwrap().to_string_lossy().to_string();
        let text = fs::read_to_string(&file).expect("read source");
        for (index, line) in production_code(&text, &name).lines().enumerate() {
            if line.contains(token) {
                hits.push(format!("{name}:{}: {}", index + 1, line.trim()));
            }
        }
    }
    hits
}

/// The shipped code of one file: everything before its test module. Fixtures
/// inside `#[cfg(test)]` legitimately create and remove files, so the guard
/// covers what the binary can do, not what the tests can do.
fn production_code(text: &str, file: &str) -> String {
    let Some(boundary) = text.find("#[cfg(test)]") else {
        return text.to_string();
    };
    assert_eq!(
        text.matches("#[cfg(test)]").count(),
        1,
        "{file} has more than one test module, which makes the scan boundary ambiguous"
    );
    text[..boundary].to_string()
}

fn assert_absent(tokens: &[&str], why: &str) {
    let mut violations = Vec::new();
    for token in tokens {
        violations.extend(scan(token));
    }
    assert!(
        violations.is_empty(),
        "{why}\nforbidden token found:\n{}",
        violations.join("\n")
    );
}

#[test]
fn source_contains_no_mutating_filesystem_api() {
    assert_absent(
        MUTATING_APIS,
        "a read-only server must not name a file-mutating API (SPEC.md §2)",
    );
}

#[test]
fn source_contains_no_network_api() {
    assert_absent(
        NETWORK_APIS,
        "the server is reachable only through its parent's pipes (SPEC.md §2)",
    );
}

#[test]
fn source_contains_no_unsafe_code() {
    let hits = scan(UNSAFE);
    assert!(hits.is_empty(), "unsafe found:\n{}", hits.join("\n"));
}

#[test]
fn every_tool_is_declared_once_with_a_unique_name() {
    let text = source_files()
        .iter()
        .map(|file| {
            let name = file.file_name().unwrap().to_string_lossy().to_string();
            production_code(&fs::read_to_string(file).expect("read source"), &name)
        })
        .collect::<Vec<_>>()
        .join("\n");
    for tool in ["list_directory", "read_file", "file_metadata"] {
        assert_eq!(
            text.matches(&format!("name = \"{tool}\"")).count(),
            1,
            "tool `{tool}` must be declared exactly once"
        );
    }
    assert_eq!(
        text.matches("#[tool(").count(),
        3,
        "the tool surface is exactly three tools (SPEC.md §3)"
    );
}

#[test]
fn read_only_annotations_are_declared_for_every_tool() {
    let text = source_files()
        .iter()
        .map(|file| {
            let name = file.file_name().unwrap().to_string_lossy().to_string();
            production_code(&fs::read_to_string(file).expect("read source"), &name)
        })
        .collect::<Vec<_>>()
        .join("\n");
    assert_eq!(
        text.matches("read_only_hint = true").count(),
        3,
        "every tool must advertise readOnlyHint"
    );
    assert_eq!(
        text.matches("destructive_hint = false").count(),
        3,
        "every tool must deny destructiveHint"
    );
}

#[test]
fn the_documented_error_codes_are_the_implemented_ones() {
    let error_source =
        fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("src/error.rs"))
            .expect("read error.rs");
    for code in [
        "outside_workspace",
        "not_found",
        "not_a_file",
        "not_a_directory",
        "permission_denied",
        "not_text",
        "line_too_large",
        "invalid_parameter",
        "io_error",
    ] {
        assert!(
            error_source.contains(&format!("\"{code}\"")),
            "documented code `{code}` is not defined"
        );
    }
}

#[test]
fn the_readme_documents_the_three_tools() {
    let readme = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("README.md"))
        .expect("README.md must exist");
    for tool in ["list_directory", "read_file", "file_metadata"] {
        assert!(readme.contains(tool), "README does not mention `{tool}`");
    }
    assert!(
        readme.contains("--root"),
        "README does not document the --root flag"
    );
}
