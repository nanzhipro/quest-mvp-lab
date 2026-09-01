//! 命令行参数定义。

use std::path::PathBuf;

use clap::Parser;

use crate::error::SensorError;
use crate::ffi;

/// ES NOTIFY-only AI Agent 行为感知传感器。
#[derive(Debug, Parser)]
#[command(name = "es-agent-sensor", version, about)]
pub struct Cli {
    /// Agent 工作目录，格式 id=path，可多次指定。
    #[arg(long = "agent", value_name = "ID=PATH", value_parser = parse_agent, required = true)]
    pub agents: Vec<(String, PathBuf)>,

    /// 事件 JSONL 输出文件（缺省 stdout）。
    #[arg(long, value_name = "FILE")]
    pub output: Option<PathBuf>,

    /// 默认订阅集之外显式加订的事件（write,readdir,signal），逗号分隔。
    #[arg(long, value_delimiter = ',', value_name = "EVENTS")]
    pub extra_events: Vec<String>,

    /// 内核级 target path 前缀静音（可多次）。默认不静音，保感知完整。
    #[arg(long = "mute-path", value_name = "PATH")]
    pub mute_paths: Vec<String>,

    /// 网络连接轮询间隔（秒），0 关闭。ESF 无 TCP/UDP 事件，此为快照补偿。
    #[arg(long, default_value_t = 2)]
    pub net_poll_interval: u64,

    /// 吞吐/丢弃统计输出间隔（秒，到 stderr），0 关闭周期输出。
    #[arg(long, default_value_t = 60)]
    pub stats_interval: u64,

    /// 诊断日志级别（stderr，tracing env-filter 语法）。
    #[arg(long, default_value = "info")]
    pub log_level: String,

    /// 运行 N 秒后自动优雅停机（0 = 不限）。
    /// 注意：签名后的 ES client 进程受 macOS 反篡改保护，SIGINT/SIGTERM
    /// 无效（实测 macOS 26.5.2），外部只能 SIGKILL；有界运行请用本选项。
    #[arg(long, default_value_t = 0)]
    pub duration: u64,
}

/// `--agent id=path` 解析：首个 `=` 分隔，两侧非空。
fn parse_agent(s: &str) -> Result<(String, PathBuf), String> {
    let Some((id, path)) = s.split_once('=') else {
        return Err("格式须为 id=path".into());
    };
    if id.is_empty() {
        return Err("agent id 不能为空".into());
    }
    if path.is_empty() {
        return Err("agent path 不能为空".into());
    }
    Ok((id.to_owned(), PathBuf::from(path)))
}

impl Cli {
    /// extra-events 名称 → 订阅掩码位。
    pub fn extra_mask(&self) -> Result<u32, SensorError> {
        let mut mask = 0;
        for name in &self.extra_events {
            if name.is_empty() {
                continue; // 显式传空串视为未加订
            }
            mask |= match name.as_str() {
                "write" => ffi::EVX_WRITE,
                "readdir" => ffi::EVX_READDIR,
                "signal" => ffi::EVX_SIGNAL,
                other => {
                    return Err(SensorError::Config(format!(
                        "未知 extra event `{other}`（可选: write,readdir,signal）"
                    )));
                }
            };
        }
        Ok(mask)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_agent_specs() {
        let (id, path) = parse_agent("hermes=/Users/x/.hermes").unwrap();
        assert_eq!(id, "hermes");
        assert_eq!(path, PathBuf::from("/Users/x/.hermes"));
        // 路径内含 = 也正确（split_once 只切首个）
        let (id, path) = parse_agent("a=/tmp/x=y").unwrap();
        assert_eq!(path, PathBuf::from("/tmp/x=y"));
        assert_eq!(id, "a");
        assert!(parse_agent("noequals").is_err());
        assert!(parse_agent("=/tmp").is_err());
        assert!(parse_agent("id=").is_err());
    }

    #[test]
    fn extra_mask_mapping() {
        let cli = Cli {
            agents: vec![("a".into(), PathBuf::from("/tmp"))],
            output: None,
            extra_events: vec!["write".into(), "signal".into()],
            mute_paths: vec![],
            net_poll_interval: 2,
            stats_interval: 60,
            log_level: "info".into(),
            duration: 0,
        };
        assert_eq!(cli.extra_mask().unwrap(), ffi::EVX_WRITE | ffi::EVX_SIGNAL);

        let cli = Cli {
            extra_events: vec!["bogus".into()],
            ..cli
        };
        assert!(cli.extra_mask().is_err());
    }

    #[test]
    fn cli_parsing_full() {
        let cli = Cli::try_parse_from([
            "es-agent-sensor",
            "--agent",
            "hermes=/tmp/a",
            "--agent",
            "claude=/tmp/b",
            "--output",
            "/tmp/out.jsonl",
            "--extra-events",
            "write,readdir",
            "--mute-path",
            "/System/",
            "--net-poll-interval",
            "0",
            "--stats-interval",
            "10",
            "--log-level",
            "debug",
        ])
        .unwrap();
        assert_eq!(cli.agents.len(), 2);
        assert_eq!(cli.output, Some(PathBuf::from("/tmp/out.jsonl")));
        assert_eq!(cli.extra_events, vec!["write", "readdir"]);
        assert_eq!(cli.mute_paths, vec!["/System/"]);
        assert_eq!(cli.net_poll_interval, 0);
        assert_eq!(cli.stats_interval, 10);
        assert_eq!(cli.log_level, "debug");
    }

    #[test]
    fn extra_mask_empty_by_default() {
        // 不传 --extra-events：空 vec，mask=0（曾用 default_value="" 导致
        // vec![""] 被当作未知事件名报错）。
        let cli = Cli::try_parse_from(["es-agent-sensor", "--agent", "a=/tmp"]).unwrap();
        assert!(cli.extra_events.is_empty());
        assert_eq!(cli.extra_mask().unwrap(), 0);
        // 显式空串容错
        let cli = Cli {
            agents: vec![("a".into(), PathBuf::from("/tmp"))],
            output: None,
            extra_events: vec![String::new()],
            mute_paths: vec![],
            net_poll_interval: 2,
            stats_interval: 60,
            log_level: "info".into(),
            duration: 0,
        };
        assert_eq!(cli.extra_mask().unwrap(), 0);
    }

    #[test]
    fn cli_requires_agent() {
        assert!(Cli::try_parse_from(["es-agent-sensor"]).is_err());
    }
}
