import Foundation

/// 一次统计快照，渲染为 `key=value` 单行。
public struct StatsSnapshot: Equatable, CustomStringConvertible {
    /// 发现客户端收到的 AUTH_EXEC 总数（全系统 exec，证明发现客户端在收）。
    public var execReceived: UInt64 = 0
    /// 纳入管控（命中策略并 watch）的进程数。
    public var controlled: UInt64 = 0
    /// 非目标 exec 的快速放行次数（不落日志）。
    public var ignored: UInt64 = 0
    /// 监控客户端收到的 AUTH_OPEN 总数（仅管控进程，反转后应很小）。
    public var openReceived: UInt64 = 0
    /// AUTH_OPEN DENY 次数。
    public var denied: UInt64 = 0
    /// AUTH_OPEN ALLOW 次数（反转下应为 0，防御性兜底）。
    public var allowed: UInt64 = 0
    /// watchProcess（es_mute_process）失败次数。
    public var watchError: UInt64 = 0
    /// 应答失败次数（deadline 风险）。
    public var respondError: UInt64 = 0

    public var description: String {
        "exec_received=\(execReceived) controlled=\(controlled) ignored=\(ignored) open_received=\(openReceived) "
            + "denied=\(denied) allowed=\(allowed) watch_error=\(watchError) respond_error=\(respondError)"
    }
}

/// 运行统计。ES handler 线程与报告线程共享，锁粒度小、临界区只有计数器自增。
public final class Stats {
    private var snapshot_ = StatsSnapshot()
    private let lock = NSLock()

    public init() {}

    public func recordExecReceived() { mutate { $0.execReceived += 1 } }
    public func recordControlled() { mutate { $0.controlled += 1 } }
    public func recordIgnored() { mutate { $0.ignored += 1 } }
    public func recordOpenReceived() { mutate { $0.openReceived += 1 } }
    public func recordVerdict(deny: Bool) { mutate { deny ? ($0.denied += 1) : ($0.allowed += 1) } }
    public func recordWatchError() { mutate { $0.watchError += 1 } }
    public func recordRespondError() { mutate { $0.respondError += 1 } }

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
