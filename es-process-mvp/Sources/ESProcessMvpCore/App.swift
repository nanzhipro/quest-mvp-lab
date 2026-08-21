import Foundation

/// 应用编排：双客户端初始化序列（顺序有官方约束）+ 事件处理 + 周期统计 + 信号收尾。
public enum App {
    /// 生产入口：真实后端 + 阻塞运行（dispatchMain 不返回）。
    public static func run(config: AppConfig) -> Result<Void, EsError> {
        let stats = Stats()
        switch setup(config: config, backend: RealEs(), stats: stats) {
        case .failure(let error): return .failure(error)
        case .success: break
        }

        Log.info("started", [
            "config": config.configPath,
            "controlled_bundle_ids": config.policy.controlledBundleIds.sorted().joined(separator: ","),
        ])
        if config.policy.controlledBundleIds.isEmpty {
            Log.warn("bundleIds 为空：不管控任何进程，全部默认 ALLOW")
        }
        Log.info("纳入管控的进程须在本程序启动后再 launch 才能被 watch（进程反转无法发现已在运行的进程，见 SPEC.md）")
        startStatsReporter(interval: config.statsInterval, stats: stats)
        installSignalHandler(stats: stats)
        dispatchMain()
    }

    /// ES 初始化序列（顺序有官方约束）：
    /// 1. 先建监控客户端并反转进程静音（反转后 es_mute_process=watch；反转前不得订阅）。
    /// 2. 再建发现客户端（其 handler 会把命中的 token mute 到监控客户端，故监控客户端必须先就位）。
    /// 3. 订阅发现客户端 AUTH_EXEC → 订阅监控客户端 AUTH_OPEN（订阅必须最后）。
    public static func setup(config: AppConfig, backend: any EsBackend, stats: Stats) -> Result<Void, EsError> {
        let openBox = ResponderBox<FlagsResponder>()
        let execBox = ResponderBox<AuthResponder>()

        // 1. 监控客户端（进程反转）
        switch backend.newMonitorClient({ event in
            handleOpen(event, responderBox: openBox, stats: stats, policy: config.policy)
        }) {
        case .failure(let error): return .failure(error)
        case .success: openBox.responder = backend.openResponder()
        }
        let steps: [() -> Result<Void, EsError>] = [
            backend.invertProcessMuting,
            backend.ensureProcessMutingInverted,
        ]
        for step in steps {
            if case .failure(let error) = step() { return .failure(error) }
        }

        // 2. 发现客户端（非反转）
        switch backend.newDiscoveryClient({ event in
            handleExec(event, backend: backend, responderBox: execBox, stats: stats, policy: config.policy)
        }) {
        case .failure(let error): return .failure(error)
        case .success: execBox.responder = backend.execResponder()
        }

        // 3. 订阅（最后）
        if case .failure(let error) = backend.subscribeExec() { return .failure(error) }
        return backend.subscribeOpen()
    }

    /// AUTH_EXEC 处理（发现客户端，非反转，全系统 exec 都到）。
    /// 命中策略（bundleId 在 YAML）→ 先 mute（watch）后 ALLOW exec（exec 不拦截，仅发现）；
    /// 未命中 → 快速 ALLOW + 缓存（减载，不落日志）。
    private static func handleExec(
        _ event: ExecEvent,
        backend: any EsBackend,
        responderBox: ResponderBox<AuthResponder>,
        stats: Stats,
        policy: Policy
    ) {
        stats.recordExecReceived()

        guard let responder = responderBox.responder else {
            Log.error("exec responder 未初始化（不应发生）", ["target": event.targetPath])
            stats.recordRespondError()
            return
        }

        guard policy.isControlled(event.bundleId) else {
            // 未纳入管控：立即放行并写内核缓存，减小全系统 exec 对用户态的冲击；不落日志。
            if case .failure(let error) = responder(event.msg, false, true) {
                Log.error("非管控 exec 应答失败", ["error": "\(error)"])
                stats.recordRespondError()
            }
            stats.recordIgnored()
            return
        }

        // 纳入管控：先 watch（mute）再 ALLOW exec，确保其后续 AUTH_OPEN 能投递到监控客户端。
        if case .failure(let error) = backend.watchProcess(token: event.token) {
            Log.warn("watchProcess 失败", ["bundle": event.bundleId, "target": event.targetPath, "error": "\(error)"])
            stats.recordWatchError()
        } else {
            stats.recordControlled()
        }

        // exec 永远 ALLOW（策略只管控 AUTH_OPEN）；cache=false：每次 launch 都要重新 mute 新 pid。
        if case .failure(let error) = responder(event.msg, false, false) {
            Log.error("exec 应答失败（deadline 风险）", ["target": event.targetPath, "error": "\(error)"])
            stats.recordRespondError()
        }

        Log.info("exec", [
            "bundle": event.bundleId,
            "decision": "ALLOW",
            "watched": "true",
            "target": event.targetPath,
        ])
    }

