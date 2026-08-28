import Foundation

/// 应用编排：种子（Backfill）→ ES 订阅 → 事件归因 → 周期快照 → 信号收尾。
public enum App {
    /// 生产入口：真实后端 + 阻塞运行（dispatchMain 不返回）。
    public static func run(config: AppConfig) -> Result<Void, EsError> {
        let stats = Stats()
        let tree = ProcessTree(isRoot: config.matcher.matches)
        let sink = JsonlSink(path: config.outputJsonl)

        // 1. 种子：枚举已运行进程，找根 + 祖先链 + 后代（订阅前完成，见 SPEC）
        let roots = seed(tree: tree, matcher: config.matcher, stats: stats)
        Log.info("seeded", [
            "roots": "\(roots.count)",
            "scoped": "\(stats.snapshot().seedScoped)",
            "nodes": "\(tree.totalCount())",
        ])
        for root in roots {
            let chain = tree.lineage(root.id).map { "\($0.pid):\($0.shortName)" }.joined(separator: "→")
            Log.info("root", ["pid": "\(root.pid)", "path": root.path, "chain": chain])
        }
        if roots.isEmpty {
            Log.warn("未发现任何命中 roots 的进程；请检查 config 的 pathContains/signingId")
        }
        printTreeSnapshot(tree: tree, stats: stats)

        // 2. ES client + 订阅（订阅必须最后）
        let backend = RealEs()
        switch backend.newClient({ event in
            handleEvent(event, tree: tree, stats: stats, config: config, sink: sink)
        }) {
        case .failure(let error): return .failure(error)
        case .success: break
        }
        if case .failure(let error) = backend.subscribe() { return .failure(error) }

        Log.info("started", ["roots": config.matcher.description])
        startSnapshotReporter(tree: tree, stats: stats, interval: config.report.snapshotSeconds)
        installSignalHandler(tree: tree, stats: stats)
        dispatchMain()
    }

    // MARK: - 种子（Backfill，对齐 Santa ProcessTree::Backfill）

    /// 枚举已运行进程 → 找到 roots 命中进程 → 自顶向下插祖先链 + BFS 插后代。
    /// 返回根进程基因列表（用于打印初始进程链）。
    static func seed(tree: ProcessTree, matcher: RootMatcher, stats: Stats) -> [ProcGene] {
        let pids = ProcEnumerator.allPids()
        // pid -> (path, ppid, pidversion, argv)。pidversion 经 task_info(TASK_AUDIT_TOKEN) 取权威值，
        // argv 经 KERN_PROCARGS2（解释型进程的「脚本身份」）。
        struct Raw { var path: String; var ppid: pid_t; var pidversion: UInt64; var args: [String] }
        var info: [pid_t: Raw] = [:]
        for pid in pids {
            guard let path = ProcEnumerator.path(of: pid) else { continue }
            let ver = ProcEnumerator.auditToken(of: pid).map { Token($0).pidversion } ?? 0
            let ppid = ProcEnumerator.ppid(of: pid) ?? 0
            let args = ProcEnumerator.arguments(of: pid)
            info[pid] = Raw(path: path, ppid: ppid, pidversion: ver, args: args)
        }

        var roots: [ProcGene] = []
        var rootPids: [pid_t] = []
        for (pid, d) in info {
            var g = ProcGene()
            g.pid = pid; g.pidversion = d.pidversion; g.path = d.path; g.ppid = d.ppid; g.arguments = d.args
            if matcher.matches(g) { roots.append(g); rootPids.append(pid) }
        }
        stats.recordSeedRoots(UInt64(roots.count))

        // 祖先链：每个根向上走到顶层（ppid=0 或查不到），再自顶向下插入（先有爸后有儿）。
        for rootPid in rootPids {
            var chain: [pid_t] = []
            var cur: pid_t? = rootPid
            while let c = cur, c != 0 {
                chain.append(c)
                cur = info[c]?.ppid
            }
            var prev: Pid = Pid(pid: 0, pidversion: 0)
            for pid in chain.reversed() {
                guard let d = info[pid] else { continue }
                var g = ProcGene()
                g.pid = pid; g.pidversion = d.pidversion; g.path = d.path; g.ppid = d.ppid; g.arguments = d.args
                tree.insert(g, parent: prev)
                prev = g.id
            }
        }

        // 后代：BFS，父在子前插入，使 in-scope 沿父子链传播。
        var scoped = Set(rootPids)
        var queue = rootPids
        while !queue.isEmpty {
            let p = queue.removeFirst()
            for child in ProcEnumerator.childPids(of: p) where !scoped.contains(child) {
                scoped.insert(child)
                guard let d = info[child] else { continue }
                var g = ProcGene()
                g.pid = child; g.pidversion = d.pidversion; g.path = d.path; g.ppid = d.ppid; g.arguments = d.args
                tree.insert(g, parent: Pid(pid: p, pidversion: info[p]?.pidversion ?? 0))
                queue.append(child)
            }
        }
        stats.recordSeedScoped(UInt64(scoped.count))
        return roots
    }

