//! The tool contract: parameter types, result types and the read-only
//! semantics behind each MCP tool.
//!
//! Everything here is pure: the functions take a [`Workspace`] plus already
//! deserialized parameters and either describe the filesystem or fail with an
//! [`FsError`]. No function opens a file for writing, and none of them can be
//! made to by any parameter — the surface has no write, move or delete verb.

use std::collections::VecDeque;
use std::fs::{self, File};
use std::io::{BufRead, BufReader, Read};
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::Path;

use chrono::{DateTime, SecondsFormat, Utc};
use schemars::JsonSchema;
use serde::{Deserialize, Serialize};

use crate::error::FsError;
use crate::workspace::Workspace;

/// Lines returned by `read_file` when the caller does not ask for a range.
pub const DEFAULT_READ_LINES: u64 = 200;
/// Entries returned by `list_directory` when the caller does not ask for more.
pub const DEFAULT_LIST_ENTRIES: u64 = 200;
/// Levels listed by a recursive `list_directory` when no depth is given.
pub const DEFAULT_LIST_DEPTH: u32 = 2;

/// Bytes inspected when deciding whether a file looks like UTF-8 text.
const TEXT_PROBE_BYTES: usize = 4096;

/// Kind of a directory entry, derived from `lstat` (symlinks are not followed).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
#[serde(rename_all = "lowercase")]
pub enum FileKind {
    File,
    Directory,
    Symlink,
    Other,
}

impl FileKind {
    fn of(file_type: &fs::FileType) -> Self {
        if file_type.is_symlink() {
            FileKind::Symlink
        } else if file_type.is_dir() {
            FileKind::Directory
        } else if file_type.is_file() {
            FileKind::File
        } else {
            FileKind::Other
        }
    }
}

/// Parameters of `list_directory`.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ListDirectoryParams {
    /// Directory to list: workspace-relative, or absolute inside the workspace
    /// root. Both `.` and `..` are accepted as long as the resolved path stays
    /// inside the root. Defaults to the workspace root.
    #[serde(default)]
    pub path: Option<String>,
    /// Include dotfiles and dot-directories (`.gitignore`, `.github/`).
    /// Defaults to false, which keeps a routine listing short.
    #[serde(default)]
    pub include_hidden: bool,
    /// Walk into subdirectories as well. Symlinks are never followed when
    /// walking. Defaults to false (a single level).
    #[serde(default)]
    pub recursive: bool,
    /// Levels returned when `recursive` is true: 1 returns only the direct
    /// children, 2 their children too. Defaults to 2, capped by the server.
    /// Ignored when `recursive` is false.
    #[serde(default)]
    pub max_depth: Option<u32>,
    /// Maximum number of entries returned. Defaults to 200, capped by the
    /// server; a capped listing reports `truncated: true`.
    #[serde(default)]
    pub max_entries: Option<u64>,
}

/// One entry of a listing.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct DirectoryEntry {
    /// Name of the entry (its last path component).
    pub name: String,
    /// Workspace-relative path — pass this to `read_file` or `file_metadata`.
    pub path: String,
    /// File, directory, symlink or something else.
    pub kind: FileKind,
    /// Size in bytes as recorded for the entry itself (for a symlink, the
    /// length of the link target string, not the file it points at).
    pub size_bytes: u64,
    /// Last modification time, RFC 3339 in UTC, when available.
    pub modified: Option<String>,
    /// Levels below the listed directory: 0 for a direct child.
    pub depth: u32,
}

/// Result of `list_directory`.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ListDirectoryOutput {
    /// Absolute path of the workspace root; nothing outside it is reachable.
    pub root: String,
    /// Workspace-relative path of the directory that was listed.
    pub path: String,
    /// Entries, breadth-first by depth, siblings in byte order by name.
    pub entries: Vec<DirectoryEntry>,
    /// Number of entries in `entries`.
    pub entry_count: usize,
    /// True when the entry budget stopped the walk: more entries exist.
    pub truncated: bool,
    /// Subdirectories that could not be read (permission denied), so their
    /// contents are missing rather than absent. Workspace-relative paths.
    pub unreadable: Vec<String>,
}

/// Parameters of `read_file`.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct ReadFileParams {
    /// File to read: workspace-relative, or absolute inside the workspace root.
    /// Must resolve inside the root (a symlink leaving the root is refused).
    pub path: String,
    /// First line to return, 1-based. Defaults to 1.
    #[serde(default)]
    pub start_line: Option<u64>,
    /// Maximum number of lines to return. Defaults to 200, capped by the
    /// server; the returned range can end earlier on the byte budget.
    #[serde(default)]
    pub max_lines: Option<u64>,
}

