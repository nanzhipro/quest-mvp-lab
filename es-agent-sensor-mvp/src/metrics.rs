//! 吞吐/丢弃统计。全部原子计数，producer/写线程/net-poller 跨线程共享。

use std::sync::atomic::{AtomicU64, Ordering};

#[derive(Default)]
pub struct Counter(AtomicU64);

impl Counter {
    pub fn inc(&self) {
        self.0.fetch_add(1, Ordering::Relaxed);
    }

    pub fn get(&self) -> u64 {
        self.0.load(Ordering::Relaxed)
    }
}

impl std::fmt::Debug for Counter {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}", self.get())
    }
}

/// 全局指标。`dropped` 由 pipeline::Producer 自维护（背压丢弃），
/// 此处汇总其余计数。
#[derive(Debug, Default)]
pub struct Metrics {
    /// ES 回调收到的事件数。
    pub received: Counter,
    /// 命中过滤（进入组装）的事件数。
    pub matched: Counter,
    /// 过滤丢弃（不命中 agent 双向匹配 + CLOSE 未修改等）。
    pub filtered: Counter,
    /// 成功写入 JSONL 的事件数。
    pub written: Counter,
    /// 序列化/写盘错误数。
    pub errors: Counter,
}

impl Metrics {
    pub fn snapshot(&self, dropped: u64) -> String {
        format!(
            "received={} matched={} filtered={} written={} dropped={} errors={}",
            self.received.get(),
            self.matched.get(),
            self.filtered.get(),
            self.written.get(),
            dropped,
            self.errors.get(),
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn counters_and_snapshot() {
        let m = Metrics::default();
        m.received.inc();
        m.received.inc();
        m.written.inc();
        assert_eq!(m.received.get(), 2);
        assert_eq!(
            m.snapshot(7),
            "received=2 matched=0 filtered=0 written=1 dropped=7 errors=0"
        );
    }

    #[test]
    fn counter_debug_impl() {
        let c = Counter::default();
        c.inc();
        assert_eq!(format!("{c:?}"), "1");
        let m = Metrics::default();
        let dbg = format!("{m:?}");
        assert!(dbg.contains("received: 0"));
    }
}
