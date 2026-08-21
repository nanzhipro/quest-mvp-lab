import EndpointSecurity
import Foundation

/// 一条已从 es_message_t 提取为自有数据的 AUTH_OPEN 事件。
public struct OpenEvent {
    /// 不透明应答令牌，仅在回调期间有效，只允许回传给 `Responder`。
    public let msg: UnsafeRawPointer
    public let path: String
    public let fflag: UInt32
    public let stMode: UInt32
}

public typealias OpenHandler = (OpenEvent) -> Void

/// 应答器：创建 client 后才存在（es_new_client 与 handler 注册同时发生），
/// 因此由后端在 newClient 成功后提供。
public typealias Responder = (_ msg: UnsafeRawPointer, _ flags: UInt32, _ cache: Bool) -> Result<Void, EsError>

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
            return "es_muting_inverted 自检未通过（inversion 未生效）"
        }
    }
}

public protocol EsBackend: AnyObject {
    func newClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError>
    func responder() -> Responder
    func defaultTargetMuteCount() -> Result<Int, EsError>
    func unmuteAllTargetPaths() -> Result<Void, EsError>
    func invertTargetPathMuting() -> Result<Void, EsError>
    func ensureTargetMutingInverted() -> Result<Void, EsError>
    func muteTargetPrefix(_ path: String) -> Result<Void, EsError>
    func subscribeAuthOpen() -> Result<Void, EsError>
}

private func check(_ op: String, _ rc: es_return_t) -> Result<Void, EsError> {
    rc == ES_RETURN_SUCCESS ? .success(()) : .failure(.call(op: op, rc: Int32(rc.rawValue)))
}

/// 真实后端：直接调用 EndpointSecurity C API（Swift 闭包自动桥接 block，无需 shim）。
public final class RealEs: EsBackend {
    private var client: OpaquePointer?

    public init() {}

    /// 除 newClient 外的所有调用都以 client 已创建为前提（setup 序列保证）。
    private var requireClient: OpaquePointer {
        guard let client else { preconditionFailure("EsBackend 方法必须在 newClient 成功后调用") }
        return client
    }

    public func newClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError> {
        var client: OpaquePointer?
        let rc = es_new_client(&client) { _, msgPtr in
            let message = msgPtr.pointee
            guard message.event_type == ES_EVENT_TYPE_AUTH_OPEN else { return }
            let file = message.event.open.file.pointee
            // es_string_token_t 不保证 NUL 结尾，按 length 解码（CChar=Int8 需 rebind 为 UInt8）
            let path = file.path.data.map {
                $0.withMemoryRebound(to: UInt8.self, capacity: file.path.length) {
                    String(decoding: UnsafeBufferPointer(start: $0, count: file.path.length), as: UTF8.self)
                }
            } ?? ""
            handler(OpenEvent(
                msg: UnsafeRawPointer(msgPtr),
                path: path,
                fflag: UInt32(bitPattern: message.event.open.fflag),
                stMode: UInt32(file.stat.st_mode)
            ))
        }
        guard rc == ES_NEW_CLIENT_RESULT_SUCCESS, let client else {
            return .failure(.newClient(Int32(rc.rawValue)))
        }
        self.client = client
        return .success(())
    }

    public func responder() -> Responder {
        { msg, flags, cache in
            let typed = msg.assumingMemoryBound(to: es_message_t.self)
            // AUTH_OPEN 是 flags 类事件，必须 es_respond_flags_result；
            // 误用 es_respond_auth_result 会整体失败并触发 deadline kill（实测）。
            let rc = es_respond_flags_result(self.requireClient, typed, flags, cache)
            return rc == ES_RESPOND_RESULT_SUCCESS
                ? .success(())
                : .failure(.call(op: "es_respond_flags_result", rc: Int32(rc.rawValue)))
        }
    }

    public func defaultTargetMuteCount() -> Result<Int, EsError> {
        // SDK 声明的 OUT 参数内层为非空指针：先以占位指针初始化槽位，成功后由 API 覆写
        let slot = UnsafeMutablePointer<UnsafeMutablePointer<es_muted_paths_t>>.allocate(capacity: 1)
        slot.initialize(to: UnsafeMutablePointer(bitPattern: 1)!)
        defer {
            slot.deinitialize(count: 1)
            slot.deallocate()
        }
        let rc = es_muted_paths_events(requireClient, slot)
        guard rc == ES_RETURN_SUCCESS else {
            return .failure(.call(op: "es_muted_paths_events", rc: Int32(rc.rawValue)))
        }
        let muted = slot.pointee
        defer { es_release_muted_paths(muted) }
        return .success(muted.pointee.count)
    }

