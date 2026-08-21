import EndpointSecurity
import Foundation

/// C 的 `audit_token_t`（进程内核身份，8×u32 不透明），直接从 ES 事件取，绝不手工构造。
public typealias AuditToken = audit_token_t

/// 一条已从 es_message_t 提取为自有数据的 AUTH_EXEC 事件（发现客户端专用）。
public struct ExecEvent {
    /// 不透明应答令牌，仅在回调期间有效，只允许回传给 `AuthResponder`。
    public let msg: UnsafeRawPointer
    /// 被 exec 的目标可执行文件路径（es_event_exec_t.target.executable.path）。
    public let targetPath: String
    /// 目标进程的 bundleId（代码签名标识，即 es_process_t.signing_id），用于策略匹配。
    public let bundleId: String
    /// 目标进程的精确 audit token（exec 后身份，pidversion 已更新），用于 es_mute_process。
    public let token: AuditToken
}

/// 一条已从 es_message_t 提取为自有数据的 AUTH_OPEN 事件（监控客户端专用）。
public struct OpenEvent {
    public let msg: UnsafeRawPointer
    public let path: String
    /// 打开文件的进程的 bundleId（es_process_t.signing_id）。
    public let bundleId: String
    public let fflag: UInt32
}

public typealias ExecHandler = (ExecEvent) -> Void
public typealias OpenHandler = (OpenEvent) -> Void

/// AUTH_EXEC 应答器：`deny=true` → ES_AUTH_RESULT_DENY，否则 ALLOW。
public typealias AuthResponder = (_ msg: UnsafeRawPointer, _ deny: Bool, _ cache: Bool) -> Result<Void, EsError>
/// AUTH_OPEN 应答器（flags 类）：flags=0 即拒绝，否则透传原始 fflag。
public typealias FlagsResponder = (_ msg: UnsafeRawPointer, _ flags: UInt32, _ cache: Bool) -> Result<Void, EsError>

public enum EsError: Error, CustomStringConvertible, Equatable {
    /// es_new_client 失败，rc 为 es_new_client_result_t。
    case newClient(Int32)
    /// 其余 ES 调用失败，rc 为 es_return_t / es_respond_result_t。
    case call(op: String, rc: Int32)
    /// inversion 自检未通过。
    case notInverted

    public var description: String {
        switch self {
        case .newClient(let rc):
            let hint = switch rc {
            case 3: "缺少 com.apple.developer.endpoint-security.client entitlement（检查签名/embedded profile）"
            case 4: "缺少 TCC 完全磁盘访问授权（系统设置 → 隐私与安全性 → 完全磁盘访问权限）"
            case 5: "需要 root 运行（使用 sudo）"
            default: "见 es_new_client_result_t 定义"
            }
            return "es_new_client 失败 rc=\(rc)：\(hint)"
        case .call(let op, let rc):
            return "\(op) 失败 rc=\(rc)"
        case .notInverted:
            return "es_muting_inverted 自检未通过（进程反转未生效）"
        }
    }
}

/// ES 后端协议：封装双客户端模型，使编排层（App）可被 Mock 完整测试（无 root）。
///
/// 双客户端是进程反转的硬约束（见 SPEC.md「反转客户端是盲的」）：
/// - **监控客户端**：进程反转，订阅 AUTH_OPEN，只收到被 watch（mute）进程的打开事件。
/// - **发现客户端**：非反转，订阅 AUTH_EXEC，看到全系统 exec，命中策略即 mute 到监控客户端。
public protocol EsBackend: AnyObject {
    // ---- 监控客户端（进程反转，AUTH_OPEN）----
    func newMonitorClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError>
    func invertProcessMuting() -> Result<Void, EsError>
    func ensureProcessMutingInverted() -> Result<Void, EsError>
    /// 反转语义下 es_mute_process = 开始 watch（投递该进程事件）。
    func watchProcess(token: AuditToken) -> Result<Void, EsError>
    func subscribeOpen() -> Result<Void, EsError>
    func openResponder() -> FlagsResponder

    // ---- 发现客户端（非反转，AUTH_EXEC）----
    func newDiscoveryClient(_ handler: @escaping ExecHandler) -> Result<Void, EsError>
    func subscribeExec() -> Result<Void, EsError>
    func execResponder() -> AuthResponder
}

private func check(_ op: String, _ rc: es_return_t) -> Result<Void, EsError> {
    rc == ES_RETURN_SUCCESS ? .success(()) : .failure(.call(op: op, rc: Int32(rc.rawValue)))
}

