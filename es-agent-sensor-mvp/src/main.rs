//! CLI 入口：解析参数、初始化 tracing、组装各模块、优雅停机。
//!
//! 事件 JSON 只写 stdout/文件；诊断日志（tracing，无 ANSI）写 stderr。
//! RealEs 需要 root + entitlement + FDA；单测不经过此路径。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, RwLock};
use std::time::{Duration, SystemTime};

use anyhow::{bail, Context as _};
use clap::Parser;

use es_agent_sensor::backend::{EsBackend, RealEs};
use es_agent_sensor::cli::Cli;
use es_agent_sensor::ffi;
use es_agent_sensor::matcher::AgentSet;
use es_agent_sensor::metrics::Metrics;
use es_agent_sensor::netpoll::{NetPoller, SocketKey};
use es_agent_sensor::pipeline::{self, Assembler, NetEvent};
use es_agent_sensor::proctree::ProcessTree;
use es_agent_sensor::sink::JsonlSink;
use es_agent_sensor::sysproc::{self, RealProcSource};

/// 事件 channel 容量（背压窗口）。
const CHANNEL_CAPACITY: usize = 8192;
/// 延迟回收 grace 窗口（秒）。
const RETIRE_GRACE_SECS: u64 = 5;

fn main() -> anyhow::Result<()> {
    let cli = Cli::parse();

    tracing_subscriber::fmt()
        .with_ansi(false)
        .with_writer(std::io::stderr)
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_new(&cli.log_level)
                .with_context(|| format!("无效 log-level `{}`", cli.log_level))?,
        )
        .init();

    if sysproc::effective_uid() != 0 {
        bail!("需要 root 运行（sudo）；es_new_client 非 root 必失败");
    }

    let extra_mask = cli.extra_mask()?;
    let agents = Arc::new(AgentSet::new(&cli.agents)?);
    for a in agents.agents() {
        tracing::info!(id = %a.id, workdir = %a.workdir, "监控 agent 工作目录");
    }

    // 进程树：annotator 由 AgentSet 提供；seed 回填现存进程。
    let annotator: es_agent_sensor::proctree::Annotator = {
        let agents = agents.clone();
        Arc::new(move |program: &es_agent_sensor::schema::Program| {
            agents.agent_for_program(program)
        })
    };
    let tree = Arc::new(RwLock::new(ProcessTree::with_grace(
        annotator,
        sysproc::ticks_for_seconds(RETIRE_GRACE_SECS),
    )));
    let seeded = tree.write().unwrap().seed(&RealProcSource::new());
    tracing::info!(seeded, "进程树种子回填完成");

    let metrics = Arc::new(Metrics::default());
    let (producer, rx) = pipeline::channel(CHANNEL_CAPACITY);

    // ES client：回调只把扁平事件拷贝进 channel，零阻塞。
    let mut backend = RealEs::default();
    {
        let producer = producer.clone();
        backend.new_client(Box::new(move |msg| producer.try_push_raw(*msg)))?;
    }
    backend.subscribe(ffi::EV_DEFAULT_MASK, extra_mask)?;
    tracing::info!(event_mask = ffi::EV_DEFAULT_MASK, extra_mask, "ES 订阅完成");
    for path in &cli.mute_paths {
        backend.mute_path_prefix(path)?;
        tracing::info!(path, "内核级 target path 静音");
    }

    // 写线程：组装 + 序列化 + JSONL 落盘。
    let mut sink = match &cli.output {
        Some(path) => JsonlSink::file(path)?,
        None => JsonlSink::stdout(),
    };
    let writer = {
        let assembler = Assembler {
            tree: tree.clone(),
            agents: agents.clone(),
            metrics: metrics.clone(),
            sensor_pid: std::process::id(),
        };
        let metrics = metrics.clone();
        std::thread::spawn(move || pipeline::run_writer(rx, assembler, &mut sink, metrics))
    };

    // 停机信号。
    let stop = Arc::new(AtomicBool::new(false));
    signal_hook::flag::register(signal_hook::consts::SIGINT, stop.clone())?;
    signal_hook::flag::register(signal_hook::consts::SIGTERM, stop.clone())?;

    // --duration：有界运行（ES client 进程受 macOS 反篡改保护，外部信号
    // 可能无效，有界场景依赖此定时器）。
    if cli.duration > 0 {
        let stop = stop.clone();
        let duration = cli.duration;
        std::thread::spawn(move || {
            std::thread::sleep(Duration::from_secs(duration));
            stop.store(true, Ordering::Relaxed);
        });
    }

    // net-poller 线程：轮询 agent 成员的 socket 快照。
    let netpoll = if cli.net_poll_interval > 0 {
        let stop = stop.clone();
        let tree = tree.clone();
        let producer = producer.clone();
        let interval = cli.net_poll_interval;
        Some(std::thread::spawn(move || {
            let mut poller = NetPoller::new();
            while !stop.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_secs(interval));
                if stop.load(Ordering::Relaxed) {
                    break;
                }
                let members = tree.read().unwrap().agent_members();
                tracing::debug!(members = members.len(), "net-poller 轮询 agent 成员");
                let mut pids: Vec<i32> = members.iter().map(|(pid, _)| *pid).collect();
                pids.sort_unstable();
                pids.dedup();
                let agent_of: std::collections::HashMap<i32, String> =
                    members.into_iter().collect();
                let events = poller.poll(&pids, |pid| {
                    RealProcSource::list_sockets(pid)
                        .unwrap_or_default()
                        .iter()
                        .filter_map(SocketKey::from_socket)
                        .collect()
                });
                let now = SystemTime::now()
                    .duration_since(SystemTime::UNIX_EPOCH)
                    .unwrap_or_default();
                for ev in events {
                    let Some(agent_id) = agent_of.get(&ev.pid) else {
                        continue;
                    };
                    producer.try_push_net(NetEvent {
                        pid: ev.pid,
                        agent_id: agent_id.clone(),
                        verb: ev.verb,
                        key: ev.key,
                        time_sec: now.as_secs() as i64,
                        time_nsec: now.subsec_nanos() as i64,
                    });
                }
            }
        }))
    } else {
        None
    };

    // 主线程：等待停机信号，周期输出统计。
    let mut last_stats = std::time::Instant::now();
    while !stop.load(Ordering::Relaxed) {
        std::thread::sleep(Duration::from_millis(200));
        if cli.stats_interval > 0 && last_stats.elapsed() >= Duration::from_secs(cli.stats_interval)
        {
            tracing::info!(stats = %metrics.snapshot(producer.dropped()), "周期统计");
            last_stats = std::time::Instant::now();
        }
    }
    tracing::info!("收到停机信号，开始优雅停机");

    // 停机：先断事件源（删 client），再关 channel，写线程排空后退出。
    let dropped = producer.dropped_counter();
    drop(backend);
    drop(producer);
    if let Some(h) = netpoll {
        let _ = h.join();
    }
    let _ = writer.join();
    tracing::info!(stats = %metrics.snapshot(dropped.load(Ordering::Relaxed)), "最终统计");
    Ok(())
}
