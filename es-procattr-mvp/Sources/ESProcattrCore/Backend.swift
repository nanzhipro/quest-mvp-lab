import EndpointSecurity
import Foundation

/// ES 后端协议：封装「一个 NOTIFY 订阅客户端」模型，使编排层（App）可被 Mock 完整测试（无 root）。
///
/// 与 es-process-mvp 的进程反转双客户端不同，本 MVP 做「全系统进程归因」——
/// 单一客户端订阅 NOTIFY_EXEC/FORK/EXIT + 文件行为事件，用户态维护进程树，
/// 事件按「是否在关注子树内」过滤后归因。NOTIFY 事件无应答义务，handler 内同步提取
/// 自有数据后即返回（「NOTIFY = 零持有」内存模式，见 macos-endpoint-security skill P1）。
public protocol EsBackend: AnyObject {
    /// 创建 ES client 并注册事件 handler（handler 在 ES 投递线程同步回调）。
    func newClient(_ handler: @escaping (EsEvent) -> Void) -> Result<Void, EsError>
    /// 订阅事件集（必须最后调用）。
    func subscribe() -> Result<Void, EsError>
    /// 删除 client（与 newClient 同线程）。
    func deleteClient() -> Result<Void, EsError>
}

public enum EsError: Error, CustomStringConvertible, Equatable {
    case newClient(Int32)
    case call(op: String, rc: Int32)

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
        }
    }
}

/// 解码 es_string_token_t（不保证 NUL 结尾，按 length 截取；data 为 NULL 返回空串）。
func decodeStringToken(_ token: es_string_token_t) -> String {
    token.data.map {
        $0.withMemoryRebound(to: UInt8.self, capacity: token.length) {
            String(decoding: UnsafeBufferPointer(start: $0, count: token.length), as: UTF8.self)
        }
    } ?? ""
}

/// 真实后端：直接调用 EndpointSecurity C API（Swift 闭包自动桥接 block，无需 shim）。
public final class RealEs: EsBackend {
    private var client: OpaquePointer?

    public init() {}

    /// 订阅的 NOTIFY 事件集：进程生命周期（exec/fork/exit）+ 文件行为（open/close/create/rename/unlink/write）。
    private static let subscribedEvents: [es_event_type_t] = [
        ES_EVENT_TYPE_NOTIFY_EXEC,
        ES_EVENT_TYPE_NOTIFY_FORK,
        ES_EVENT_TYPE_NOTIFY_EXIT,
        ES_EVENT_TYPE_NOTIFY_OPEN,
        ES_EVENT_TYPE_NOTIFY_CLOSE,
        ES_EVENT_TYPE_NOTIFY_CREATE,
        ES_EVENT_TYPE_NOTIFY_RENAME,
        ES_EVENT_TYPE_NOTIFY_UNLINK,
        ES_EVENT_TYPE_NOTIFY_WRITE,
    ]

    public func newClient(_ handler: @escaping (EsEvent) -> Void) -> Result<Void, EsError> {
        var client: OpaquePointer?
        let rc = es_new_client(&client) { _, msgPtr in
            let message = msgPtr.pointee
            handler(self.decodeEvent(message))
        }
        guard rc == ES_NEW_CLIENT_RESULT_SUCCESS, let client else {
            return .failure(.newClient(Int32(rc.rawValue)))
        }
        self.client = client
        return .success(())
    }

    public func subscribe() -> Result<Void, EsError> {
        guard let client else { return .failure(.call(op: "es_subscribe", rc: -1)) }
        var events = Self.subscribedEvents
        let rc = es_subscribe(client, &events, UInt32(events.count))
        return rc == ES_RETURN_SUCCESS ? .success(()) : .failure(.call(op: "es_subscribe", rc: Int32(rc.rawValue)))
    }

    public func deleteClient() -> Result<Void, EsError> {
        guard let client else { return .success(()) }
        let rc = es_delete_client(client)
        self.client = nil
        return rc == ES_RETURN_SUCCESS ? .success(()) : .failure(.call(op: "es_delete_client", rc: Int32(rc.rawValue)))
    }

    deinit {
        if let client { es_delete_client(client) }
    }

    // MARK: - 解码