/// Result of `read_file`.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct ReadFileOutput {
    /// Absolute path of the workspace root; nothing outside it is reachable.
    pub root: String,
    /// Workspace-relative path of the file that was read.
    pub path: String,
    /// Size of the whole file in bytes.
    pub size_bytes: u64,
    /// Lines in the whole file; `null` when the returned range stopped before
    /// the end of the file, which is also when `truncated` is true.
    pub total_lines: Option<u64>,
    /// First line of `content`, 1-based.
    pub start_line: u64,
    /// Last line of `content`; `null` when the range starts past the end.
    pub end_line: Option<u64>,
    /// Number of lines in `content`.
    pub returned_lines: u64,
    /// True when the file continues beyond `end_line`: ask again with
    /// `start_line: end_line + 1` to page through it.
    pub truncated: bool,
    /// The requested lines joined by `\n` (no trailing newline). Empty when the
    /// range is empty.
    pub content: String,
}

/// Parameters of `file_metadata`.
#[derive(Debug, Clone, Deserialize, JsonSchema)]
#[serde(deny_unknown_fields)]
pub struct FileMetadataParams {
    /// Path to describe: workspace-relative, or absolute inside the workspace
    /// root. Directories, files, symlinks, sockets and devices are all accepted
    /// here; the path itself must stay inside the root.
    pub path: String,
}

/// Result of `file_metadata`.
#[derive(Debug, Clone, Serialize, Deserialize, JsonSchema)]
pub struct FileMetadataOutput {
    /// Absolute path of the workspace root; nothing outside it is reachable.
    pub root: String,
    /// Workspace-relative path of the described entry.
    pub path: String,
    /// File, directory, symlink or something else (socket, device, FIFO).
    pub kind: FileKind,
    /// Size in bytes: file contents, or the link target string for a symlink.
    pub size_bytes: u64,
    /// Last modification time, RFC 3339 in UTC, when available.
    pub modified: Option<String>,
    /// Creation (birth) time, RFC 3339 in UTC, when the filesystem records one.
    pub created: Option<String>,
    /// POSIX permissions in octal, e.g. `"0644"`.
    pub mode_octal: String,
    /// POSIX permissions symbolically, e.g. `"rw-r--r--"`.
    pub permissions: String,
    /// Owning user id.
    pub uid: u32,
    /// Owning group id.
    pub gid: u32,
    /// Hard link count.
    pub hard_links: u64,
    /// Inode number.
    pub inode: u64,
    /// Workspace-relative path this symlink resolves to. Present only when the
    /// target stays inside the workspace root; `null` for a dangling link or a
    /// target outside the root.
    pub symlink_target: Option<String>,
    /// The entry is a symlink whose target lies outside the workspace root;
    /// reading through it is refused, so the target path is not disclosed.
    pub escapes_workspace: bool,
    /// The file looks like UTF-8 text (`null` for anything that is not a
    /// readable regular file). A false value means `read_file` will refuse it.
    pub looks_like_text: Option<bool>,
}

