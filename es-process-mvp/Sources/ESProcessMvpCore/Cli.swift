import ArgumentParser

/// 命令行接口（swift-argument-parser）。仅负责解析，语义归 `AppConfig`。
public struct Cli: ParsableCommand {
    public static let configuration = CommandConfiguration(
        commandName: "es-process-mvp",
        abstract: "进程级 AUTH_OPEN 管控：按 bundleId 匹配策略，纳入管控的进程 AUTH_OPEN 一律 DENY"
    )

    /// YAML 策略配置文件路径（见 config.example.yaml）。
    @Argument(help: "策略配置文件路径（YAML，见 config.example.yaml）")
    public var configPath: String

    public init() {}
}