    private func decodeEvent(_ m: es_message_t) -> EsEvent {
        let actor = decodeProcess(m.process.pointee, version: m.version)
        switch m.event_type {
        case ES_EVENT_TYPE_NOTIFY_EXEC:
            let target = decodeProcess(m.event.exec.target.pointee, version: m.version)
            return EsEvent(kind: .exec, actor: actor, target: target)
        case ES_EVENT_TYPE_NOTIFY_FORK:
            let child = decodeProcess(m.event.fork.child.pointee, version: m.version)
            return EsEvent(kind: .fork, actor: actor, target: child)
        case ES_EVENT_TYPE_NOTIFY_EXIT:
            return EsEvent(kind: .exit, actor: actor, exitStatus: m.event.exit.stat)
        case ES_EVENT_TYPE_NOTIFY_OPEN:
            return EsEvent(kind: .open, actor: actor,
                           path: decodeStringToken(m.event.open.file.pointee.path))
        case ES_EVENT_TYPE_NOTIFY_CLOSE:
            return EsEvent(kind: .close, actor: actor,
                           path: decodeStringToken(m.event.close.target.pointee.path),
                           closeModified: m.event.close.modified)
        case ES_EVENT_TYPE_NOTIFY_CREATE:
            return EsEvent(kind: .create, actor: actor, path: decodeCreateDestination(m.event.create))
        case ES_EVENT_TYPE_NOTIFY_RENAME:
            let src = decodeStringToken(m.event.rename.source.pointee.path)
            let dst = decodeRenameDestination(m.event.rename)
            return EsEvent(kind: .rename, actor: actor, path: src, destPath: dst)
        case ES_EVENT_TYPE_NOTIFY_UNLINK:
            return EsEvent(kind: .unlink, actor: actor,
                           path: decodeStringToken(m.event.unlink.target.pointee.path))
        case ES_EVENT_TYPE_NOTIFY_WRITE:
            return EsEvent(kind: .write, actor: actor,
                           path: decodeStringToken(m.event.write.target.pointee.path))
        default:
            // 未订阅事件防御性兜底：当作 write 但空路径（不应发生）
            return EsEvent(kind: .write, actor: actor)
        }
    }

    private func decodeProcess(_ p: es_process_t, version: UInt32) -> ProcGene {
        var g = ProcGene()
        let tok = Token(p.audit_token)
        g.pid = tok.pid
        g.pidversion = tok.pidversion
        g.path = decodeStringToken(p.executable.pointee.path)
        g.signingId = decodeStringToken(p.signing_id)
        g.teamId = decodeStringToken(p.team_id)
        g.isPlatformBinary = p.is_platform_binary
        g.isEsClient = p.is_es_client
        g.codesigningFlags = p.codesigning_flags
        g.ppid = p.ppid
        g.originalPpid = p.original_ppid
        if version >= 3 {
            g.startTimeSec = Int64(p.start_time.tv_sec)
        }
        if version >= 4 {
            g.responsiblePid = Token(p.responsible_audit_token).pid
            let parentTok = Token(p.parent_audit_token)
            g.parentPid = parentTok.pid
            g.parentPidversion = parentTok.pidversion
        }
        return g
    }

    /// create 事件目标路径：EXISTING_FILE 用 existing_file；NEW_PATH 用 dir + "/" + filename。
    private func decodeCreateDestination(_ c: es_event_create_t) -> String {
        if c.destination_type == ES_DESTINATION_TYPE_EXISTING_FILE {
            return decodeStringToken(c.destination.existing_file.pointee.path)
        }
        let dir = decodeStringToken(c.destination.new_path.dir.pointee.path)
        let name = decodeStringToken(c.destination.new_path.filename)
        return dir.hasSuffix("/") ? dir + name : dir + "/" + name
    }

    /// rename 事件目标路径：EXISTING_FILE 用 existing_file；NEW_PATH 用 dir + "/" + filename。
    private func decodeRenameDestination(_ r: es_event_rename_t) -> String {
        if r.destination_type == ES_DESTINATION_TYPE_EXISTING_FILE {
            return decodeStringToken(r.destination.existing_file.pointee.path)
        }
        let dir = decodeStringToken(r.destination.new_path.dir.pointee.path)
        let name = decodeStringToken(r.destination.new_path.filename)
        return dir.hasSuffix("/") ? dir + name : dir + "/" + name
    }
}

/// 内存后端：录制调用序列，可手动回放事件，支撑无 root 的完整测试。
public final class MockEs: EsBackend {
    public private(set) var calls: [String] = []
    private var handler: ((EsEvent) -> Void)?
    private var newClientRc: Int32 = 0
    private var subscribeRc: Int32 = 0

    public init() {}

    public static func failingNewClient(_ rc: Int32) -> MockEs {
        let mock = MockEs()
        mock.newClientRc = rc
        return mock
    }

    /// 回放一条事件，驱动已注册 handler。
    public func fire(_ event: EsEvent) {
        guard let handler else { preconditionFailure("fire 前须先 newClient") }
        handler(event)
    }

    public func newClient(_ handler: @escaping (EsEvent) -> Void) -> Result<Void, EsError> {
        calls.append("new_client")
        guard newClientRc == 0 else { return .failure(.newClient(newClientRc)) }
        self.handler = handler
        return .success(())
    }

    public func subscribe() -> Result<Void, EsError> {
        calls.append("subscribe")
        return subscribeRc == 0 ? .success(()) : .failure(.call(op: "es_subscribe", rc: subscribeRc))
    }

    public func deleteClient() -> Result<Void, EsError> {
        calls.append("delete_client")
        self.handler = nil
        return .success(())
    }
}
