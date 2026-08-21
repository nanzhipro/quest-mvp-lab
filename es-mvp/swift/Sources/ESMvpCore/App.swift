import Foundation

/// 应用编排：ES 初始化序列（顺序有官方约束）+ 事件处理 + 周期统计 + 信号收尾。
public enum App {
    /// 生产入口：真实后端 + 阻塞运行（dispatchMain 不返回）。
    public static func run(config: AppConfig) -> Result<Void, EsError> {
        let stats = Stats()
        switch setup(config: config, backend: RealEs(), stats: stats) {
        case .failure(let error): return .failure(error)
        case .success: break
        }

        Log.info("started", ["mode": "\(config.mode)", "cache": "\(config.cacheAllow)"])
        startStatsReporter(interval: config.statsInterval, stats: stats)
        installSignalHandler(stats: stats)
        dispatchMain()
    }

    /// ES 初始化序列（ESClient.h 注释约束：invert 前不得有 AUTH 订阅，订阅必须最后）：
    /// new_client → unmute_all_target_paths → invert → 自检 → 应用静音规则 → subscribe。
    /// inversion 语义下静音规则即"白名单"：只接收命中目录的事件；规则为空 = 全静音。
    public static func setup(config: AppConfig, backend: any EsBackend, stats: Stats) -> Result<Void, EsError> {
        let box = ResponderBox()
        switch backend.newClient({ event in
            handleEvent(event, responderBox: box, stats: stats,
                        verbose: config.verbose, cacheAllow: config.cacheAllow)
        }) {
        case .failure(let error): return .failure(error)
        case .success: box.responder = backend.responder()
        }

        if case .failure(let error) = backend.defaultTargetMuteCount().map({ count in
            Log.info("默认 target mute set 条目数（inversion 前留档）", ["count": "\(count)"])
        }) {
            Log.warn("默认 mute set 查询失败（不阻断）", ["error": "\(error)"])
        }

        let steps: [() -> Result<Void, EsError>] = [
            backend.unmuteAllTargetPaths,
            backend.invertTargetPathMuting,
            backend.ensureTargetMutingInverted,
        ]
        for step in steps {
            if case .failure(let error) = step() { return .failure(error) }
        }
        for rule in config.watchRules {
            if case .failure(let error) = backend.muteTargetPrefix(rule) { return .failure(error) }
        }
        return backend.subscribeAuthOpen()
    }

    private static func handleEvent(
        _ event: OpenEvent,
        responderBox: ResponderBox,
        stats: Stats,
        verbose: Bool,
        cacheAllow: Bool
    ) {
        stats.recordReceived()

        let decision = DecisionEngine.decide(path: event.path, stMode: event.stMode)
        let flags = decision.responseFlags(fflag: event.fflag)
        let cache = decision.cacheable(cacheAllow: cacheAllow)

        // responder 在 subscribe 之前已就位，此处必然存在
        guard let responder = responderBox.responder else {
            Log.error("responder 未初始化（不应发生）", ["path": event.path])
            stats.recordRespondError()
            return
        }
        if case .failure(let error) = responder(event.msg, flags, cache) {
            Log.error("应答失败（deadline 风险）", ["error": "\(error)"])
            stats.recordRespondError()
        }
        stats.recordVerdict(deny: decision.isDeny)

        if verbose || decision.isDeny {
            Log.info("event", [
                "decision": decision.isDeny ? "DENY" : "ALLOW",
                "path": event.path,
                "mime": decision.deniedMime ?? "-",
                "cache": "\(cache)",
            ])
        }
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
/// subscribe 之前由 setup 填入——与 Rust 版的 OnceLock 同一模式。
final class ResponderBox {
    var responder: Responder?
}