    // MARK: - 事件处理

    static func handleEvent(_ event: EsEvent, tree: ProcessTree, stats: Stats, config: AppConfig, sink: JsonlSink) {
        switch event.kind {
        case .exec:
            stats.recordExec()
            guard let target = event.target else { return }
            // ES 事件不含 argv；解释型进程的「脚本身份」需经 KERN_PROCARGS2 补查（对齐 Santa）。
            var enriched = target
            if enriched.arguments.isEmpty {
                enriched.arguments = ProcEnumerator.arguments(of: target.pid)
            }
            guard tree.handleExec(actor: event.actor, target: enriched) else { return }
            stats.recordScoped()
            let chain = tree.lineage(enriched.id).map { "\($0.pid):\($0.shortName)" }.joined(separator: "→")
            let resp = tree.shortName(ofBarePid: enriched.responsiblePid) ?? "\(enriched.responsiblePid)"
            Log.info("exec", [
                "pid": "\(enriched.pid)", "pidver": "\(enriched.pidversion)",
                "path": enriched.path, "sign": enriched.signingId, "team": enriched.teamId,
                "ppid": "\(enriched.ppid)", "parent": "\(enriched.parentPid)", "resp": resp,
                "argv": enriched.arguments.joined(separator: " "),
                "chain": chain,
            ])
            sink.emit(.exec, gene: enriched, chain: chain)

        case .fork:
            stats.recordFork()
            guard let child = event.target else { return }
            guard tree.handleFork(child: child, parent: event.actor) else { return }
            stats.recordScoped()
            Log.info("fork", ["child": "\(child.pid)", "parent": "\(event.actor.pid)", "path": child.path])
            sink.emit(.fork, gene: child)

        case .exit:
            stats.recordExit()
            guard tree.handleExit(event.actor, status: event.exitStatus) else { return }
            stats.recordScoped()
            Log.info("exit", ["pid": "\(event.actor.pid)", "status": "\(event.exitStatus)"])
            sink.emit(.exit, gene: event.actor)

        case .open, .close, .create, .rename, .unlink, .write:
            switch event.kind {
            case .open: stats.recordOpen()
            case .close: stats.recordClose()
            case .create: stats.recordCreate()
            case .rename: stats.recordRename()
            case .unlink: stats.recordUnlink()
            default: stats.recordWrite()
            }
            handleFileEvent(event, tree: tree, stats: stats, config: config, sink: sink)
        }
    }