/// 解码 es_string_token_t（不保证 NUL 结尾，按 length 截取）。
private func decode(_ token: es_string_token_t) -> String {
    token.data.map {
        $0.withMemoryRebound(to: UInt8.self, capacity: token.length) {
            String(decoding: UnsafeBufferPointer(start: $0, count: token.length), as: UTF8.self)
        }
    } ?? ""
}

/// 真实后端：直接调用 EndpointSecurity C API（Swift 闭包自动桥接 block，无需 shim）。
public final class RealEs: EsBackend {
    private var monitorClient: OpaquePointer?
    private var discoveryClient: OpaquePointer?

    public init() {}

    private var requireMonitor: OpaquePointer {
        guard let monitorClient else { preconditionFailure("监控客户端尚未创建") }
        return monitorClient
    }

    private var requireDiscovery: OpaquePointer {
        guard let discoveryClient else { preconditionFailure("发现客户端尚未创建") }
        return discoveryClient
    }

    // MARK: - 监控客户端

    public func newMonitorClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError> {
        var client: OpaquePointer?
        let rc = es_new_client(&client) { _, msgPtr in
            let message = msgPtr.pointee
            guard message.event_type == ES_EVENT_TYPE_AUTH_OPEN else { return }
            let file = message.event.open.file.pointee
            let process = message.process.pointee
            handler(OpenEvent(
                msg: UnsafeRawPointer(msgPtr),
                path: decode(file.path),
                bundleId: decode(process.signing_id),
                fflag: UInt32(bitPattern: message.event.open.fflag)
            ))
        }
        guard rc == ES_NEW_CLIENT_RESULT_SUCCESS, let client else {
            return .failure(.newClient(Int32(rc.rawValue)))
        }
        self.monitorClient = client
        return .success(())
    }

    public func invertProcessMuting() -> Result<Void, EsError> {
        check("es_invert_muting", es_invert_muting(requireMonitor, ES_MUTE_INVERSION_TYPE_PROCESS))
    }

    public func ensureProcessMutingInverted() -> Result<Void, EsError> {
        switch es_muting_inverted(requireMonitor, ES_MUTE_INVERSION_TYPE_PROCESS) {
        case ES_MUTE_INVERTED: .success(())
        case ES_MUTE_NOT_INVERTED: .failure(.notInverted)
        default: .failure(.call(op: "es_muting_inverted", rc: -1))
        }
    }

    public func watchProcess(token: AuditToken) -> Result<Void, EsError> {
        var tok = token
        return check("es_mute_process", withUnsafePointer(to: &tok) { es_mute_process(requireMonitor, $0) })
    }

    public func subscribeOpen() -> Result<Void, EsError> {
        var events = [ES_EVENT_TYPE_AUTH_OPEN]
        return check("es_subscribe", es_subscribe(requireMonitor, &events, 1))
    }

    public func openResponder() -> FlagsResponder {
        { msg, flags, cache in
            let typed = msg.assumingMemoryBound(to: es_message_t.self)
            // AUTH_OPEN 是 flags 类事件，必须 es_respond_flags_result；误用 auth_result 会失败（实测）。
            let rc = es_respond_flags_result(self.requireMonitor, typed, flags, cache)
            return rc == ES_RESPOND_RESULT_SUCCESS
                ? .success(())
                : .failure(.call(op: "es_respond_flags_result", rc: Int32(rc.rawValue)))
        }
    }

    // MARK: - 发现客户端

    public func newDiscoveryClient(_ handler: @escaping ExecHandler) -> Result<Void, EsError> {
        var client: OpaquePointer?
        let rc = es_new_client(&client) { _, msgPtr in
            let message = msgPtr.pointee
            guard message.event_type == ES_EVENT_TYPE_AUTH_EXEC else { return }
            let target = message.event.exec.target.pointee
            handler(ExecEvent(
                msg: UnsafeRawPointer(msgPtr),
                targetPath: decode(target.executable.pointee.path),
                bundleId: decode(target.signing_id),
                token: target.audit_token
            ))
        }
        guard rc == ES_NEW_CLIENT_RESULT_SUCCESS, let client else {
            return .failure(.newClient(Int32(rc.rawValue)))
        }
        self.discoveryClient = client
        return .success(())
    }

    public func subscribeExec() -> Result<Void, EsError> {
        var events = [ES_EVENT_TYPE_AUTH_EXEC]
        return check("es_subscribe", es_subscribe(requireDiscovery, &events, 1))
    }

