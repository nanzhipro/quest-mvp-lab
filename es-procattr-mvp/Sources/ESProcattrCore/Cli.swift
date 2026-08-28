import Foundation

/// 命令行接口（stdlib 手写解析，零第三方依赖）。
///
/// 用法：`es-procattr <config.json> [--output <path.jsonl>]`
public struct Cli {
    public let configPath: String
    public let output: String?

    public init(configPath: String, output: String?) {
        self.configPath = configPath
        self.output = output
    }

    /// 解析命令行参数；失败抛错（由 main 统一 exit）。
    public static func parse(_ args: [String]) throws -> Cli {
        var configPath: String?
        var output: String?
        var i = 1
        while i < args.count {
            let arg = args[i]
            if arg == "--output" || arg == "-o" {
                guard i + 1 < args.count else { throw CliError.missingValue(arg) }
                output = args[i + 1]
                i += 2
                continue
            }
            if arg.hasPrefix("--output=") {
                output = String(arg.dropFirst("--output=".count))
                i += 1
                continue
            }
            if arg == "--help" || arg == "-h" {
                print(usage)
                throw CliError.help
            }
            if arg.hasPrefix("-") {
                throw CliError.unknownFlag(arg)
            }
            if configPath == nil {
                configPath = arg
            } else {
                throw CliError.unexpectedArgument(arg)
            }
            i += 1
        }
        guard let configPath else { throw CliError.missingConfigPath }
        return Cli(configPath: configPath, output: output)
    }

    public static var usage: String {
        """
        es-procattr — ESF 进程归因（AI Agent 进程基因 / 进程链 / 行为链）

        用法:
          sudo ./ESProcattr.app/Contents/MacOS/es-procattr <config.json> [--output <path.jsonl>]

        参数:
          <config.json>           配置文件路径（见 config.example.json）
          -o, --output <path>    可选 JSONL 事件落盘路径
          -h, --help             显示本帮助
        """
    }
}

public enum CliError: Error, CustomStringConvertible {
    case missingConfigPath
    case missingValue(String)
    case unknownFlag(String)
    case unexpectedArgument(String)
    case help

    public var description: String {
        switch self {
        case .missingConfigPath: "缺少配置文件路径（见 --help）"
        case .missingValue(let f): "\(f) 缺少值"
        case .unknownFlag(let f): "未知参数 \(f)"
        case .unexpectedArgument(let a): "多余参数 \(a)"
        case .help: ""
        }
    }
}