/// List a directory inside the workspace.
pub fn list_directory(
    workspace: &Workspace,
    params: ListDirectoryParams,
) -> Result<ListDirectoryOutput, FsError> {
    let limits = workspace.limits();
    let requested = params.path.as_deref().unwrap_or(".");
    let root = workspace.resolve_followed(requested)?;
    let metadata = fs::metadata(&root).map_err(|error| FsError::from_io(requested, error))?;
    if !metadata.is_dir() {
        return Err(FsError::NotADirectory {
            path: workspace.relative(&root),
        });
    }

    let max_entries = params
        .max_entries
        .unwrap_or(DEFAULT_LIST_ENTRIES)
        .clamp(1, limits.max_entries);
    let max_depth = if params.recursive {
        params
            .max_depth
            .unwrap_or(DEFAULT_LIST_DEPTH)
            .clamp(1, limits.max_depth)
    } else {
        1
    };

    let mut entries: Vec<DirectoryEntry> = Vec::new();
    let mut unreadable: Vec<String> = Vec::new();
    let mut truncated = false;
    let mut queue: VecDeque<(std::path::PathBuf, u32)> = VecDeque::new();
    queue.push_back((root.clone(), 0));

    while let Some((dir, depth)) = queue.pop_front() {
        let listing = match fs::read_dir(&dir) {
            Ok(listing) => listing,
            Err(error) => {
                // The requested directory failing is the caller's error; a
                // nested one is reported as a gap in the result.
                if dir == root {
                    return Err(FsError::from_io(&workspace.relative(&dir), error));
                }
                unreadable.push(workspace.relative(&dir));
                continue;
            }
        };

        let mut children = Vec::new();
        for item in listing {
            match item {
                Ok(child) => children.push(child),
                Err(_) => {
                    unreadable.push(workspace.relative(&dir));
                    break;
                }
            }
        }
        children.sort_by_key(|child| child.file_name());

        for child in children {
            let name = child.file_name().to_string_lossy().into_owned();
            if !params.include_hidden && name.starts_with('.') {
                continue;
            }
            let Ok(file_type) = child.file_type() else {
                unreadable.push(workspace.relative(&child.path()));
                continue;
            };
            if entries.len() as u64 >= max_entries {
                truncated = true;
                break;
            }
            let metadata = child.metadata().ok();
            entries.push(DirectoryEntry {
                name,
                path: workspace.relative(&child.path()),
                kind: FileKind::of(&file_type),
                size_bytes: metadata.as_ref().map(|meta| meta.len()).unwrap_or(0),
                modified: metadata
                    .as_ref()
                    .and_then(|meta| meta.modified().ok())
                    .and_then(rfc3339),
                depth,
            });
            if file_type.is_dir() && depth + 1 < max_depth {
                queue.push_back((child.path(), depth + 1));
            }
        }

        if truncated {
            break;
        }
    }

    Ok(ListDirectoryOutput {
        root: workspace.root().display().to_string(),
        path: workspace.relative(&root),
        entry_count: entries.len(),
        entries,
        truncated,
        unreadable,
    })
}

/// Read a line range of a UTF-8 text file inside the workspace.
pub fn read_file(workspace: &Workspace, params: ReadFileParams) -> Result<ReadFileOutput, FsError> {
    let limits = workspace.limits();
    let requested = params.path.as_str();
    let path = workspace.resolve_followed(requested)?;
    let display = workspace.relative(&path);
    let metadata = fs::metadata(&path).map_err(|error| FsError::from_io(requested, error))?;
    if !metadata.is_file() {
        return Err(FsError::NotAFile { path: display });
    }

    let start_line = match params.start_line.unwrap_or(1) {
        0 => {
            return Err(FsError::InvalidParameter {
                parameter: "start_line",
                reason: "line numbers are 1-based, so 0 is not a valid line".to_string(),
            });
        }
        line => line,
    };
    let max_lines = params
        .max_lines
        .unwrap_or(DEFAULT_READ_LINES)
        .clamp(1, limits.max_read_lines);

    // A file within the scan budget is read to its end so `total_lines` is
    // exact rather than a guess; a larger one stops once the range is complete.
    let count_all_lines = metadata.len() <= limits.max_scan_bytes;
    let file = File::open(&path).map_err(|error| FsError::from_io(requested, error))?;
    let mut reader = BufReader::new(file);

    let mut line_buffer: Vec<u8> = Vec::new();
    let mut line_number: u64 = 0;
    let mut collected: u64 = 0;
    let mut content = String::new();
    let mut content_bytes: usize = 0;
    let mut reached_eof = true;

    loop {
        line_buffer.clear();
        let read = reader
            .read_until(b'\n', &mut line_buffer)
            .map_err(|error| FsError::from_io(requested, error))?;
        if read == 0 {
            break;
        }
        line_number += 1;
        if line_number < start_line {
            continue;
        }

        let mut line = line_buffer.as_slice();
        if let Some(rest) = line.strip_suffix(b"\n") {
            line = rest;
        }
        if let Some(rest) = line.strip_suffix(b"\r") {
            line = rest;
        }

        // A single line above the byte budget cannot be returned at all, so say
        // so instead of handing back an empty page the caller would retry.
        if collected == 0 && line.len() > limits.max_read_bytes {
            return Err(FsError::LineTooLarge {
                path: display.clone(),
                line: line_number,
                line_bytes: line.len(),
                budget: limits.max_read_bytes,
            });
        }

        // Reaching here means a line is available, so the result really is
        // truncated rather than merely capped.
        if collected >= max_lines || content_bytes + line.len() > limits.max_read_bytes {
            if count_all_lines {
                continue;
            }
            reached_eof = false;
            break;
        }

        let text = std::str::from_utf8(line).map_err(|_| FsError::NotText {
            path: display.clone(),
            line: line_number,
        })?;
        if text.contains('\0') {
            return Err(FsError::NotText {
                path: display.clone(),
                line: line_number,
            });
        }
        if collected > 0 {
            content.push('\n');
        }
        content.push_str(text);
        content_bytes += line.len();
        collected += 1;
    }

    let (total_lines, truncated) = if reached_eof {
        (Some(line_number), line_number > start_line - 1 + collected)
    } else {
        (None, true)
    };

    Ok(ReadFileOutput {
        root: workspace.root().display().to_string(),
        path: display,
        size_bytes: metadata.len(),
        total_lines,
        start_line,
        end_line: (collected > 0).then(|| start_line + collected - 1),
        returned_lines: collected,
        truncated,
        content,
    })
}

