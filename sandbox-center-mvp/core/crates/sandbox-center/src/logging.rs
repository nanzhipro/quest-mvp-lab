//! Tiny line-oriented logger (`[ts][component][level] message`).
//!
//! Kept intentionally small: the audit trail is the machine-readable record,
//! this file is just for humans eyeballing `logs/sandbox-center.log`.

use sandbox_core::timefmt::{iso8601_utc, now_unix_ms};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::sync::Mutex;

pub struct Logger {
    component: String,
    file: Option<Mutex<File>>,
    echo_stderr: bool,
}

impl Logger {
    pub fn new(component: &str, path: Option<&std::path::Path>) -> Self {
        let file = path.and_then(|path| {
            if let Some(parent) = path.parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            OpenOptions::new()
                .create(true)
                .append(true)
                .open(path)
                .ok()
                .map(Mutex::new)
        });
        Self {
            component: component.to_string(),
            file,
            echo_stderr: true,
        }
    }

    pub fn without_stderr(mut self) -> Self {
        self.echo_stderr = false;
        self
    }

    pub fn log(&self, level: &str, message: &str) {
        let line = format!(
            "[{}][{}][{}] {}",
            iso8601_utc(now_unix_ms()),
            self.component,
            level,
            message
        );
        if self.echo_stderr {
            eprintln!("{line}");
        }
        if let Some(file) = &self.file {
            if let Ok(mut file) = file.lock() {
                let _ = writeln!(file, "{line}");
                let _ = file.flush();
            }
        }
    }

    pub fn info(&self, message: impl AsRef<str>) {
        self.log("I", message.as_ref());
    }

    pub fn warn(&self, message: impl AsRef<str>) {
        self.log("W", message.as_ref());
    }

    pub fn error(&self, message: impl AsRef<str>) {
        self.log("E", message.as_ref());
    }
}
