import Foundation

/// 一次统计快照，渲染为 `key=value` 单行。
public struct StatsSnapshot: Equatable, CustomStringConvertible {
    /// 收到的 ES 事件总数（全系统，按类型分桶见下）。
    public var exec: UInt64 = 0
    public var fork: UInt64 = 0
    public var exit: UInt64 = 0
    public var open: UInt64 = 0
    public var close: UInt64 = 0
    public var create: UInt64 = 0
    public var rename: UInt64 = 0
    public var unlink: UInt64 = 0
    public var write: UInt64 = 0
    /// 归因到关注子树（in-scope）的事件数。
    public var scoped: UInt64 = 0
    /// 种子阶段发现的根进程数（匹配 roots 配置）。
    public var seedRoots: UInt64 = 0
    /// 种子阶段纳入监控的进程数（根 + 后代）。
    public var seedScoped: UInt64 = 0
    /// 进程树当前存活节点数。
    public var liveNodes: UInt64 = 0

    public var description: String {
        "exec=\(exec) fork=\(fork) exit=\(exit) open=\(open) close=\(close) "
            + "create=\(create) rename=\(rename) unlink=\(unlink) write=\(write) "
            + "scoped=\(scoped) seed_roots=\(seedRoots) seed_scoped=\(seedScoped) live_nodes=\(liveNodes)"
    }
}

/// 运行统计。ES handler 线程与报告线程共享，锁粒度小、临界区只有计数器自增。
public final class Stats {
    private var snapshot_ = StatsSnapshot()
    private let lock = NSLock()

    public init() {}

    public func recordExec() { mutate { $0.exec += 1 } }
    public func recordFork() { mutate { $0.fork += 1 } }
    public func recordExit() { mutate { $0.exit += 1 } }
    public func recordOpen() { mutate { $0.open += 1 } }
    public func recordClose() { mutate { $0.close += 1 } }
    public func recordCreate() { mutate { $0.create += 1 } }
    public func recordRename() { mutate { $0.rename += 1 } }
    public func recordUnlink() { mutate { $0.unlink += 1 } }
    public func recordWrite() { mutate { $0.write += 1 } }
    public func recordScoped() { mutate { $0.scoped += 1 } }
    public func recordSeedRoots(_ n: UInt64) { mutate { $0.seedRoots += n } }
    public func recordSeedScoped(_ n: UInt64) { mutate { $0.seedScoped += n } }
    public func setLiveNodes(_ n: UInt64) { mutate { $0.liveNodes = n } }

    public func snapshot() -> StatsSnapshot {
        lock.lock()
        defer { lock.unlock() }
        return snapshot_
    }

    private func mutate(_ body: (inout StatsSnapshot) -> Void) {
        lock.lock()
        body(&snapshot_)
        lock.unlock()
    }
}
