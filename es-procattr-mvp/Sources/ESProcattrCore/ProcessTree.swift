import Foundation

/// 进程存活状态。
public enum ProcState: Equatable {
    case running
    case exited(Int32)   // 携带 wait(2) 编码的退出状态
}

/// 进程树节点。
public struct TreeNode: Equatable {
    public var gene: ProcGene
    public var state: ProcState
    public var inScope: Bool

    public init(gene: ProcGene, state: ProcState, inScope: Bool) {
        self.gene = gene
        self.state = state
        self.inScope = inScope
    }
}

/// 进程树：`(pid, pidversion)` → 节点 + 父链 + 关注范围（scope）判定。
///
/// 对齐 Santa `Source/common/processtree/` 的核心语义：
/// - **键是 (pid, pidversion)**（pid 复用 + exec 递增 pidversion，两元组才是「一次进程执行」）。
/// - **只存父链不存子链**（查问题永远「往上查」；渲染子链时瞬时重建）。
/// - fork = 新 pid、同镜像；exec = 同 pid、新 pidversion、换镜像；exit = 立墓碑（MVP 保留不删）。
/// - 内存模型：全系统进程都入库（每个节点是轻量 struct，MVP 运行窗口内可忽略），
///   「是否关注」由 inScope 标记，仅在根命中或父链经过根时为 true。
///
/// 线程安全（NSLock，临界区短）。
public final class ProcessTree {
    /// 「根进程」判定闭包（来自配置 roots 匹配）。
    private let isRoot: (ProcGene) -> Bool

    private var nodes: [Pid: TreeNode] = [:]
    private var parentOf: [Pid: Pid] = [:]
    private var latestByPid: [pid_t: Pid] = [:]
    private let lock = NSLock()

    public init(isRoot: @escaping (ProcGene) -> Bool) {
        self.isRoot = isRoot
    }

    /// 插入/更新一个进程节点，返回其是否处于关注范围（in-scope）。
    /// in-scope = 自身命中根（isRoot）或父节点 in-scope。
    @discardableResult
    public func insert(_ gene: ProcGene, parent: Pid) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return insertLocked(gene, parent: parent)
    }

    private func insertLocked(_ gene: ProcGene, parent: Pid) -> Bool {
        let parentScope = nodes[parent]?.inScope ?? false
        let scope = isRoot(gene) || parentScope
        nodes[gene.id] = TreeNode(gene: gene, state: .running, inScope: scope)
        parentOf[gene.id] = parent
        latestByPid[gene.pid] = gene.id
        return scope
    }

    /// fork 事件：父进程派生同镜像子进程（新 pid，pidversion 0）。
    @discardableResult
    public func handleFork(child: ProcGene, parent: ProcGene) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return insertLocked(child, parent: parent.id)
    }

    /// exec 事件：同一 pid 换上新的 pidversion 与镜像；父链沿用「exec 前节点」的父。
    /// （对齐 Santa HandleExec：`new_proc` 继承 `p.parent_`。）
    @discardableResult
    public func handleExec(actor: ProcGene, target: ProcGene) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        // 父链优先取「exec 前节点」在树中已记录的父；缺失时退回 target 的 parent_audit_token。
        let parent = parentOf[actor.id] ?? target.effectiveParentId
        return insertLocked(target, parent: parent)
    }

    /// exit 事件：标记退出（MVP 保留节点不删，作为「生命周期终点」留痕）。
    @discardableResult
    public func handleExit(_ gene: ProcGene, status: Int32) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        guard var node = nodes[gene.id] else { return false }
        node.state = .exited(status)
        nodes[gene.id] = node
        return node.inScope
    }

    /// 查询某进程是否处于关注范围（不在树内返回 false）。
    public func isInScope(_ gene: ProcGene) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return nodes[gene.id]?.inScope ?? false
    }

    /// 查询节点基因（按完整 Pid；不在树内返回 nil）。
    public func gene(_ id: Pid) -> ProcGene? {
        lock.lock()
        defer { lock.unlock() }
        return nodes[id]?.gene
    }

    /// 按裸 pid 解析进程名（用于把 responsiblePid 还原成可读名；取最新 pidversion 的节点）。
    public func shortName(ofBarePid pid: pid_t) -> String? {
        lock.lock()
        defer { lock.unlock() }
        guard let id = latestByPid[pid], let node = nodes[id] else { return nil }
        return node.gene.shortName
    }

    /// 进程链（lineage）：从「已知最顶层祖先」到目标进程的有序基因序列（祖先在前）。
    /// 用于回答「谁拉起了这个进程，链条如何」（对齐 Santa RootSlice）。
    public func lineage(_ id: Pid) -> [ProcGene] {
        lock.lock()
        defer { lock.unlock() }
        guard nodes[id] != nil else { return [] }
        var chain: [Pid] = []
        var cur: Pid? = id
        while let c = cur {
            chain.append(c)
            cur = parentOf[c]
        }
        return chain.reversed().compactMap { nodes[$0]?.gene }
    }

    /// 关注范围内的存活进程快照（供周期报告）。
    public func scopedSnapshot() -> [TreeNode] {
        lock.lock()
        defer { lock.unlock() }
        return nodes.values.filter { $0.inScope && $0.state == .running }
            .sorted { $0.gene.id < $1.gene.id }
    }

    /// 当前存活（running）节点数。
    public func liveCount() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return nodes.values.filter { $0.state == .running }.count
    }

    /// 全部节点数（含已退出，诊断用）。
    public func totalCount() -> Int {
        lock.lock()
        defer { lock.unlock() }
        return nodes.count
    }

    /// 渲染关注范围内的存活进程树（缩进文本）。
    /// 每个节点一行：`pid 短名 sign=签名 resp=责任进程号`，按父子缩进。
    public func renderScopedTree() -> String {
        lock.lock()
        defer { lock.unlock() }

        let scopedRunning = nodes.filter { $0.value.inScope && $0.value.state == .running }
        var children: [Pid: [Pid]] = [:]
        var roots: [Pid] = []
        for (id, _) in scopedRunning {
            let p = parentOf[id] ?? Pid(pid: 0, pidversion: 0)
            if let pn = nodes[p], pn.inScope, scopedRunning[p] != nil {
                children[p, default: []].append(id)
            } else {
                roots.append(id)
            }
        }

        func render(_ id: Pid, _ depth: Int) -> [String] {
            var out: [String] = []
            if let n = nodes[id] {
                let indent = String(repeating: "  ", count: depth)
                out.append("\(indent)\(id.pid) \(n.gene.shortName) sign=\(n.gene.signingId) resp=\(n.gene.responsiblePid)")
            }
            for c in (children[id] ?? []).sorted() {
                out += render(c, depth + 1)
            }
            return out
        }
        return roots.sorted().flatMap { render($0, 0) }.joined(separator: "\n")
    }
}
