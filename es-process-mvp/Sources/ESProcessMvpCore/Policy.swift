/// 策略：按进程 bundleId（代码签名标识）匹配。纳入管控的进程，其打开 PDF 文件的动作
/// 一律 DENY；其余（非 PDF、或未纳入管控的进程）默认 ALLOW。
public struct Policy: Equatable {
    public let controlledBundleIds: Set<String>

    public init(controlledBundleIds: Set<String>) {
        self.controlledBundleIds = controlledBundleIds
    }

    /// 该 bundleId 是否纳入管控。
    public func isControlled(_ bundleId: String) -> Bool {
        !bundleId.isEmpty && controlledBundleIds.contains(bundleId)
    }

    /// 裁决一条 AUTH_OPEN：管控进程且目标是 PDF → DENY，其余 ALLOW。
    public func denyOpen(bundleId: String, path: String) -> Bool {
        isControlled(bundleId) && isPDF(path)
    }
}

/// PDF 文件判定：路径以 `.pdf` 结尾（大小写不敏感）。
/// 扩展名判定，不读文件内容（AUTH_OPEN 为阻塞事件，不应在 handler 内做 I/O）。
/// 已知局限：改名可绕过（MVP 接受）。
public func isPDF(_ path: String) -> Bool {
    path.lowercased().hasSuffix(".pdf")
}
