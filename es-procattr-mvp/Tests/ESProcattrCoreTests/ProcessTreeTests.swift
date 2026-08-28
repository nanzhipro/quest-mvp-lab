import XCTest
@testable import ESProcattrCore

/// ProcessTree 单元测试：对齐 Santa 语义（(pid,pidversion) 键、fork/exec/exit、scope 传播、lineage）。
final class ProcessTreeTests: XCTestCase {
    // 根判定：路径含 "/agent/" 视为关注根。
    private func makeTree() -> ProcessTree {
        ProcessTree(isRoot: { $0.path.contains("/agent/") })
    }

    private func gene(_ pid: pid_t, _ ver: UInt64 = 0, path: String, ppid: pid_t = 0) -> ProcGene {
        var g = ProcGene()
        g.pid = pid; g.pidversion = ver; g.path = path; g.ppid = ppid
        return g
    }

    func testInsertRootIsInScope() {
        let tree = makeTree()
        let root = gene(100, 0, path: "/agent/hermes")
        XCTAssertTrue(tree.insert(root, parent: Pid(pid: 1, pidversion: 0)))
        XCTAssertTrue(tree.isInScope(root))
    }

    func testInsertNonRootIsOutOfScope() {
        let tree = makeTree()
        let other = gene(200, 0, path: "/bin/ls")
        XCTAssertFalse(tree.insert(other, parent: Pid(pid: 1, pidversion: 0)))
        XCTAssertFalse(tree.isInScope(other))
    }

    func testChildInheritsScope() {
        let tree = makeTree()
        let root = gene(100, 0, path: "/agent/hermes")
        tree.insert(root, parent: Pid(pid: 1, pidversion: 0))
        let child = gene(101, 0, path: "/bin/bash", ppid: 100)
        XCTAssertTrue(tree.insert(child, parent: root.id))
        XCTAssertTrue(tree.isInScope(child))
    }

    func testForkInheritsParentScopeAndProgram() {
        let tree = makeTree()
        let root = gene(100, 0, path: "/agent/hermes")
        tree.insert(root, parent: Pid(pid: 1, pidversion: 0))
        // fork 子进程同镜像（同 path），新 pid
        let child = gene(101, 0, path: "/agent/hermes")
        XCTAssertTrue(tree.handleFork(child: child, parent: root))
        XCTAssertTrue(tree.isInScope(child))
    }

    func testExecSamePidNewPidversionKeepsParentChain() {
        let tree = makeTree()
        let root = gene(100, 0, path: "/agent/hermes")
        tree.insert(root, parent: Pid(pid: 1, pidversion: 0))
        // fork 出 101，然后 101 exec 成 /bin/bash（pidversion 0→1）
        let child = gene(101, 0, path: "/agent/hermes")
        tree.handleFork(child: child, parent: root)
        let execActor = gene(101, 0, path: "/agent/hermes")      // exec 前（旧镜像）
        let execTarget = gene(101, 1, path: "/bin/bash", ppid: 100) // exec 后（新镜像、新 pidversion）
        XCTAssertTrue(tree.handleExec(actor: execActor, target: execTarget))
        XCTAssertTrue(tree.isInScope(execTarget))

        // lineage 应从 launchd 一路到 101（exec 后节点）
        let chain = tree.lineage(execTarget.id)
        XCTAssertEqual(chain.last?.pid, 101)
        XCTAssertEqual(chain.last?.pidversion, 1)
        XCTAssertEqual(chain.last?.path, "/bin/bash")
        // 父链应含 root 100
        XCTAssertTrue(chain.contains { $0.pid == 100 })
    }

    func testPidReuseIsDistinctNodes() {
        let tree = makeTree()
        let a = gene(50, 0, path: "/bin/a")
        tree.insert(a, parent: Pid(pid: 1, pidversion: 0))
        // pid 复用：同 pid 新 pidversion 是不同节点
        let b = gene(50, 1, path: "/agent/hermes")
        XCTAssertTrue(tree.insert(b, parent: Pid(pid: 1, pidversion: 0))) // 命中根 → in-scope
        XCTAssertTrue(tree.isInScope(b))
        // 旧节点（pid 50 pidversion 0）仍是 out-of-scope 的 /bin/a
        XCTAssertFalse(tree.isInScope(a))
    }

    func testExitMarksState() {
        let tree = makeTree()
        let root = gene(100, 0, path: "/agent/hermes")
        tree.insert(root, parent: Pid(pid: 1, pidversion: 0))
        XCTAssertTrue(tree.handleExit(root, status: 0))
        XCTAssertEqual(tree.liveCount(), 0)
        XCTAssertEqual(tree.totalCount(), 1) // 节点保留（墓碑）
    }

    func testLineageWalksToTop() {
        let tree = makeTree()
        let launchd = gene(1, 0, path: "/sbin/launchd")
        tree.insert(launchd, parent: Pid(pid: 0, pidversion: 0))
        let shell = gene(10, 0, path: "/bin/zsh", ppid: 1)
        tree.insert(shell, parent: launchd.id)
        let agent = gene(100, 0, path: "/agent/hermes", ppid: 10)
        tree.insert(agent, parent: shell.id)
        let chain = tree.lineage(agent.id)
        XCTAssertEqual(chain.map { $0.pid }, [1, 10, 100])
    }

    func testRenderScopedTreeIndentsChildren() {
        let tree = makeTree()
        let launchd = gene(1, 0, path: "/sbin/launchd")
        tree.insert(launchd, parent: Pid(pid: 0, pidversion: 0))
        let agent = gene(100, 0, path: "/agent/hermes", ppid: 1)
        tree.insert(agent, parent: launchd.id)
        let child = gene(101, 0, path: "/bin/bash", ppid: 100)
        tree.insert(child, parent: agent.id)
        let rendered = tree.renderScopedTree()
        XCTAssertTrue(rendered.contains("100 hermes"))
        XCTAssertTrue(rendered.contains("101 bash"))
        // 101 应缩进在 100 之下
        XCTAssertTrue(rendered.contains("  101 bash"))
        // launchd 是 out-of-scope，不应出现在 scoped 树里
        XCTAssertFalse(rendered.contains("1 launchd"))
    }
}
