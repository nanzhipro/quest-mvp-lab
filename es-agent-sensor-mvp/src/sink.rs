//! JSONL 输出：stdout 或文件，写即 flush（事件流实时性优先于写吞吐）。

use std::fs::File;
use std::io::{self, Write};
use std::path::Path;

pub enum JsonlSink {
    Stdout(io::Stdout),
    File(File),
}

impl JsonlSink {
    pub fn stdout() -> Self {
        Self::Stdout(io::stdout())
    }

    pub fn file(path: &Path) -> io::Result<Self> {
        Ok(Self::File(File::create(path)?))
    }

    /// 写一行 JSON 并立即 flush。
    pub fn write_line(&mut self, line: &str) -> io::Result<()> {
        match self {
            Self::Stdout(out) => {
                let mut lock = out.lock();
                lock.write_all(line.as_bytes())?;
                lock.write_all(b"\n")?;
                lock.flush()
            }
            Self::File(f) => {
                f.write_all(line.as_bytes())?;
                f.write_all(b"\n")?;
                f.flush()
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn file_sink_writes_and_flushes() {
        let dir = std::env::temp_dir().join(format!("es-sensor-sink-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("out.jsonl");
        {
            let mut sink = JsonlSink::file(&path).unwrap();
            sink.write_line("{\"a\":1}").unwrap();
            sink.write_line("{\"b\":2}").unwrap();
        }
        let content = std::fs::read_to_string(&path).unwrap();
        assert_eq!(content, "{\"a\":1}\n{\"b\":2}\n");
        std::fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn stdout_sink_smoke() {
        // 冒烟：stdout 分支可写且不报错（输出混入测试日志，无害）。
        let mut sink = JsonlSink::stdout();
        sink.write_line("{\"smoke\":true}").unwrap();
    }

    #[test]
    fn file_sink_create_error() {
        let e = JsonlSink::file(std::path::Path::new("/nonexistent-dir-xyz/out.jsonl"));
        assert!(e.is_err());
    }
}
