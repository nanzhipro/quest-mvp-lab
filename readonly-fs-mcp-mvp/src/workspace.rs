//! The workspace: one root directory plus the limits applied to every read,
//! and the path resolution that keeps every tool inside it.
//!
//! Containment is decided on *canonical* paths (symlinks resolved), so a link
//! that leads out of the root is refused rather than followed, and `..` cannot
//! be used to walk out of the tree.

use std::fs;
use std::io;
use std::path::{Path, PathBuf};

use crate::error::FsError;

/// Per-call budgets. Bounds are the server's, not the caller's: a request may
/// ask for less and is clamped to these.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Limits {
    /// Hard cap on `max_lines` for one `read_file` call.
    pub max_read_lines: u64,
    /// Hard cap on `max_entries` for one `list_directory` call.
    pub max_entries: u64,
    /// Hard cap on `max_depth` for one recursive `list_directory` call.
    pub max_depth: u32,
    /// Byte budget for the text returned by one `read_file` call.
    pub max_read_bytes: usize,
    /// Files up to this size are read to the end so `read_file` can report an
    /// exact `total_lines`; larger ones stop at the requested range.
    pub max_scan_bytes: u64,
}

impl Default for Limits {
    fn default() -> Self {
        Self {
            max_read_lines: 2_000,
            max_entries: 2_000,
            max_depth: 8,
            max_read_bytes: 512 * 1024,
            max_scan_bytes: 8 * 1024 * 1024,
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum WorkspaceError {
    #[error("workspace root `{path}` cannot be resolved: {source}")]
    Unresolvable { path: String, source: io::Error },
    #[error("workspace root `{path}` is not a directory")]
    NotADirectory { path: String },
}

/// What `locate` learned about one path.
#[derive(Debug)]
pub struct Located {
    /// Absolute path of the entry itself, inside the root.
    pub entry: PathBuf,
    /// Metadata of the entry as named (symlinks are not followed).
    pub metadata: fs::Metadata,
    /// Absolute path the entry resolves to, present only when it stays inside.
    pub target: Option<PathBuf>,
    /// The entry is a symlink whose target lies outside the workspace root.
    pub escapes: bool,
}

#[derive(Debug, Clone)]
pub struct Workspace {
    root: PathBuf,
    limits: Limits,
}

impl Workspace {
    /// Resolve the root once at startup: a server that cannot name its root
    /// should refuse to start rather than fail on every call.
    pub fn open(root: impl AsRef<Path>, limits: Limits) -> Result<Self, WorkspaceError> {
        let requested = root.as_ref();
        let display = requested.display().to_string();
        let root = requested
            .canonicalize()
            .map_err(|source| WorkspaceError::Unresolvable {
                path: display.clone(),
                source,
            })?;
        if !root.is_dir() {
            return Err(WorkspaceError::NotADirectory { path: display });
        }
        Ok(Self { root, limits })
    }

    pub fn root(&self) -> &Path {
        &self.root
    }

    pub fn limits(&self) -> Limits {
        self.limits
    }

    /// Root-relative display form, `"."` for the root itself.
    pub fn relative(&self, path: &Path) -> String {
        match path.strip_prefix(&self.root) {
            Ok(rest) if rest.as_os_str().is_empty() => ".".to_string(),
            Ok(rest) => rest.to_string_lossy().into_owned(),
            Err(_) => path.to_string_lossy().into_owned(),
        }
    }

    /// Canonical path of an existing entry, symlinks followed, inside the root.
    /// Used when the caller wants the *contents* (reading a file, listing a
    /// directory): a link that leaves the root is refused.
    pub fn resolve_followed(&self, requested: &str) -> Result<PathBuf, FsError> {
        let candidate = self.candidate(requested)?;
        let canonical = candidate
            .canonicalize()
            .map_err(|error| FsError::from_io(requested, error))?;
        self.confirm_inside(&canonical, requested)?;
        Ok(canonical)
    }

    /// Observe one entry without following it: the kernel resolves `.`/`..`
    /// and any symlink in the parent chain, so a path that names something
    /// outside the root is still refused, while a symlink *inside* the root is
    /// reported as itself (its target only when that target stays inside).
    pub fn locate(&self, requested: &str) -> Result<Located, FsError> {
        let candidate = self.candidate(requested)?;
        let metadata =
            fs::symlink_metadata(&candidate).map_err(|error| FsError::from_io(requested, error))?;

        if metadata.file_type().is_symlink() {
            let parent = candidate.parent().unwrap_or(&self.root);
            let canonical_parent = parent
                .canonicalize()
                .map_err(|error| FsError::from_io(requested, error))?;
            self.confirm_inside(&canonical_parent, requested)?;
            let entry = match candidate.file_name() {
                Some(name) => canonical_parent.join(name),
                None => canonical_parent,
            };
            match entry.canonicalize() {
                // A target inside the root is ordinary metadata.
                Ok(target) if target.starts_with(&self.root) => Ok(Located {
                    entry,
                    metadata,
                    target: Some(target),
                    escapes: false,
                }),
                // A target outside the root is the one thing not described.
                Ok(_) => Ok(Located {
                    entry,
                    metadata,
                    target: None,
                    escapes: true,
                }),
                // A dangling link reaches nothing, inside or outside.
                Err(_) => Ok(Located {
                    entry,
                    metadata,
                    target: None,
                    escapes: false,
                }),
            }
        } else {
            let canonical = candidate
                .canonicalize()
                .map_err(|error| FsError::from_io(requested, error))?;
            self.confirm_inside(&canonical, requested)?;
            Ok(Located {
                entry: canonical,
                metadata,
                target: None,
                escapes: false,
            })
        }
    }

    fn candidate(&self, requested: &str) -> Result<PathBuf, FsError> {
        if requested.is_empty() {
            return Err(FsError::InvalidParameter {
                parameter: "path",
                reason: "must not be empty".to_string(),
            });
        }
        if requested.contains('\0') {
            return Err(FsError::InvalidParameter {
                parameter: "path",
                reason: "must not contain a NUL byte".to_string(),
            });
        }
        let path = Path::new(requested);
        Ok(if path.is_absolute() {
            path.to_path_buf()
        } else {
            self.root.join(path)
        })
    }

    fn confirm_inside(&self, canonical: &Path, requested: &str) -> Result<(), FsError> {
        if canonical.starts_with(&self.root) {
            Ok(())
        } else {
            Err(FsError::OutsideWorkspace {
                path: requested.to_string(),
                root: self.root.display().to_string(),
            })
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use tempfile::TempDir;

    fn workspace(dir: &TempDir) -> Workspace {
        Workspace::open(dir.path(), Limits::default()).expect("open")
    }

    #[test]
    fn open_resolves_and_validates_the_root() {
        let dir = TempDir::new().unwrap();
        fs::create_dir(dir.path().join("inner")).unwrap();
        let ws = workspace(&dir);
        assert_eq!(ws.root(), dir.path().canonicalize().unwrap());
        assert_eq!(ws.limits(), Limits::default());

        let nested = Workspace::open(dir.path().join("inner"), Limits::default()).unwrap();
        assert!(nested.root().ends_with("inner"));

        let missing = Workspace::open(dir.path().join("nope"), Limits::default());
        assert!(matches!(missing, Err(WorkspaceError::Unresolvable { .. })));
        assert!(
            missing
                .unwrap_err()
                .to_string()
                .contains("cannot be resolved")
        );

        fs::write(dir.path().join("file"), b"x").unwrap();
        let file_root = Workspace::open(dir.path().join("file"), Limits::default());
        assert!(matches!(
            file_root,
            Err(WorkspaceError::NotADirectory { .. })
        ));
        assert!(
            file_root
                .unwrap_err()
                .to_string()
                .contains("not a directory")
        );
    }

    #[test]
    fn relative_names_the_root_and_children() {
        let dir = TempDir::new().unwrap();
        fs::create_dir(dir.path().join("docs")).unwrap();
        let ws = workspace(&dir);
        assert_eq!(ws.relative(ws.root()), ".");
        assert_eq!(ws.relative(&ws.root().join("docs")), "docs");
        assert_eq!(ws.relative(Path::new("/elsewhere")), "/elsewhere");
    }

    #[test]
    fn resolve_followed_accepts_inside_and_absolute_paths() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("a.txt"), b"hello").unwrap();
        fs::create_dir(dir.path().join("docs")).unwrap();
        let ws = workspace(&dir);

        assert_eq!(
            ws.resolve_followed("a.txt").unwrap(),
            ws.root().join("a.txt")
        );
        assert_eq!(
            ws.resolve_followed("./a.txt").unwrap(),
            ws.root().join("a.txt")
        );
        assert_eq!(
            ws.resolve_followed(ws.root().join("a.txt").to_str().unwrap())
                .unwrap(),
            ws.root().join("a.txt")
        );
        assert_eq!(ws.resolve_followed(".").unwrap(), ws.root());
        // `..` is fine while it stays inside.
        assert_eq!(
            ws.resolve_followed("docs/../a.txt").unwrap(),
            ws.root().join("a.txt")
        );
    }

    #[test]
    fn resolve_followed_refuses_escapes() {
        let parent = TempDir::new().unwrap();
        let root = parent.path().join("root");
        fs::create_dir_all(root.join("sub")).unwrap();
        fs::write(parent.path().join("secret.txt"), b"outside\n").unwrap();
        let ws = Workspace::open(&root, Limits::default()).unwrap();

        for request in [
            "../secret.txt",
            "/etc/hosts",
            "..",
            "sub/../../secret.txt",
            "sub/../../",
        ] {
            let error = ws.resolve_followed(request).unwrap_err();
            assert_eq!(
                error.code(),
                crate::error::CODE_OUTSIDE_WORKSPACE,
                "{request}"
            );
        }

        // A symlink that leads out is refused even though the link file is inside.
        symlink("/etc/hosts", ws.root().join("outside")).unwrap();
        let error = ws.resolve_followed("outside").unwrap_err();
        assert_eq!(error.code(), crate::error::CODE_OUTSIDE_WORKSPACE);
        assert!(
            error
                .to_string()
                .contains("resolves outside the workspace root")
        );

        // A symlink that stays inside is followed.
        fs::write(ws.root().join("a.txt"), b"hello").unwrap();
        symlink("a.txt", ws.root().join("inside")).unwrap();
        assert_eq!(
            ws.resolve_followed("inside").unwrap(),
            ws.root().join("a.txt")
        );
    }

    #[test]
    fn resolve_followed_reports_missing_paths() {
        let dir = TempDir::new().unwrap();
        let ws = workspace(&dir);
        assert_eq!(
            ws.resolve_followed("ghost.txt").unwrap_err().code(),
            crate::error::CODE_NOT_FOUND
        );
    }

    #[test]
    fn candidate_rejects_empty_and_nul() {
        let dir = TempDir::new().unwrap();
        let ws = workspace(&dir);
        assert_eq!(
            ws.resolve_followed("").unwrap_err().code(),
            crate::error::CODE_INVALID_PARAMETER
        );
        assert_eq!(
            ws.resolve_followed("a\0b").unwrap_err().code(),
            crate::error::CODE_INVALID_PARAMETER
        );
    }

    #[test]
    fn locate_describes_symlinks_without_leaking_outside_paths() {
        let dir = TempDir::new().unwrap();
        fs::write(dir.path().join("a.txt"), b"hello").unwrap();
        symlink("/etc/hosts", dir.path().join("escapes")).unwrap();
        symlink("a.txt", dir.path().join("inside")).unwrap();
        symlink("ghost", dir.path().join("dangling")).unwrap();
        let ws = workspace(&dir);

        let plain = ws.locate("a.txt").unwrap();
        assert!(plain.metadata.is_file());
        assert!(plain.target.is_none());
        assert!(!plain.escapes);
        assert_eq!(plain.entry, ws.root().join("a.txt"));

        let escaping = ws.locate("escapes").unwrap();
        assert!(escaping.metadata.file_type().is_symlink());
        assert!(escaping.escapes);
        assert!(escaping.target.is_none());

        let inside = ws.locate("inside").unwrap();
        assert_eq!(inside.target, Some(ws.root().join("a.txt")));
        assert!(!inside.escapes);

        let dangling = ws.locate("dangling").unwrap();
        assert!(dangling.target.is_none());
        assert!(!dangling.escapes);

        let missing = ws.locate("ghost").unwrap_err();
        assert_eq!(missing.code(), crate::error::CODE_NOT_FOUND);
    }

    #[test]
    fn locate_refuses_paths_naming_something_outside() {
        let parent = TempDir::new().unwrap();
        let root = parent.path().join("root");
        fs::create_dir(&root).unwrap();
        fs::write(parent.path().join("secret.txt"), b"outside\n").unwrap();
        let ws = Workspace::open(&root, Limits::default()).unwrap();

        assert_eq!(
            ws.locate("../secret.txt").unwrap_err().code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
        // `..` as the final component resolves to the parent of the root.
        assert_eq!(
            ws.locate("..").unwrap_err().code(),
            crate::error::CODE_OUTSIDE_WORKSPACE
        );
        assert_eq!(ws.locate(".").unwrap().entry, ws.root());
    }

    #[test]
    fn locate_reports_the_entry_itself_through_a_symlinked_parent() {
        let dir = TempDir::new().unwrap();
        fs::create_dir(dir.path().join("real")).unwrap();
        fs::write(dir.path().join("real/f.txt"), b"hi").unwrap();
        symlink("real", dir.path().join("link")).unwrap();
        let ws = workspace(&dir);

        let located = ws.locate("link/f.txt").unwrap();
        assert!(located.metadata.is_file());
        assert_eq!(located.entry, ws.root().join("real/f.txt"));
    }
}