/// Describe one entry inside the workspace without following it.
pub fn file_metadata(
    workspace: &Workspace,
    params: FileMetadataParams,
) -> Result<FileMetadataOutput, FsError> {
    let located = workspace.locate(&params.path)?;
    let metadata = &located.metadata;
    let kind = FileKind::of(&metadata.file_type());

    let text_probe = if metadata.is_file() {
        Some(located.entry.clone())
    } else {
        located.target.clone().filter(|target| target.is_file())
    };

    Ok(FileMetadataOutput {
        root: workspace.root().display().to_string(),
        path: workspace.relative(&located.entry),
        kind,
        size_bytes: metadata.len(),
        modified: metadata.modified().ok().and_then(rfc3339),
        created: metadata.created().ok().and_then(rfc3339),
        mode_octal: format!("{:04o}", metadata.permissions().mode() & 0o7777),
        permissions: symbolic_permissions(metadata.permissions().mode()),
        uid: metadata.uid(),
        gid: metadata.gid(),
        hard_links: metadata.nlink(),
        inode: metadata.ino(),
        symlink_target: located.target.as_ref().map(|t| workspace.relative(t)),
        escapes_workspace: located.escapes,
        looks_like_text: text_probe.map(|path| looks_like_text(&path)),
    })
}

/// Heuristic: the first [`TEXT_PROBE_BYTES`] bytes are UTF-8 without NULs. A
/// character cut by the probe window is not held against the file.
fn looks_like_text(path: &Path) -> bool {
    let Ok(mut file) = File::open(path) else {
        return false;
    };
    let mut buffer = vec![0u8; TEXT_PROBE_BYTES];
    let read = match file.read(&mut buffer) {
        Ok(read) => read,
        Err(_) => return false,
    };
    let sample = &buffer[..read];
    if sample.contains(&0) {
        return false;
    }
    match std::str::from_utf8(sample) {
        Ok(_) => true,
        Err(error) => error.valid_up_to() + 4 >= sample.len(),
    }
}

fn rfc3339(time: std::time::SystemTime) -> Option<String> {
    let stamp: DateTime<Utc> = time.into();
    Some(stamp.to_rfc3339_opts(SecondsFormat::Secs, true))
}

