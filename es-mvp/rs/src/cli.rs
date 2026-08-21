//! 命令行接口（clap derive）。仅负责解析，语义归 [`crate::config::AppConfig`]。

use clap::Parser;

#[derive(Parser, Debug)]
#[command(
    name = "esmvp-rs",
    version,
    about = "目录级 AUTH_OPEN 管控：es_invert_muting + 内核授权缓存的最小验证"
)]
pub struct Cli {
    /// 监控目录（可重复）。不指定 = 全部 AUTH_OPEN 内核侧静音。
    #[arg(long = "watch", value_name = "DIR")]
    pub watch: Vec<String>,

    /// ALLOW 响应写入内核授权缓存（DENY 永不缓存）。
    #[arg(long)]
    pub cache: bool,

    /// 打印每条 ALLOW 事件（DENY 始终打印）。
    #[arg(long)]
    pub verbose: bool,

    /// 统计输出间隔（秒）。
    #[arg(long = "stats-interval", default_value_t = 10)]
    pub stats_interval: u64,
}

impl Cli {
    /// 测试友好的解析入口（不触发进程退出）。
    pub fn parse_from<I, T>(itr: I) -> Self
    where
        I: IntoIterator<Item = T>,
        T: Into<std::ffi::OsString> + Clone,
    {
        <Self as Parser>::parse_from(itr)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn defaults() {
        let cli = Cli::parse_from(["esmvp-rs"]);
        assert!(cli.watch.is_empty());
        assert!(!cli.cache);
        assert!(!cli.verbose);
        assert_eq!(cli.stats_interval, 10);
    }

    #[test]
    fn repeated_watch_and_flags() {
        let cli = Cli::parse_from([
            "esmvp-rs",
            "--watch",
            "/a",
            "--watch",
            "/b",
            "--cache",
            "--verbose",
            "--stats-interval",
            "3",
        ]);
        assert_eq!(cli.watch, ["/a", "/b"]);
        assert!(cli.cache && cli.verbose);
        assert_eq!(cli.stats_interval, 3);
    }

    #[test]
    fn unknown_arg_is_rejected() {
        assert!(<Cli as Parser>::try_parse_from(["esmvp-rs", "--bogus"]).is_err());
    }
}
