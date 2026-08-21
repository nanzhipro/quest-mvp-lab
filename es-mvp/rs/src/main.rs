use std::process::ExitCode;

use clap::Parser;
use tracing_subscriber::EnvFilter;

use esmvp_rs::app;
use esmvp_rs::cli::Cli;
use esmvp_rs::config::AppConfig;

fn main() -> ExitCode {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info")),
        )
        .with_target(false)
        .with_ansi(false) // 关闭颜色转义：重定向到文件/管道后仍可 grep、可被脚本消费
        .compact()
        .init();

    let cli = Cli::parse();
    let config = match AppConfig::from_cli(&cli) {
        Ok(config) => config,
        Err(e) => {
            tracing::error!(error = %e, "配置无效");
            return ExitCode::from(2);
        }
    };

    match app::run(&config) {
        Ok(()) => ExitCode::SUCCESS, // run 永不返回，此处仅为类型完整
        Err(e) => {
            tracing::error!(error = %e, "启动失败");
            ExitCode::FAILURE
        }
    }
}
