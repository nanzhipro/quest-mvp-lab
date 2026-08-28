import XCTest
@testable import ESProcattrCore

/// handleEvent 归因逻辑测试（无 root、无 ES）：用真实 ProcessTree + Stats + 无文件 Sink。
final class AppHandleEventTests: XCTestCase {
    private var config: AppConfig {
        AppConfig(configPath: "test", matcher: RootMatcher(rules: [RootRule(pathContains: "/agent/")]),
                  report: ReportConfig(), outputJsonl: nil)
    }
    private var tree: ProcessTree {
        ProcessTree(isRoot: { $0.path.contains("/agent/") })
    }
    private func gene(_ pid: pid_t, _ ver: UInt64 = 0, path: String, ppid: pid_t = 0) -> ProcGene {
        var g = ProcGene()
        g.pid = pid; g.pidversion = ver; g.path = path; g.ppid = ppid
        return g
    }

    func testExecInScopeRecordsAndAttributes() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        // 种子根
        tree.insert(gene(100, 0, path: "/agent/hermes"), parent: Pid(pid: 1, pidversion: 0))

        // fork：100 → 101
        App.handleEvent(
            EsEvent(kind: .fork, actor: gene(100, 0, path: "/agent/hermes"),
                    target: gene(101, 0, path: "/agent/hermes")),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertTrue(tree.isInScope(gene(101, 0, path: "/agent/hermes")))

        // exec：101 变 /bin/bash（pidversion 0→1）
        App.handleEvent(
            EsEvent(kind: .exec, actor: gene(101, 0, path: "/agent/hermes"),
                    target: gene(101, 1, path: "/bin/bash", ppid: 100)),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertTrue(tree.isInScope(gene(101, 1, path: "/bin/bash")))

        let snap = stats.snapshot()
        XCTAssertEqual(snap.fork, 1)
        XCTAssertEqual(snap.exec, 1)
        XCTAssertEqual(snap.scoped, 2)
    }

    func testExecOutOfScopeNotRecorded() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        tree.insert(gene(1, 0, path: "/sbin/launchd"), parent: Pid(pid: 0, pidversion: 0))

        App.handleEvent(
            EsEvent(kind: .exec, actor: gene(1, 0, path: "/sbin/launchd"),
                    target: gene(200, 0, path: "/bin/ls", ppid: 1)),
            tree: tree, stats: stats, config: config, sink: sink)

        let snap = stats.snapshot()
        XCTAssertEqual(snap.exec, 1)
        XCTAssertEqual(snap.scoped, 0) // 非关注子树，不记 scoped
    }

    func testWriteFileInScopeAttributed() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        tree.insert(gene(100, 0, path: "/agent/hermes"), parent: Pid(pid: 1, pidversion: 0))

        App.handleEvent(
            EsEvent(kind: .write, actor: gene(100, 0, path: "/agent/hermes"), path: "/tmp/out.txt"),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertEqual(stats.snapshot().write, 1)
        XCTAssertEqual(stats.snapshot().scoped, 1)
    }

    func testWriteFileOutOfScopeIgnored() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        // 非关注进程
        tree.insert(gene(500, 0, path: "/bin/cat"), parent: Pid(pid: 1, pidversion: 0))

        App.handleEvent(
            EsEvent(kind: .write, actor: gene(500, 0, path: "/bin/cat"), path: "/tmp/x.txt"),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertEqual(stats.snapshot().write, 1) // 计数仍累加（全系统）
        XCTAssertEqual(stats.snapshot().scoped, 0) // 但不归因
    }

    func testOpenRespectsConfig() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        tree.insert(gene(100, 0, path: "/agent/hermes"), parent: Pid(pid: 1, pidversion: 0))

        // logOpen=false（默认）：open 不归因
        App.handleEvent(
            EsEvent(kind: .open, actor: gene(100, 0, path: "/agent/hermes"), path: "/etc/hosts"),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertEqual(stats.snapshot().scoped, 0)
    }

    func testCloseNonModifiedIgnored() {
        let tree = tree
        let stats = Stats()
        let sink = JsonlSink(path: nil)
        tree.insert(gene(100, 0, path: "/agent/hermes"), parent: Pid(pid: 1, pidversion: 0))

        App.handleEvent(
            EsEvent(kind: .close, actor: gene(100, 0, path: "/agent/hermes"), path: "/tmp/x", closeModified: false),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertEqual(stats.snapshot().scoped, 0)

        App.handleEvent(
            EsEvent(kind: .close, actor: gene(100, 0, path: "/agent/hermes"), path: "/tmp/x", closeModified: true),
            tree: tree, stats: stats, config: config, sink: sink)
        XCTAssertEqual(stats.snapshot().scoped, 1)
    }
}
