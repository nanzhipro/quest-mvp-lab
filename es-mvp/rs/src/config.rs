//! 配置模型与 watch 目录规范化（realpath + 目录尾斜杠，对应静音规则的匹配语义）。

use std::fmt;
use std::path::PathBuf;
use std::time::Duration;

use crate::cli::Cli;

#[derive(Debug)]
pub struct AppConfig {
    pub watch_rules: Vec<String>,
    pub cache_allow: bool,
    pub verbose: bool,
    pub stats_interval: Duration,
}

impl AppConfig {
    pub fn from_cli(cli: &Cli) -> Result<Self, ConfigError> {
        let mut watch_rules = Vec::with_capacity(cli.watch.len());
        for dir in &cli.watch {
            watch_rules.push(normalize_watch_dir(dir)?);
        }
        Ok(Self {
            watch_rules,
            cache_allow: cli.cache,
            verbose: cli.verbose,
            stats_interval: Duration::from_secs(cli.stats_interval.max(1)),
        })
    }

    /// 运行模式描述（启动日志用）。
    pub fn mode(&self) -> Mode<'_> {
        if self.watch_rules.is_empty() {
            Mode::MuteAll
        } else {
            Mode::WatchOnly(&self.watch_rules)
        }
    }
}

pub enum Mode<'a> {
    /// 无 watch 目录：inversion + 空规则 = 全部 AUTH_OPEN 在内核侧抑制。
    MuteAll,
    /// 只接收这些目录下的 AUTH_OPEN。
    WatchOnly(&'a [String]),
}

impl fmt::Display for Mode<'_> {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Mode::MuteAll => write!(f, "mute-all"),
            Mode::WatchOnly(dirs) => write!(f, "watch-only dirs=[{}]", dirs.join(", ")),
        }
    }
}

#[derive(Debug)]
pub struct ConfigError {
    pub input: String,
    pub reason: String,
}

impl fmt::Display for ConfigError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "watch 目录无效 {}：{}", self.input, self.reason)
    }
}

impl std::error::Error for ConfigError {}

/// 规范化 watch 目录：展开 `~` → canonicalize（realpath，解析符号链接）→ 保证尾斜杠。
/// 尾斜杠是硬约定：target-prefix 匹配是字符串级的，"/foo/bar" 会误伤 "/foo/bar2"。
pub fn normalize_watch_dir(input: &str) -> Result<String, ConfigError> {
    let fail = |reason: String| ConfigError {
        input: input.to_owned(),
        reason,
    };
    let expanded = expand_tilde(input);
    let canon =
        std::fs::canonicalize(&expanded).map_err(|e| fail(format!("realpath 失败：{e}")))?;
    if !canon.is_dir() {
        return Err(fail("不是目录".to_owned()));
    }
    let mut s = canon.to_string_lossy().into_owned();
    if !s.ends_with('/') {
        s.push('/');
    }
    Ok(s)
}

fn expand_tilde(input: &str) -> PathBuf {
    if (input == "~" || input.starts_with("~/"))
        && let Some(home) = std::env::home_dir()
    {
        return home.join(input.trim_start_matches('~').trim_start_matches('/'));
    }
    PathBuf::from(input)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn normalizes_and_appends_trailing_slash() {
        let tmp = tempfile::tempdir().unwrap();
        let rule = normalize_watch_dir(tmp.path().to_str().unwrap()).unwrap();
        assert!(rule.ends_with('/'));
        assert!(rule.starts_with('/'));
    }

    #[test]
    fn resolves_symlinked_watch_dir() {
        // macOS 上 /tmp → /private/tmp：经符号链接传入的规则必须按真实路径静音
        let tmp = tempfile::tempdir().unwrap();
        let real = tmp.path().join("real");
        std::fs::create_dir(&real).unwrap();
        let link = tmp.path().join("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();

        let via_link = normalize_watch_dir(link.to_str().unwrap()).unwrap();
        let via_real = normalize_watch_dir(real.to_str().unwrap()).unwrap();
        assert_eq!(via_link, via_real);
        assert!(via_link.ends_with("/real/"), "{via_link}");
    }

    #[test]
    fn rejects_nonexistent_and_file() {
        assert!(normalize_watch_dir("/definitely/not/exist/esmvp").is_err());
        let tmp = tempfile::NamedTempFile::new().unwrap();
        assert!(normalize_watch_dir(tmp.path().to_str().unwrap()).is_err());
    }

    #[test]
    fn expands_home_tilde() {
        let rule = normalize_watch_dir("~/").unwrap();
        let home = std::env::home_dir().unwrap();
        assert_eq!(rule, format!("{}/", home.to_string_lossy()));
    }

    #[test]
    fn mode_reporting() {
        let cli = Cli::parse_from(["esmvp-rs"]);
        let cfg = AppConfig::from_cli(&cli).unwrap();
        assert_eq!(cfg.mode().to_string(), "mute-all");

        let cli = Cli::parse_from(["esmvp-rs", "--watch", "/tmp"]);
        let cfg = AppConfig::from_cli(&cli).unwrap();
        let text = cfg.mode().to_string();
        assert!(text.starts_with("watch-only"), "{text}");
        assert!(text.contains("/private/tmp/"), "{text}");
    }
}
