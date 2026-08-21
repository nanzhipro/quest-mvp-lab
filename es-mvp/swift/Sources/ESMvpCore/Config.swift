import Foundation

/// 运行配置（语义归此处；参数解析见 Cli）。
public struct AppConfig {
    public let watchRules: [String]
    public let cacheAllow: Bool
    public let verbose: Bool
    public let statsInterval: TimeInterval

    public init(watchRules: [String], cacheAllow: Bool, verbose: Bool, statsInterval: TimeInterval) {
        self.watchRules = watchRules
        self.cacheAllow = cacheAllow
        self.verbose = verbose
        self.statsInterval = statsInterval
    }

    public static func from(cli: Cli) throws -> AppConfig {
        AppConfig(
            watchRules: try cli.watch.map(normalizeWatchDir(_:)),
            cacheAllow: cli.cache,
            verbose: cli.verbose,
            statsInterval: TimeInterval(max(cli.statsInterval, 1))
        )
    }

    public var mode: Mode {
        watchRules.isEmpty ? .muteAll : .watchOnly(watchRules)
    }
}

public enum Mode: CustomStringConvertible, Equatable {
    /// 无 watch 目录：inversion + 空规则 = 全部 AUTH_OPEN 在内核侧抑制。
    case muteAll
    /// 只接收这些目录下的 AUTH_OPEN。
    case watchOnly([String])

    public var description: String {
        switch self {
        case .muteAll: "mute-all"
        case .watchOnly(let dirs): "watch-only dirs=[\(dirs.joined(separator: ", "))]"
        }
    }
}

public enum ConfigError: Error, CustomStringConvertible {
    case invalidWatchDir(input: String, reason: String)

    public var description: String {
        switch self {
        case .invalidWatchDir(let input, let reason): "watch 目录无效 \(input)：\(reason)"
        }
    }
}

/// 规范化 watch 目录：展开 `~` → realpath（解析符号链接）→ 保证尾斜杠。
/// 尾斜杠是硬约定：target-prefix 匹配是字符串级的，"/foo/bar" 会误伤 "/foo/bar2"。
public func normalizeWatchDir(_ input: String) throws -> String {
    let expanded = expandTilde(input)
    guard let resolved = realpath(expanded, nil) else {
        throw ConfigError.invalidWatchDir(input: input, reason: "realpath 失败：errno=\(errno)")
    }
    defer { free(resolved) }
    let path = String(cString: resolved)
    var isDir: ObjCBool = false
    guard FileManager.default.fileExists(atPath: path, isDirectory: &isDir), isDir.boolValue else {
        throw ConfigError.invalidWatchDir(input: input, reason: "不是目录")
    }
    return path.hasSuffix("/") ? path : path + "/"
}

private func expandTilde(_ input: String) -> String {
    if input == "~" || input.hasPrefix("~/") {
        return NSHomeDirectory() + input.dropFirst()
    }
    return input
}