    /// 文件行为事件：仅归因 in-scope 进程；open/close 受配置与库路径过滤控制。
    static func handleFileEvent(_ event: EsEvent, tree: ProcessTree, stats: Stats, config: AppConfig, sink: JsonlSink) {
        guard tree.isInScope(event.actor) else { return }
        switch event.kind {
        case .open:
            guard config.report.logOpen else { return }
            if !config.report.logLibraryOpen && isLibraryPath(event.path) { return }
        case .close:
            guard event.closeModified else { return } // 只关心「写后关闭」
        default:
            break
        }
        stats.recordScoped()
        var fields = ["pid": "\(event.actor.pid)", "path": event.path]
        if event.kind == .rename { fields["dest"] = event.destPath }
        if event.kind == .close { fields["modified"] = "true" }
        Log.info(event.kind.rawValue, fields)
        sink.emit(event.kind, gene: event.actor, file: event.path, dest: event.destPath)
    }

    /// 库/系统路径粗判（open 减噪）：.dylib/.framework/.so/.bundle 或系统只读目录。
    static func isLibraryPath(_ path: String) -> Bool {
        let systemPrefixes = ["/System/", "/usr/lib/", "/usr/share/", "/usr/bin/", "/bin/", "/sbin/",
                              "/Library/Apple/", "/Library/Frameworks/", "/usr/local/lib/"]
        if systemPrefixes.contains(where: { path.hasPrefix($0) }) { return true }
        let ext = (path as NSString).pathExtension.lowercased()
        return ["dylib", "so", "framework", "bundle", "tbd", "car"].contains(ext)
    }

    // MARK: - 周期快照 / 信号

    private static func printTreeSnapshot(tree: ProcessTree, stats: Stats) {
        stats.setLiveNodes(UInt64(tree.liveCount()))
        let treeText = tree.renderScopedTree()
        Log.info("tree", ["kind": "snapshot", "nodes": treeText.replacingOccurrences(of: "\n", with: " | ")])
        Log.info("stats", ["kind": "interval", "stats": "\(stats.snapshot())"])
    }

    private static func startSnapshotReporter(tree: ProcessTree, stats: Stats, interval: TimeInterval) {
        let timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now() + interval, repeating: interval)
        timer.setEventHandler {
            printTreeSnapshot(tree: tree, stats: stats)
        }
        timer.resume()
        retained.append(timer) // dispatch source 出作用域即被取消，必须持有
    }

    private static func installSignalHandler(tree: ProcessTree, stats: Stats) {
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        for sig in [SIGINT, SIGTERM] {
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler {
                Log.info("tree", ["kind": "final"])
                printTreeSnapshot(tree: tree, stats: stats)
                Log.info("stats", ["kind": "final", "stats": "\(stats.snapshot())"])
                exit(0)
            }
            source.resume()
            retained.append(source)
        }
    }

    /// 常驻 dispatch source 的持有容器（timer / signal source）。
    private static var retained: [any DispatchSourceProtocol] = []
}

/// JSONL 事件落盘（可选 --output）。写操作即落盘，无缓冲，崩溃不丢已写行。
public final class JsonlSink {
    private let handle: FileHandle?

    public init(path: String?) {
        if let p = path {
            FileManager.default.createFile(atPath: p, contents: nil, attributes: nil)
            handle = FileHandle(forWritingAtPath: p)
        } else {
            handle = nil
        }
    }

    public func emit(_ kind: EsEventKind, gene: ProcGene, chain: String = "", file: String = "", dest: String = "") {
        guard let handle else { return }
        var obj: [String: Any] = [
            "t": kind.rawValue,
            "ts": Int(Date().timeIntervalSince1970 * 1000),
            "pid": Int(gene.pid),
            "pidversion": gene.pidversion,
            "proc": gene.path,
            "argv": gene.arguments,   // 解释型进程的「脚本身份」在此
            "sign": gene.signingId,
            "team": gene.teamId,
            "ppid": Int(gene.ppid),
            "parent": Int(gene.parentPid),
            "resp": Int(gene.responsiblePid),
        ]
        if !chain.isEmpty { obj["chain"] = chain }
        if !file.isEmpty { obj["file"] = file }
        if !dest.isEmpty { obj["dest"] = dest }
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []),
              let line = String(data: data, encoding: .utf8) else { return }
        handle.write((line + "\n").data(using: .utf8)!)
    }
}
