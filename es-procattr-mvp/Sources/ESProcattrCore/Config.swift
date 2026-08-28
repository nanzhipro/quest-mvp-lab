import Foundation

/// 单条根匹配规则：路径子串或代码签名标识。
public struct RootRule: Equatable {
    public var pathContains: String?
    public var signingId: String?

    public func matches(_ gene: ProcGene) -> Bool {
        if let sub = pathContains, !sub.isEmpty {
            if gene.path.contains(sub) { return true }
            // 解释型进程（python/node 等）的「身份」在 argv（脚本/模块路径），
            // 而非解析后的解释器二进制路径（对齐 Santa 捕获 Program.arguments）。
            if gene.arguments.contains(where: { $0.contains(sub) }) { return true }
        }
        if let sid = signingId, !sid.isEmpty, gene.signingId == sid { return true }
        return false
    }
}

/// 根匹配器：多个规则任一命中即视为「关注根进程」。
public struct RootMatcher: Equatable {
    public let rules: [RootRule]

    public init(rules: [RootRule]) {
        self.rules = rules
    }

    public func matches(_ gene: ProcGene) -> Bool {
        rules.contains { $0.matches(gene) }
    }

    public var isEmpty: Bool { rules.isEmpty }

    /// 人类可读的规则描述（日志/报告用）。
    public var description: String {
        rules.map { rule -> String in
            if let p = rule.pathContains { return "pathContains:\(p)" }
            if let s = rule.signingId { return "signingId:\(s)" }
            return "(empty)"
        }.joined(separator: ",")
    }
}

/// 报告配置。
public struct ReportConfig: Equatable {
    public var snapshotSeconds: TimeInterval
    public var logOpen: Bool
    public var logLibraryOpen: Bool

    public init(snapshotSeconds: TimeInterval = 5, logOpen: Bool = false, logLibraryOpen: Bool = false) {
        self.snapshotSeconds = snapshotSeconds
        self.logOpen = logOpen
        self.logLibraryOpen = logLibraryOpen
    }
}

/// 运行配置：JSON 语义归 AppConfig，CLI 只注入 configPath 与输出路径。
public struct AppConfig {
    public let configPath: String
    public let matcher: RootMatcher
    public let report: ReportConfig
    /// JSONL 事件输出路径（CLI --output，可选）。
    public let outputJsonl: String?

    public init(configPath: String, matcher: RootMatcher, report: ReportConfig, outputJsonl: String?) {
        self.configPath = configPath
        self.matcher = matcher
        self.report = report
        self.outputJsonl = outputJsonl
    }

    public static func from(cli: Cli) throws -> AppConfig {
        let raw = try RawConfig.load(path: cli.configPath)
        return AppConfig(configPath: cli.configPath, matcher: raw.matcher, report: raw.report, outputJsonl: cli.output)
    }
}

/// 从 JSON 解析出的原始配置（磁盘形态）。
struct RawConfig {
    let matcher: RootMatcher
    let report: ReportConfig

    static func load(path: String) throws -> RawConfig {
        let data: Data
        do {
            data = try Data(contentsOf: URL(fileURLWithPath: path))
        } catch {
            throw ConfigError.unreadable(path: path, reason: "\(error)")
        }
        let root: Any
        do {
            root = try JSONSerialization.jsonObject(with: data)
        } catch {
            throw ConfigError.invalidJson(path: path, reason: "\(error)")
        }
        guard let dict = root as? [String: Any] else {
            throw ConfigError.invalidStructure(path: path, reason: "顶层必须是 JSON object（如 `{\"roots\": [...]}`）")
        }
        guard let rootsRaw = dict["roots"] as? [Any] else {
            throw ConfigError.invalidStructure(path: path, reason: "缺少 `roots` 字段（数组）")
        }
        var rules: [RootRule] = []
        for (i, item) in rootsRaw.enumerated() {
            guard let r = item as? [String: Any] else {
                throw ConfigError.invalidStructure(path: path, reason: "roots[\(i)] 必须是 object（pathContains / signingId）")
            }
            var rule = RootRule()
            if let pc = r["pathContains"] as? String { rule.pathContains = pc.trimmingCharacters(in: .whitespaces) }
            if let sid = r["signingId"] as? String { rule.signingId = sid.trimmingCharacters(in: .whitespaces) }
            if rule.pathContains == nil && rule.signingId == nil {
                throw ConfigError.invalidStructure(path: path, reason: "roots[\(i)] 需含 pathContains 或 signingId")
            }
            rules.append(rule)
        }

        var report = ReportConfig()
        if let rr = dict["report"] as? [String: Any] {
            if let s = rr["snapshotSeconds"] as? Int { report.snapshotSeconds = TimeInterval(s) }
            if let s = rr["snapshotSeconds"] as? Double { report.snapshotSeconds = s }
            if let o = rr["logOpen"] as? Bool { report.logOpen = o }
            if let l = rr["logLibraryOpen"] as? Bool { report.logLibraryOpen = l }
        }
        return RawConfig(matcher: RootMatcher(rules: rules), report: report)
    }
}

public enum ConfigError: Error, CustomStringConvertible {
    case unreadable(path: String, reason: String)
    case invalidJson(path: String, reason: String)
    case invalidStructure(path: String, reason: String)

    public var description: String {
        switch self {
        case .unreadable(let path, let reason): "配置文件不可读 \(path)：\(reason)"
        case .invalidJson(let path, let reason): "JSON 解析失败 \(path)：\(reason)"
        case .invalidStructure(let path, let reason): "配置结构无效 \(path)：\(reason)"
        }
    }
}