    /// AUTH_OPEN 处理（监控客户端，进程反转，只有被 watch 的管控进程事件会到）。
    private static func handleOpen(
        _ event: OpenEvent,
        responderBox: ResponderBox<FlagsResponder>,
        stats: Stats,
        policy: Policy
    ) {
        stats.recordOpenReceived()

        // 裁决：管控进程 + PDF → DENY；其余（非 PDF，或非管控进程）→ ALLOW。
        let deny = policy.denyOpen(bundleId: event.bundleId, path: event.path)
        // AUTH_OPEN 是 flags 类：DENY = flags 0；ALLOW = UINT32_MAX（授权全部 flags）。
        let flags: UInt32 = deny ? 0 : .max
        // 不缓存任何 AUTH_OPEN 响应：flags 缓存是共享缓存且语义微妙（同名文件不同 flags 会被缓存
        // 误判为 DENY，见 ESClient.h @note；真机实测缓存曾让 WeChat 启动即崩）。全量投递换正确与简单，
        // handler 是 O(路径长) 字符串判断 + 一次内核应答，成本可接受。
        let cache = false

        guard let responder = responderBox.responder else {
            Log.error("open responder 未初始化（不应发生）", ["path": event.path])
            stats.recordRespondError()
            return
        }
        if case .failure(let error) = responder(event.msg, flags, cache) {
            Log.error("open 应答失败（deadline 风险）", ["path": event.path, "error": "\(error)"])
            stats.recordRespondError()
        }
        stats.recordVerdict(deny: deny)

        if deny {
            Log.info("open", [
                "decision": "DENY",
                "bundle": event.bundleId,
                "path": event.path,
            ])
        }
        // ALLOW 分支不落日志：管控进程的常规 open 高频，保持安静；只有 PDF 拦截才值得记录。
    }

    private static func startStatsReporter(interval: TimeInterval, stats: Stats) {
        let timer = DispatchSource.makeTimerSource(queue: .global())
        timer.schedule(deadline: .now() + interval, repeating: interval)
        timer.setEventHandler {
            Log.info("stats", ["kind": "interval", "stats": "\(stats.snapshot())"])
        }
        timer.resume()
        retained.append(timer) // dispatch source 出作用域即被取消，必须持有
    }

    private static func installSignalHandler(stats: Stats) {
        signal(SIGINT, SIG_IGN)
        signal(SIGTERM, SIG_IGN)
        for sig in [SIGINT, SIGTERM] {
            let source = DispatchSource.makeSignalSource(signal: sig, queue: .main)
            source.setEventHandler {
                Log.info("stats", ["kind": "final", "signal": "\(sig)", "stats": "\(stats.snapshot())"])
                exit(0)
            }
            source.resume()
            retained.append(source)
        }
    }

    /// 常驻 dispatch source 的持有容器（timer / signal source）。
    private static var retained: [any DispatchSourceProtocol] = []
}

/// responder 的引用语义盒子：handler 注册时 responder 尚不存在（client 未创建），
/// subscribe 之前由 setup 填入——与 Rust 版 OnceLock 同一模式。
final class ResponderBox<T> {
    var responder: T?
}
