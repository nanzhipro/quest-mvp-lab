import Foundation

/// 一次统计快照，渲染为 `key=value` 单行。
public struct StatsSnapshot: Equatable, CustomStringConvertible {
    public var received: UInt64 = 0
    public var allowed: UInt64 = 0
    public var denied: UInt64 = 0
    public var respondError: UInt64 = 0

    public var description: String {
        "received=\(received) allowed=\(allowed) denied=\(denied) respond_error=\(respondError)"
    }
}

/// 运行统计。ES handler 线程与报告线程共享，锁粒度小、临界区只有计数器自增。
public final class Stats {
    private var snapshot_ = StatsSnapshot()
    private let lock = NSLock()

    public init() {}

    public func recordReceived() {
        mutate { $0.received += 1 }
    }

    public func recordVerdict(deny: Bool) {
        mutate { deny ? ($0.denied += 1) : ($0.allowed += 1) }
    }

    public func recordRespondError() {
        mutate { $0.respondError += 1 }
    }

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