    public func unmuteAllTargetPaths() -> Result<Void, EsError> {
        check("es_unmute_all_target_paths", es_unmute_all_target_paths(requireClient))
    }

    public func invertTargetPathMuting() -> Result<Void, EsError> {
        check("es_invert_muting", es_invert_muting(requireClient, ES_MUTE_INVERSION_TYPE_TARGET_PATH))
    }

    public func ensureTargetMutingInverted() -> Result<Void, EsError> {
        switch es_muting_inverted(requireClient, ES_MUTE_INVERSION_TYPE_TARGET_PATH) {
        case ES_MUTE_INVERTED: .success(())
        case ES_MUTE_NOT_INVERTED: .failure(.notInverted)
        default: .failure(.call(op: "es_muting_inverted", rc: -1))
        }
    }

    public func muteTargetPrefix(_ path: String) -> Result<Void, EsError> {
        path.withCString { check("es_mute_path", es_mute_path(requireClient, $0, ES_MUTE_PATH_TYPE_TARGET_PREFIX)) }
    }

    public func subscribeAuthOpen() -> Result<Void, EsError> {
        var events = [ES_EVENT_TYPE_AUTH_OPEN]
        return check("es_subscribe", es_subscribe(requireClient, &events, 1))
    }

    deinit {
        if let client { es_delete_client(client) }
    }
}

/// 内存后端：录制 setup 调用序列与应答内容，可手动回放事件，支撑无 root 的完整测试。
public final class MockEs: EsBackend {
    public private(set) var calls: [String] = []
    public private(set) var responds: [(flags: UInt32, cache: Bool)] = []
    private var handler: OpenHandler?
    private var newClientRc: Int32 = 0
    private var invertEffective = true
    private var inverted = false

    public init() {}

    /// 预设 es_new_client 失败码（如 4 模拟未授 FDA）。
    public static func failingNewClient(_ rc: Int32) -> MockEs {
        let mock = MockEs()
        mock.newClientRc = rc
        return mock
    }

    /// inversion 调用不被内核接受的场景。
    public static func inversionRejected() -> MockEs {
        let mock = MockEs()
        mock.invertEffective = false
        return mock
    }

    /// 回放一条 AUTH_OPEN 事件，驱动已注册的 handler。
    public func fire(path: String, stMode: UInt32, fflag: UInt32) {
        guard let handler else { preconditionFailure("fire 前须先 newClient") }
        handler(OpenEvent(msg: UnsafeRawPointer(bitPattern: 1)!, path: path, fflag: fflag, stMode: stMode))
    }

    public func newClient(_ handler: @escaping OpenHandler) -> Result<Void, EsError> {
        calls.append("new_client")
        guard newClientRc == 0 else { return .failure(.newClient(newClientRc)) }
        self.handler = handler
        return .success(())
    }

    public func responder() -> Responder {
        { [unowned self] _, flags, cache in
            self.responds.append((flags, cache))
            return .success(())
        }
    }

    public func defaultTargetMuteCount() -> Result<Int, EsError> {
        calls.append("default_target_mute_count")
        return .success(0)
    }

    public func unmuteAllTargetPaths() -> Result<Void, EsError> {
        calls.append("unmute_all_target_paths")
        return .success(())
    }

    public func invertTargetPathMuting() -> Result<Void, EsError> {
        calls.append("invert_muting")
        inverted = invertEffective
        return .success(())
    }

    public func ensureTargetMutingInverted() -> Result<Void, EsError> {
        calls.append("ensure_inverted")
        return inverted ? .success(()) : .failure(.notInverted)
    }

    public func muteTargetPrefix(_ path: String) -> Result<Void, EsError> {
        calls.append("mute_target_prefix:\(path)")
        return .success(())
    }

    public func subscribeAuthOpen() -> Result<Void, EsError> {
        calls.append("subscribe_auth_open")
        return .success(())
    }
}
