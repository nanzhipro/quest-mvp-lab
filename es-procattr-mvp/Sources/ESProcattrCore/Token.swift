import Foundation

/// `audit_token_t` 的安全封装：进程内核身份 (pid, pidversion)。
///
/// `audit_token_t` 是 `struct { unsigned int val[8]; }`（见 `mach/message.h`），
/// 各槽位语义固定（xnu 审计令牌布局）：
///   val[0]=auid  val[1]=euid  val[2]=egid  val[3]=ruid
///   val[4]=rgid  val[5]=pid   val[6]=asid  val[7]=pidversion
///
/// pid 会被回收复用，只是运行期句柄；(pid, pidversion) 才是「一次进程执行」的唯一标识
/// （exec 会递增 pidversion）。权威身份是完整 8 元组 audit_token，跨进程操作应优先走
/// audit_token API（proc_pidpath_audittoken / proc_signal_with_audittoken），本 MVP 只读
/// pid/pidversion 用于进程树键与归因。
public struct Token: Equatable {
    public let raw: audit_token_t

    public init(_ raw: audit_token_t) {
        self.raw = raw
    }

    /// 进程号（audit token 第 5 槽）。
    public var pid: pid_t {
        pid_t(raw.val.5)
    }

    /// 进程执行版本（audit token 第 7 槽），exec 时递增。
    public var pidversion: UInt64 {
        UInt64(raw.val.7)
    }

    /// 登录审计用户 id（第 0 槽）。
    public var auid: UInt32 { raw.val.0 }
    /// 有效用户 id（第 1 槽）。
    public var euid: UInt32 { raw.val.1 }
}
