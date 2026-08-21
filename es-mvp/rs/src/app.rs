//! 应用编排：ES 初始化序列（顺序有官方约束）+ 事件处理 + 周期统计 + 信号收尾。

use std::sync::{Arc, OnceLock};
use std::thread;
use std::time::Duration;

use signal_hook::consts::{SIGINT, SIGTERM};
use signal_hook::iterator::Signals;
use tracing::{error, info, warn};

use crate::backend::{EsBackend, EsError, OpenEvent, RealEs, Responder};
use crate::config::AppConfig;
use crate::decision::DecisionEngine;
use crate::stats::Stats;

/// 生产入口：真实后端 + 阻塞运行。
pub fn run(config: &AppConfig) -> Result<(), EsError> {
    let mut backend = RealEs::default();
    let stats = Arc::new(Stats::new());
    setup(config, &mut backend, stats.clone())?;

    info!(mode = %config.mode(), cache = config.cache_allow, "started");
    spawn_stats_reporter(config.stats_interval, stats.clone());
    install_signal_handler(stats);

    loop {
        thread::park();
    }
}

/// ES 初始化序列（ESClient.h 注释约束：invert 前不得有 AUTH 订阅，订阅必须最后）：
/// new_client → unmute_all_target_paths → invert → 自检 → 应用静音规则 → subscribe。
/// inversion 语义下静音规则即"白名单"：只接收命中目录的事件；规则为空 = 全静音。
pub fn setup(
    config: &AppConfig,
    backend: &mut impl EsBackend,
    stats: Arc<Stats>,
) -> Result<(), EsError> {
    let responder = Arc::new(OnceLock::<Responder>::new());
    let verbose = config.verbose;
    let cache_allow = config.cache_allow;

    backend.new_client({
        let responder = responder.clone();
        Box::new(move |event| {
            handle_event(event, &responder, &stats, verbose, cache_allow);
        })
    })?;
    responder
        .set(backend.responder())
        .map_err(|_| EsError::Call {
            op: "responder 初始化",
            rc: -1,
        })?;

    match backend.default_target_mute_count() {
        Ok(n) => info!(count = n, "默认 target mute set 条目数（inversion 前留档）"),
        Err(e) => warn!(error = %e, "默认 mute set 查询失败（不阻断）"),
    }
    backend.unmute_all_target_paths()?;
    backend.invert_target_path_muting()?;
    backend.ensure_target_muting_inverted()?;

    for rule in &config.watch_rules {
        backend.mute_target_prefix(rule)?;
    }
    backend.subscribe_auth_open()?;
    Ok(())
}

fn handle_event(
    event: &OpenEvent,
    responder: &OnceLock<Responder>,
    stats: &Stats,
    verbose: bool,
    cache_allow: bool,
) {
    stats.record_received();

    let decision = DecisionEngine::decide(&event.path, event.st_mode);
    let flags = decision.response_flags(event.fflag);
    let cache = decision.cacheable(cache_allow);

    // responder 在 subscribe 之前已就位，此处必然存在
    let respond = responder.get().expect("responder 先于 subscribe 初始化");
    if let Err(e) = respond(event.msg, flags, cache) {
        error!(error = %e, "应答失败（deadline 风险）");
        stats.record_respond_error();
    }
    stats.record_verdict(decision.is_deny());

    if verbose || decision.is_deny() {
        info!(
            decision = if decision.is_deny() { "DENY" } else { "ALLOW" },
            path = %event.path,
            mime = decision
                .denied_mime
                .as_ref()
                .map(|m| m.essence_str())
                .unwrap_or("-"),
            cache,
            "event"
        );
    }
}

fn spawn_stats_reporter(interval: Duration, stats: Arc<Stats>) {
    thread::spawn(move || {
        loop {
            thread::sleep(interval);
            info!(kind = "interval", stats = %stats.snapshot(), "stats");
        }
    });
}

fn install_signal_handler(stats: Arc<Stats>) {
    let mut signals = Signals::new([SIGINT, SIGTERM]).expect("注册信号监听失败");
    thread::spawn(move || {
        // forever() 只取首个信号即退出进程；写成 if let 而非 loop 以表意（也符合 clippy）
        if let Some(signal) = signals.forever().next() {
            info!(kind = "final", signal, stats = %stats.snapshot(), "stats");
            std::process::exit(0);
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::backend::MockEs;

    const REG: u32 = libc::S_IFREG as u32 | 0o644;

    fn test_config(watch: &[&str], cache: bool) -> AppConfig {
        AppConfig {
            watch_rules: watch.iter().map(|s| format!("{s}/")).collect(),
            cache_allow: cache,
            verbose: true,
            stats_interval: Duration::from_secs(10),
        }
    }

    #[test]
    fn setup_sequence_matches_apple_constraints() {
        let cfg = test_config(&["/watched/a", "/watched/b"], false);
        let mut backend = MockEs::default();
        setup(&cfg, &mut backend, Arc::new(Stats::new())).unwrap();
        assert_eq!(
            backend.calls(),
            [
                "new_client",
                "default_target_mute_count",
                "unmute_all_target_paths",
                "invert_muting",
                "ensure_inverted",
                "mute_target_prefix:/watched/a/",
                "mute_target_prefix:/watched/b/",
                "subscribe_auth_open",
            ]
        );
    }

    #[test]
    fn mute_all_mode_applies_no_rules() {
        let cfg = test_config(&[], false);
        let mut backend = MockEs::default();
        setup(&cfg, &mut backend, Arc::new(Stats::new())).unwrap();
        assert!(
            !backend
                .calls()
                .iter()
                .any(|c| c.starts_with("mute_target_prefix"))
        );
    }

    #[test]
    fn setup_fails_when_inversion_not_accepted() {
        let cfg = test_config(&["/watched"], false);
        let mut backend = MockEs::inversion_rejected();
        let err = setup(&cfg, &mut backend, Arc::new(Stats::new())).unwrap_err();
        assert_eq!(err, EsError::NotInverted);
        // inversion 未生效时必须终止在 subscribe 之前
        assert!(!backend.calls().contains(&"subscribe_auth_open".to_string()));
    }

    #[test]
    fn event_flow_deny_then_allow() {
        let cfg = test_config(&["/watched"], true);
        let mut backend = MockEs::default();
        let stats = Arc::new(Stats::new());
        setup(&cfg, &mut backend, stats.clone()).unwrap();

        backend.fire("/watched/a.png", REG, 0x4);
        backend.fire("/watched/b.txt", REG, 0x4);

        // DENY → flags=0 且不缓存；ALLOW → 透传 fflag 且写入内核缓存
        assert_eq!(backend.responds(), [(0, false), (0x4, true)]);
        let snap = stats.snapshot();
        assert_eq!((snap.received, snap.allowed, snap.denied), (2, 1, 1));
    }

    #[test]
    fn new_client_failure_maps_to_hint() {
        let mut backend = MockEs::failing_new_client(4);
        let cfg = test_config(&[], false);
        let err = setup(&cfg, &mut backend, Arc::new(Stats::new())).unwrap_err();
        assert!(err.to_string().contains("完全磁盘访问"), "{err}");
    }
}
