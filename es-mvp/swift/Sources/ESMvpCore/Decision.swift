import Foundation
import UniformTypeIdentifiers

public enum Verdict: Equatable {
    case allow
    case deny
}

/// 一次裁决的结果及其应答参数派生。
public struct Decision {
    public let verdict: Verdict
    /// 命中拦截规则时的 MIME（仅诊断展示用）。
    public let deniedMime: String?

    public var isDeny: Bool { verdict == .deny }

    /// 应答 flags：DENY = 0；ALLOW = 透传事件原始 fflag。
    public func responseFlags(fflag: UInt32) -> UInt32 {
        isDeny ? 0 : fflag
    }

    /// 是否写入内核授权缓存：DENY 永不缓存（每次拦截都可观测，防误封固化）。
    public func cacheable(cacheAllow: Bool) -> Bool {
        cacheAllow && !isDeny
    }
}

/// 裁决引擎：按 MIME 类型决定 AUTH_OPEN 放行/拒绝。纯函数，无 ES 依赖。
public enum DecisionEngine {
    /// 被拦截的 MIME 类型（MVP 规则：图片禁止打开）。
    static let deniedMimes: Set<String> = ["image/png", "image/jpeg"]

    /// 裁决一次 open。仅普通文件参与类型判定；目录等其余类型一律放行。
    public static func decide(path: String, stMode: UInt32) -> Decision {
        guard isRegularFile(stMode) else { return allow() }
        guard let mime = mimeOf(path) else { return allow() }
        if deniedMimes.contains(mime) {
            return Decision(verdict: .deny, deniedMime: mime)
        }
        return allow()
    }

    private static func allow() -> Decision {
        Decision(verdict: .allow, deniedMime: nil)
    }

    /// macOS 的 mode_t 为 16 位，事件通路统一用 UInt32 承载后比较。
    static func isRegularFile(_ stMode: UInt32) -> Bool {
        stMode & UInt32(S_IFMT) == UInt32(S_IFREG)
    }

    /// 扩展名 → UTType → MIME（与 ObjC 版同一系统映射）；无扩展名或未知类型返回 nil。
    /// 不读文件内容，无 TCC/FDA 依赖。已知局限：改名可绕过（MVP 接受）。
    static func mimeOf(_ path: String) -> String? {
        let ext = URL(fileURLWithPath: path).pathExtension.lowercased()
        guard !ext.isEmpty else { return nil }
        return UTType(filenameExtension: ext)?.preferredMIMEType
    }
}
