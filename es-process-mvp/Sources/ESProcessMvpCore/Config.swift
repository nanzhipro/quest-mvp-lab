import Foundation
import Yams

/// 运行配置：从 YAML 文件加载策略，语义归 `AppConfig`。
public struct AppConfig {
    public let configPath: String
    public let policy: Policy
    /// 统计输出间隔（秒）。固定值，不再暴露 CLI 参数。
    public let statsInterval: TimeInterval

    public init(configPath: String, policy: Policy, statsInterval: TimeInterval = 10) {
        self.configPath = configPath
        self.policy = policy
        self.statsInterval = statsInterval
    }

    public static func from(cli: Cli) throws -> AppConfig {
        AppConfig(configPath: cli.configPath, policy: try PolicyConfig.load(path: cli.configPath).policy)
    }
}

/// 从 YAML 解析出的策略配置（磁盘形态）。
public struct PolicyConfig {
    public let policy: Policy

    public init(policy: Policy) {
        self.policy = policy
    }

    /// 加载并解析 YAML。结构：顶层 mapping，`bundleIds` 为字符串列表。
    public static func load(path: String) throws -> PolicyConfig {
        let content: String
        do {
            content = try String(contentsOfFile: path, encoding: .utf8)
        } catch {
            throw ConfigError.unreadable(path: path, reason: "\(error)")
        }

        let root: Any?
        do {
            root = try Yams.load(yaml: content)
        } catch {
            throw ConfigError.invalidYaml(path: path, reason: "\(error)")
        }

        guard let dict = root as? [String: Any] else {
            throw ConfigError.invalidStructure(path: path, reason: "顶层必须是 mapping（如 `bundleIds:`）")
        }
        guard let raw = dict["bundleIds"] else {
            throw ConfigError.invalidStructure(path: path, reason: "缺少 `bundleIds` 字段")
        }
        guard let list = raw as? [String] else {
            throw ConfigError.invalidStructure(path: path, reason: "`bundleIds` 必须是字符串列表")
        }

        let ids = Set(list.map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty })
        return PolicyConfig(policy: Policy(controlledBundleIds: ids))
    }
}

public enum ConfigError: Error, CustomStringConvertible {
    case unreadable(path: String, reason: String)
    case invalidYaml(path: String, reason: String)
    case invalidStructure(path: String, reason: String)

    public var description: String {
        switch self {
        case .unreadable(let path, let reason): "配置文件不可读 \(path)：\(reason)"
        case .invalidYaml(let path, let reason): "YAML 解析失败 \(path)：\(reason)"
        case .invalidStructure(let path, let reason): "配置结构无效 \(path)：\(reason)"
        }
    }
}
