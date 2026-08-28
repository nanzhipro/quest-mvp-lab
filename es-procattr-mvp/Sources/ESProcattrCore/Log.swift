import Foundation

/// 极简控制台日志：`时间戳 LEVEL 消息 key=value`（key 字典序排列，输出确定）。
/// 无 ANSI 转义——重定向到文件/管道后仍可 grep、可被脚本消费。
public enum Log {
    public static func info(_ message: String, _ fields: [String: String] = [:]) {
        emit(level: "INFO", message, fields)
    }

    public static func warn(_ message: String, _ fields: [String: String] = [:]) {
        emit(level: "WARN", message, fields)
    }

    public static func error(_ message: String, _ fields: [String: String] = [:]) {
        emit(level: "ERROR", message, fields)
    }

    private static func emit(level: String, _ message: String, _ fields: [String: String]) {
        let kv = fields.sorted { $0.key < $1.key }.map { "\($0)=\($1)" }.joined(separator: " ")
        let line = kv.isEmpty ? message : "\(message) \(kv)"
        print("\(formatter.string(from: Date())) \(level.padding(toLength: 5, withPad: " ", startingAt: 0)) \(line)")
        fflush(stdout) // 行缓冲语义：重定向后崩溃不丢日志
    }

    private static let formatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return f
    }()
}
