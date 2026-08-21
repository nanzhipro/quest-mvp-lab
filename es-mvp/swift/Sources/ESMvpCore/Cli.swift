import ArgumentParser

/// 命令行接口（swift-argument-parser）。仅负责解析，语义归 `AppConfig`。
public struct Cli: ParsableCommand {
    public static let configuration = CommandConfiguration(
        commandName: "esmvp-swift",
        abstract: "目录级 AUTH_OPEN 管控：es_invert_muting + 内核授权缓存的最小验证"
    )

    /// 监控目录（可重复）。不指定 = 全部 AUTH_OPEN 内核侧静音。
    @Option(name: .long, help: "监控目录（可重复）。不指定 = 全部 AUTH_OPEN 内核侧静音。")
    public var watch: [String] = []

    /// ALLOW 响应写入内核授权缓存（DENY 永不缓存）。
    @Flag(name: .long, help: "ALLOW 响应写入内核授权缓存（DENY 永不缓存）。")
    public var cache = false

    /// 打印每条 ALLOW 事件（DENY 始终打印）。
    @Flag(name: .long, help: "打印每条 ALLOW 事件（DENY 始终打印）。")
    public var verbose = false

    /// 统计输出间隔（秒）。
    @Option(name: .long, help: "统计输出间隔（秒）。")
    public var statsInterval: UInt64 = 10

    public init() {}
}
