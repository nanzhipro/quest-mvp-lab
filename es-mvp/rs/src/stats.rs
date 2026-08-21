//! 运行统计：无锁计数 + 快照渲染。handler 线程与报告线程共享。

use std::fmt;
use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub struct Stats {
    received: AtomicU64,
    allowed: AtomicU64,
    denied: AtomicU64,
    respond_error: AtomicU64,
}

impl Stats {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn record_received(&self) {
        self.received.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_verdict(&self, deny: bool) {
        let counter = if deny { &self.denied } else { &self.allowed };
        counter.fetch_add(1, Ordering::Relaxed);
    }

    pub fn record_respond_error(&self) {
        self.respond_error.fetch_add(1, Ordering::Relaxed);
    }

    pub fn snapshot(&self) -> StatsSnapshot {
        StatsSnapshot {
            received: self.received.load(Ordering::Relaxed),
            allowed: self.allowed.load(Ordering::Relaxed),
            denied: self.denied.load(Ordering::Relaxed),
            respond_error: self.respond_error.load(Ordering::Relaxed),
        }
    }
}

#[derive(Debug, PartialEq, Eq)]
pub struct StatsSnapshot {
    pub received: u64,
    pub allowed: u64,
    pub denied: u64,
    pub respond_error: u64,
}

impl fmt::Display for StatsSnapshot {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "received={} allowed={} denied={} respond_error={}",
            self.received, self.allowed, self.denied, self.respond_error
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counts_and_snapshots() {
        let stats = Stats::default();
        stats.record_received();
        stats.record_received();
        stats.record_verdict(false);
        stats.record_verdict(true);
        stats.record_respond_error();
        assert_eq!(
            stats.snapshot(),
            StatsSnapshot {
                received: 2,
                allowed: 1,
                denied: 1,
                respond_error: 1
            }
        );
    }

    #[test]
    fn snapshot_renders_key_value_line() {
        let stats = Stats::default();
        assert_eq!(
            stats.snapshot().to_string(),
            "received=0 allowed=0 denied=0 respond_error=0"
        );
    }
}