    public func execResponder() -> AuthResponder {
        { msg, deny, cache in
            let typed = msg.assumingMemoryBound(to: es_message_t.self)
            let result: es_auth_result_t = deny ? ES_AUTH_RESULT_DENY : ES_AUTH_RESULT_ALLOW
            let rc = es_respond_auth_result(self.requireDiscovery, typed, result, cache)
            return rc == ES_RESPOND_RESULT_SUCCESS
                ? .success(())
                : .failure(.call(op: "es_respond_auth_result", rc: Int32(rc.rawValue)))
        }
    }

    deinit {
        // es_delete_client 需与 es_new_client 同线程；本类始终在主线程创建/销毁。
        if let discoveryClient { es_delete_client(discoveryClient) }
        if let monitorClient { es_delete_client(monitorClient) }
    }
}

/// 内存后端：录制 setup 调用序列与应答内容，可手动回放事件，支撑无 root 的完整测试。
public final class MockEs: EsBackend {
    public private(set) var calls: [String] = []
    public private(set) var execResponds: [(deny: Bool, cache: Bool)] = []
    public private(set) var openResponds: [(flags: UInt32, cache: Bool)] = []
    private var openHandler: OpenHandler?
    private var execHandler: ExecHandler?
    private var monitorNewClientRc: Int32 = 0
    private var discoveryNewClientRc: Int32 = 0
    private var invertEffective = true
    private var inverted = false

    public init() {}

    /// 预设监控客户端 es_new_client 失败码。
    public static func failingMonitorClient(_ rc: Int32) -> MockEs {
        let mock = MockEs()
        mock.monitorNewClientRc = rc
        return mock
    }

    /// 预设发现客户端 es_new_client 失败码。
    public static func failingDiscoveryClient(_ rc: Int32) -> MockEs {
        let mock = MockEs()
        mock.discoveryNewClientRc = rc
        return mock
    }

    /// inversion 调用不被内核接受的场景。
    public static func inversionRejected() -> MockEs {
        let mock = MockEs()
        mock.invertEffective = false
        return mock
    }

    /// 回放一条 AUTH_EXEC 事件，驱动已注册的发现 handler。
    public func fireExec(targetPath: String, bundleId: String, token: AuditToken = MockEs.fakeToken) {
        guard let execHandler else { preconditionFailure("fireExec 前须先 newDiscoveryClient") }
        execHandler(ExecEvent(
            msg: UnsafeRawPointer(bitPattern: 1)!,
            targetPath: targetPath,
            bundleId: bundleId,
            token: token
        ))
    }

    /// 回放一条 AUTH_OPEN 事件，驱动已注册的监控 handler。
    public func fireOpen(path: String, bundleId: String, fflag: UInt32) {
        guard let openHandler else { preconditionFailure("fireOpen 前须先 newMonitorClient") }
        openHandler(OpenEvent(msg: UnsafeRawPointer(bitPattern: 1)!, path: path, bundleId: bundleId, fflag: fflag))
    }

    /// 测试用占位 token（值无意义，Mock 不校验）。
    public static var fakeToken: AuditToken {
        var tok = audit_token_t()
        tok.val.0 = 1
        tok.val.5 = 42
        return tok
    }

    // MARK: - 监控客户端

    public func newMonitorClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError> {
        calls.append("new_monitor_client")
        guard monitorNewClientRc == 0 else { return .failure(.newClient(monitorNewClientRc)) }
        self.openHandler = handler
        return .success(())
    }

    public func invertProcessMuting() -> Result<Void, EsError> {
        calls.append("invert_muting")
        inverted = invertEffective
        return .success(())
    }

    public func ensureProcessMutingInverted() -> Result<Void, EsError> {
        calls.append("ensure_inverted")
        return inverted ? .success(()) : .failure(.notInverted)
    }

    public func watchProcess(token: AuditToken) -> Result<Void, EsError> {
        calls.append("watch_process")
        return .success(())
    }

    public func subscribeOpen() -> Result<Void, EsError> {
        calls.append("subscribe_open")
        return .success(())
    }

    public func openResponder() -> FlagsResponder {
        { [unowned self] _, flags, cache in
            self.openResponds.append((flags, cache))
            return .success(())
        }
    }

    // MARK: - 发现客户端

    public func newDiscoveryClient(_ handler: @escaping ExecHandler) -> Result<Void, EsError> {
        calls.append("new_discovery_client")
        guard discoveryNewClientRc == 0 else { return .failure(.newClient(discoveryNewClientRc)) }
        self.execHandler = handler
        return .success(())
    }

    public func subscribeExec() -> Result<Void, EsError> {
        calls.append("subscribe_exec")
        return .success(())
    }

    public func execResponder() -> AuthResponder {
        { [unowned self] _, deny, cache in
            self.execResponds.append((deny, cache))
            return .success(())
        }
    }
}
