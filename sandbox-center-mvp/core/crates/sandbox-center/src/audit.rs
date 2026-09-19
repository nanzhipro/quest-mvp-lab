//! Tamper-evident JSONL audit trail.
//!
//! Every record is chained: `hash = sha256(prev_hash + body)` where `body` is the
//! canonical JSON of the record without its `hash` field. Rewriting, reordering
//! or deleting any line breaks verification, which `sandbox-center --verify-audit`
//! and the e2e suite both exercise.

use sandbox_core::timefmt::{iso8601_utc, now_unix_ms};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs::{File, OpenOptions};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};

pub const GENESIS: &str = "0000000000000000000000000000000000000000000000000000000000000000";

pub struct AuditWriter {
    file: File,
    path: PathBuf,
    seq: u64,
    prev: String,
}

impl AuditWriter {
    /// Open (or resume) today's audit file inside `dir`.
    pub fn open(dir: &Path) -> std::io::Result<Self> {
        std::fs::create_dir_all(dir)?;
        let day = iso8601_utc(now_unix_ms());
        let path = dir.join(format!("audit-{}.jsonl", &day[..10]));
        let (seq, prev) = if path.exists() {
            match last_record(&path) {
                Some(record) => (
                    record.get("seq").and_then(Value::as_u64).unwrap_or(0),
                    record
                        .get("hash")
                        .and_then(Value::as_str)
                        .unwrap_or(GENESIS)
                        .to_string(),
                ),
                None => (0, GENESIS.to_string()),
            }
        } else {
            (0, GENESIS.to_string())
        };
        let file = OpenOptions::new().create(true).append(true).open(&path)?;
        Ok(Self {
            file,
            path,
            seq,
            prev,
        })
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn append(
        &mut self,
        kind: &str,
        session_id: Option<&str>,
        data: Value,
    ) -> std::io::Result<Value> {
        self.seq += 1;
        let mut body = json!({
            "seq": self.seq,
            "ts": iso8601_utc(now_unix_ms()),
            "kind": kind,
            "data": data,
        });
        if let Some(session_id) = session_id {
            body["session_id"] = json!(session_id);
        }
        let hash = chain_hash(&self.prev, &body);
        let mut record = body;
        record["prev"] = json!(self.prev);
        record["hash"] = json!(hash);
        let line = serde_json::to_string(&record).unwrap_or_else(|_| "{}".to_string());
        self.file.write_all(line.as_bytes())?;
        self.file.write_all(b"\n")?;
        self.file.flush()?;
        self.prev = hash;
        Ok(record)
    }
}

/// `sha256(prev || canonical_json(body))`.
pub fn chain_hash(prev: &str, body: &Value) -> String {
    let mut hasher = Sha256::new();
    hasher.update(prev.as_bytes());
    hasher.update(serde_json::to_string(body).unwrap_or_default().as_bytes());
    hex::encode(hasher.finalize())
}

fn last_record(path: &Path) -> Option<Value> {
    let file = File::open(path).ok()?;
    let mut last = None;
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        if line.trim().is_empty() {
            continue;
        }
        if let Ok(value) = serde_json::from_str::<Value>(&line) {
            last = Some(value);
        }
    }
    last
}

/// Recompute the whole chain; returns the number of verified records.
pub fn verify(path: &Path) -> Result<usize, String> {
    let file =
        File::open(path).map_err(|error| format!("cannot open {}: {error}", path.display()))?;
    let mut prev = GENESIS.to_string();
    let mut count = 0usize;
    let mut expected_seq = 0u64;
    for (index, line) in BufReader::new(file).lines().enumerate() {
        let line = line.map_err(|error| format!("line {}: {error}", index + 1))?;
        if line.trim().is_empty() {
            continue;
        }
        let record: Value = serde_json::from_str(&line)
            .map_err(|error| format!("line {}: malformed JSON: {error}", index + 1))?;
        let hash = record
            .get("hash")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("line {}: missing hash", index + 1))?
            .to_string();
        let prev_field = record
            .get("prev")
            .and_then(Value::as_str)
            .ok_or_else(|| format!("line {}: missing prev", index + 1))?;
        if prev_field != prev {
            return Err(format!(
                "line {}: chain break (prev={prev_field}, expected={prev})",
                index + 1
            ));
        }
        let mut body = record.clone();
        if let Some(object) = body.as_object_mut() {
            object.remove("hash");
            object.remove("prev");
        }
        if chain_hash(&prev, &body) != hash {
            return Err(format!(
                "line {}: hash mismatch (record was modified)",
                index + 1
            ));
        }
        expected_seq += 1;
        if record.get("seq").and_then(Value::as_u64) != Some(expected_seq) {
            return Err(format!("line {}: unexpected seq number", index + 1));
        }
        prev = hash;
        count += 1;
    }
    Ok(count)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_dir(tag: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("sc-audit-{tag}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn appends_chained_records() {
        let dir = temp_dir("chain");
        let mut writer = AuditWriter::open(&dir).unwrap();
        let first = writer
            .append("session.opened", Some("s-1"), json!({"pid": 42}))
            .unwrap();
        let second = writer
            .append("violation.reported", Some("s-1"), json!({"target": "/x"}))
            .unwrap();
        assert_eq!(first["prev"], GENESIS);
        assert_eq!(second["prev"], first["hash"]);
        assert_eq!(verify(writer.path()).unwrap(), 2);
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn detects_tampering() {
        let dir = temp_dir("tamper");
        let mut writer = AuditWriter::open(&dir).unwrap();
        writer.append("a", None, json!({"n": 1})).unwrap();
        writer.append("b", None, json!({"n": 2})).unwrap();
        let path = writer.path().to_path_buf();
        let text = std::fs::read_to_string(&path).unwrap();
        std::fs::write(&path, text.replacen("\"n\":1", "\"n\":9", 1)).unwrap();
        let error = verify(&path).unwrap_err();
        assert!(error.contains("hash mismatch"), "{error}");
        let _ = std::fs::remove_dir_all(dir);
    }

    #[test]
    fn resumes_an_existing_file() {
        let dir = temp_dir("resume");
        let path;
        {
            let mut writer = AuditWriter::open(&dir).unwrap();
            writer.append("a", None, json!({})).unwrap();
            path = writer.path().to_path_buf();
        }
        let mut writer = AuditWriter::open(&dir).unwrap();
        let record = writer.append("b", None, json!({})).unwrap();
        assert_eq!(record["seq"], 2);
        assert_eq!(verify(&path).unwrap(), 2);
        let _ = std::fs::remove_dir_all(dir);
    }
}