fn symbolic_permissions(mode: u32) -> String {
    let mut text = String::with_capacity(9);
    for group in (0..3).rev() {
        let bits = (mode >> (group * 3)) & 0o7;
        for (mask, letter) in [(0o4, 'r'), (0o2, 'w'), (0o1, 'x')] {
            text.push(if bits & mask != 0 { letter } else { '-' });
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::workspace::Limits;
    use std::fs;
    use std::os::unix::fs::{PermissionsExt, symlink};
    use tempfile::TempDir;

    /// Workspace rooted at `<parent>/root`, with `<parent>/secret.txt` as a real
    /// file outside the root so escape attempts can be told apart from typos.
    fn fixture() -> (TempDir, Workspace) {
        let parent = TempDir::new().unwrap();
        let root = parent.path().join("root");
        fs::create_dir_all(root.join("docs/notes")).unwrap();
        fs::write(root.join("readme.md"), b"# Title\n\nbody\n").unwrap();
        fs::write(root.join("docs/guide.md"), b"guide\n").unwrap();
        fs::write(root.join("docs/notes/todo.txt"), b"first\nsecond\n").unwrap();
        fs::write(root.join(".hidden"), b"secret\n").unwrap();
        fs::write(root.join("bin.dat"), [0u8, 159, 146, 150, 1]).unwrap();
        fs::write(parent.path().join("secret.txt"), b"outside\n").unwrap();
        let ws = Workspace::open(&root, Limits::default()).unwrap();
        (parent, ws)
    }

    fn names(output: &ListDirectoryOutput) -> Vec<String> {
        output.entries.iter().map(|e| e.path.clone()).collect()
    }

    fn listing(ws: &Workspace, params: ListDirectoryParams) -> ListDirectoryOutput {
        list_directory(ws, params).expect("listing")
    }

    fn empty_listing(path: Option<&str>) -> ListDirectoryParams {
        ListDirectoryParams {
            path: path.map(str::to_string),
            include_hidden: false,
            recursive: false,
            max_depth: None,
            max_entries: None,
        }
    }

    fn read_params(path: &str, start_line: Option<u64>, max_lines: Option<u64>) -> ReadFileParams {
        ReadFileParams {
            path: path.to_string(),
            start_line,
            max_lines,
        }
    }

    #[test]
    fn list_defaults_to_the_root_and_sorts_siblings() {
        let (_dir, ws) = fixture();
        let output = listing(&ws, empty_listing(None));
        assert_eq!(output.path, ".");
        assert_eq!(output.root, ws.root().display().to_string());
        assert_eq!(names(&output), ["bin.dat", "docs", "readme.md"]);
        assert!(output.entries.iter().all(|entry| entry.depth == 0));
        assert!(!output.truncated);
        assert!(output.unreadable.is_empty());
        assert_eq!(output.entry_count, 3);
        let docs = output.entries.iter().find(|e| e.name == "docs").unwrap();
        assert_eq!(docs.kind, FileKind::Directory);
        assert!(docs.modified.is_some());
        assert!(docs.size_bytes > 0);
    }

    #[test]
    fn list_hides_dotfiles_unless_asked() {
        let (_dir, ws) = fixture();
        let mut params = empty_listing(None);
        params.include_hidden = true;
        let output = listing(&ws, params);
        assert!(names(&output).contains(&".hidden".to_string()));
    }

    #[test]
    fn list_recurses_to_the_requested_depth() {
        let (_parent, ws) = fixture();
        let mut params = empty_listing(None);
        params.recursive = true;
        let output = listing(&ws, params);
        // Two levels: the direct children (depth 0) and theirs (depth 1).
        assert_eq!(
            names(&output),
            [
                "bin.dat",
                "docs",
                "readme.md",
                "docs/guide.md",
                "docs/notes",
            ]
        );
        assert_eq!(output.entries.last().unwrap().depth, 1);

        let mut deeper = empty_listing(None);
        deeper.recursive = true;
        deeper.max_depth = Some(3);
        assert!(names(&listing(&ws, deeper)).contains(&"docs/notes/todo.txt".to_string()));

        let mut shallow = empty_listing(None);
        shallow.recursive = true;
        shallow.max_depth = Some(1);
        let output = listing(&ws, shallow);
        assert_eq!(names(&output), ["bin.dat", "docs", "readme.md"]);

        // max_depth is ignored without recursion.
        let mut ignored = empty_listing(None);
        ignored.max_depth = Some(5);
        assert_eq!(
            names(&listing(&ws, ignored)),
            ["bin.dat", "docs", "readme.md"]
        );
    }

    #[test]
    fn list_reports_truncation_at_the_entry_budget() {
        let (_parent, ws) = fixture();
        let mut params = empty_listing(None);
        params.recursive = true;
        params.max_entries = Some(2);
        let output = listing(&ws, params);
        assert_eq!(output.entry_count, 2);
        assert!(output.truncated);

        // A budget that exactly fits the tree is not truncation.
        let mut exact = empty_listing(None);
        exact.recursive = true;
        exact.max_entries = Some(5);
        let output = listing(&ws, exact);
        assert_eq!(output.entry_count, 5);
        assert!(!output.truncated);
    }

    #[test]
    fn list_caps_the_budget_at_the_server_limit() {
        let dir = TempDir::new().unwrap();
        for index in 0..5 {
            fs::write(dir.path().join(format!("f{index}")), b"x").unwrap();
        }
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_entries: 3,
                ..Limits::default()
            },
        )
        .unwrap();
        let mut params = empty_listing(None);
        params.max_entries = Some(999);
        let output = listing(&ws, params);
        assert_eq!(output.entry_count, 3);
        assert!(output.truncated);
    }

    #[test]
    fn list_never_follows_symlinks_and_reads_through_them_when_inside() {
        let (_dir, ws) = fixture();
        symlink("docs", ws.root().join("docs-link")).unwrap();
        let mut params = empty_listing(None);
        params.recursive = true;
        params.max_depth = Some(4);
        let output = listing(&ws, params);
        let link = output
            .entries
            .iter()
            .find(|e| e.name == "docs-link")
            .unwrap();
        assert_eq!(link.kind, FileKind::Symlink);
        // The link is listed, its target's children are listed once (via docs).
        assert!(!names(&output).iter().any(|p| p.starts_with("docs-link/")));
    }

    #[test]
    fn list_reports_an_unreadable_requested_directory() {
        let (_parent, ws) = fixture();
        // Search permission without read permission: the path still resolves,
        // but the listing itself cannot be opened.
        fs::set_permissions(ws.root().join("docs"), fs::Permissions::from_mode(0o111)).unwrap();
        let error = list_directory(&ws, empty_listing(Some("docs"))).unwrap_err();
        assert_eq!(error.code(), crate::error::CODE_PERMISSION_DENIED);
        fs::set_permissions(ws.root().join("docs"), fs::Permissions::from_mode(0o755)).unwrap();
    }

    #[test]
    fn kind_other_covers_sockets() {
        let (_parent, ws) = fixture();
        let _listener = std::os::unix::net::UnixListener::bind(ws.root().join("ipc.sock")).unwrap();

        let output = listing(&ws, empty_listing(None));
        let entry = output
            .entries
            .iter()
            .find(|entry| entry.name == "ipc.sock")
            .expect("the socket is listed");
        assert_eq!(entry.kind, FileKind::Other);

        let metadata = file_metadata(
            &ws,
            FileMetadataParams {
                path: "ipc.sock".into(),
            },
        )
        .unwrap();
        assert_eq!(metadata.kind, FileKind::Other);
        assert_eq!(metadata.looks_like_text, None);
    }

    #[test]
    fn list_reports_unreadable_subdirectories() {
        let (_parent, ws) = fixture();
        fs::set_permissions(
            ws.root().join("docs/notes"),
            fs::Permissions::from_mode(0o000),
        )
        .unwrap();
        let mut params = empty_listing(None);
        params.recursive = true;
        params.max_depth = Some(3);
        let output = listing(&ws, params);
        assert_eq!(output.unreadable, ["docs/notes"]);
        assert!(!names(&output).contains(&"docs/notes/todo.txt".to_string()));
        fs::set_permissions(
            ws.root().join("docs/notes"),
            fs::Permissions::from_mode(0o755),
        )
        .unwrap();
    }

    #[test]
    fn list_rejects_files_outsiders_and_ghosts() {
        let (_dir, ws) = fixture();
        assert_eq!(
            list_directory(&ws, empty_listing(Some("readme.md")))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_A_DIRECTORY
        );
        assert_eq!(
            list_directory(&ws, empty_listing(Some("../..")))
                .unwrap_err()
                .code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
        assert_eq!(
            list_directory(&ws, empty_listing(Some("nowhere")))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_FOUND
        );
    }

    #[test]
    fn read_returns_a_whole_small_file() {
        let (_dir, ws) = fixture();
        let output = read_file(&ws, read_params("readme.md", None, None)).unwrap();
        assert_eq!(output.content, "# Title\n\nbody");
        assert_eq!(output.total_lines, Some(3));
        assert_eq!(output.start_line, 1);
        assert_eq!(output.end_line, Some(3));
        assert_eq!(output.returned_lines, 3);
        assert!(!output.truncated);
        assert_eq!(output.path, "readme.md");
        assert_eq!(output.size_bytes, 14);
    }

    #[test]
    fn read_pages_through_a_file() {
        let (_dir, ws) = fixture();
        let first = read_file(&ws, read_params("docs/notes/todo.txt", None, Some(1))).unwrap();
        assert_eq!(first.content, "first");
        assert_eq!(first.end_line, Some(1));
        assert_eq!(first.total_lines, Some(2));
        assert!(first.truncated);

        let second = read_file(&ws, read_params("docs/notes/todo.txt", Some(2), Some(1))).unwrap();
        assert_eq!(second.content, "second");
        assert_eq!(second.start_line, 2);
        assert_eq!(second.end_line, Some(2));
        assert!(!second.truncated);
    }

    #[test]
    fn read_reports_an_empty_range_past_the_end() {
        let (_dir, ws) = fixture();
        let output = read_file(&ws, read_params("readme.md", Some(99), None)).unwrap();
        assert_eq!(output.content, "");
        assert_eq!(output.returned_lines, 0);
        assert_eq!(output.end_line, None);
        assert_eq!(output.total_lines, Some(3));
        assert!(!output.truncated);
    }

    #[test]
    fn read_handles_crlf_and_missing_trailing_newline() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("crlf.txt"), b"a\r\nb\r\n").unwrap();
        fs::write(dir.path().join("none.txt"), b"a\nb").unwrap();
        fs::write(dir.path().join("empty.txt"), b"").unwrap();
        fs::write(dir.path().join("blank.txt"), b"\n").unwrap();
        let ws = Workspace::open(dir.path(), Limits::default()).unwrap();

        let crlf = read_file(&ws, read_params("crlf.txt", None, None)).unwrap();
        assert_eq!(crlf.content, "a\nb");
        assert_eq!(crlf.total_lines, Some(2));

        let none = read_file(&ws, read_params("none.txt", None, None)).unwrap();
        assert_eq!(none.total_lines, Some(2));

        let empty = read_file(&ws, read_params("empty.txt", None, None)).unwrap();
        assert_eq!(empty.total_lines, Some(0));
        assert_eq!(empty.content, "");

        let blank = read_file(&ws, read_params("blank.txt", None, None)).unwrap();
        assert_eq!(blank.total_lines, Some(1));
        assert_eq!(blank.content, "");
    }

    #[test]
    fn read_stops_at_the_byte_budget_without_splitting_a_line() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("wide.txt"), b"aaaa\nbbbb\ncccc\n").unwrap();
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_read_bytes: 6,
                ..Limits::default()
            },
        )
        .unwrap();
        let output = read_file(&ws, read_params("wide.txt", None, None)).unwrap();
        assert_eq!(output.content, "aaaa");
        assert_eq!(output.returned_lines, 1);
        assert!(output.truncated);
        assert_eq!(output.total_lines, Some(3));
    }

    #[test]
    fn read_refuses_a_line_above_the_budget() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("huge-line.txt"), b"aaaaaaaaaa\nbb\n").unwrap();
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_read_bytes: 4,
                ..Limits::default()
            },
        )
        .unwrap();
        let error = read_file(&ws, read_params("huge-line.txt", None, None)).unwrap_err();
        assert_eq!(error.code(), crate::error::CODE_LINE_TOO_LARGE);
        assert!(error.to_string().contains("10 bytes"));
    }

    #[test]
    fn read_reports_the_exact_total_when_the_file_is_within_the_scan_budget() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("lines.txt"), b"1\n2\n3\n4\n").unwrap();
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_read_lines: 2,
                ..Limits::default()
            },
        )
        .unwrap();
        let output = read_file(&ws, read_params("lines.txt", None, Some(99))).unwrap();
        assert_eq!(output.returned_lines, 2);
        assert_eq!(output.total_lines, Some(4));
        assert!(output.truncated);
        // Paging from where the first read stopped returns the rest.
        let rest = read_file(&ws, read_params("lines.txt", Some(3), Some(99))).unwrap();
        assert_eq!(rest.total_lines, Some(4));
        assert_eq!(rest.content, "3\n4");
        assert!(!rest.truncated);
    }

    #[test]
    fn read_omits_total_lines_above_the_scan_budget() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("big.txt"), b"a\nb\nc\nd\n").unwrap();
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_scan_bytes: 2,
                ..Limits::default()
            },
        )
        .unwrap();
        let output = read_file(&ws, read_params("big.txt", None, Some(2))).unwrap();
        assert_eq!(output.content, "a\nb");
        assert_eq!(output.returned_lines, 2);
        assert_eq!(output.total_lines, None);
        assert!(output.truncated);
    }

    #[test]
    fn read_caps_max_lines_at_the_server_limit() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("lines.txt"), b"1\n2\n3\n4\n").unwrap();
        let ws = Workspace::open(
            dir.path(),
            Limits {
                max_read_lines: 2,
                ..Limits::default()
            },
        )
        .unwrap();
        let output = read_file(&ws, read_params("lines.txt", None, Some(99))).unwrap();
        assert_eq!(output.returned_lines, 2);
        assert!(output.truncated);
    }

    #[test]
    fn read_rejects_binary_nul_and_bad_ranges() {
        let (_dir, ws) = fixture();
        assert_eq!(
            read_file(&ws, read_params("bin.dat", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_TEXT
        );

        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("nul.txt"), b"ok\0no\n").unwrap();
        let nul_ws = Workspace::open(dir.path(), Limits::default()).unwrap();
        assert_eq!(
            read_file(&nul_ws, read_params("nul.txt", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_TEXT
        );

        assert_eq!(
            read_file(&ws, read_params("readme.md", Some(0), None))
                .unwrap_err()
                .code(),
            crate::error::CODE_INVALID_PARAMETER
        );
        assert_eq!(
            read_file(&ws, read_params("docs", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_A_FILE
        );
        assert_eq!(
            read_file(&ws, read_params("ghost", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_NOT_FOUND
        );
        assert_eq!(
            read_file(&ws, read_params("../secret.txt", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
    }

    #[test]
    fn read_refuses_a_symlink_that_leaves_the_workspace() {
        let (_dir, ws) = fixture();
        symlink("/etc/hosts", ws.root().join("hosts")).unwrap();
        assert_eq!(
            read_file(&ws, read_params("hosts", None, None))
                .unwrap_err()
                .code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
    }

    #[test]
    fn metadata_describes_files_directories_and_symlinks() {
        let (_dir, ws) = fixture();
        fs::set_permissions(
            ws.root().join("readme.md"),
            fs::Permissions::from_mode(0o640),
        )
        .unwrap();
        symlink("/etc/hosts", ws.root().join("escape")).unwrap();
        symlink("readme.md", ws.root().join("alias")).unwrap();
        symlink("ghost", ws.root().join("dangling")).unwrap();

        let file = file_metadata(
            &ws,
            FileMetadataParams {
                path: "readme.md".into(),
            },
        )
        .unwrap();
        assert_eq!(file.kind, FileKind::File);
        assert_eq!(file.mode_octal, "0640");
        assert_eq!(file.permissions, "rw-r-----");
        assert_eq!(file.hard_links, 1);
        assert!(file.inode > 0);
        assert_eq!(file.looks_like_text, Some(true));
        assert!(file.modified.is_some());
        assert_eq!(file.symlink_target, None);
        assert!(!file.escapes_workspace);
        assert_eq!(file.root, ws.root().display().to_string());

        let dir = file_metadata(
            &ws,
            FileMetadataParams {
                path: "docs".into(),
            },
        )
        .unwrap();
        assert_eq!(dir.kind, FileKind::Directory);
        assert_eq!(dir.looks_like_text, None);

        let escaping = file_metadata(
            &ws,
            FileMetadataParams {
                path: "escape".into(),
            },
        )
        .unwrap();
        assert_eq!(escaping.kind, FileKind::Symlink);
        assert!(escaping.escapes_workspace);
        assert_eq!(escaping.symlink_target, None);
        assert_eq!(escaping.looks_like_text, None);

        let alias = file_metadata(
            &ws,
            FileMetadataParams {
                path: "alias".into(),
            },
        )
        .unwrap();
        assert_eq!(alias.symlink_target.as_deref(), Some("readme.md"));
        assert_eq!(alias.looks_like_text, Some(true));

        let dangling = file_metadata(
            &ws,
            FileMetadataParams {
                path: "dangling".into(),
            },
        )
        .unwrap();
        assert_eq!(dangling.symlink_target, None);
        assert!(!dangling.escapes_workspace);

        let binary = file_metadata(
            &ws,
            FileMetadataParams {
                path: "bin.dat".into(),
            },
        )
        .unwrap();
        assert_eq!(binary.looks_like_text, Some(false));
    }

    #[test]
    fn metadata_rejects_escapes_and_missing_paths() {
        let (_dir, ws) = fixture();
        assert_eq!(
            file_metadata(
                &ws,
                FileMetadataParams {
                    path: "../secret.txt".into()
                }
            )
            .unwrap_err()
            .code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
        assert_eq!(
            file_metadata(
                &ws,
                FileMetadataParams {
                    path: "ghost".into()
                }
            )
            .unwrap_err()
            .code(),
            crate::error::CODE_NOT_FOUND
        );
    }

    #[test]
    fn looks_like_text_tolerates_a_cut_character_and_rejects_nul() {
        let dir = TempDir::new().unwrap();
        let cut = dir.path().join("cut.bin");
        let mut bytes = vec![b'a'; TEXT_PROBE_BYTES - 1];
        bytes.extend_from_slice("中".as_bytes());
        fs::write(&cut, &bytes).unwrap();
        assert!(looks_like_text(&cut));

        let nul = dir.path().join("nul.bin");
        fs::write(&nul, b"ok\0").unwrap();
        assert!(!looks_like_text(&nul));

        assert!(!looks_like_text(&dir.path().join("missing")));
        assert!(!looks_like_text(dir.path()));
    }

    #[test]
    fn symbolic_permissions_covers_every_bit() {
        assert_eq!(symbolic_permissions(0o000), "---------");
        assert_eq!(symbolic_permissions(0o755), "rwxr-xr-x");
        assert_eq!(symbolic_permissions(0o640), "rw-r-----");
        assert_eq!(symbolic_permissions(0o4755), "rwxr-xr-x");
    }
}
