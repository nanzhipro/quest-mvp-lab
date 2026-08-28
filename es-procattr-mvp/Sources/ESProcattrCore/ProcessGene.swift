import Foundation

/// 进程唯一标识：(pid, pidversion) 二元组——「一次进程执行」的身份证（对齐 Santa `struct Pid`）。
///
/// pid 会被回收复用，只是运行期句柄；exec 会递增 pidversion。只有 (pid, pidversion)
/// 才能唯一标识「某一次进程执行」，是进程树的标准键（见 Santa `Source/common/processtree/process.h`）。
public struct Pid: Hashable, Comparable {
    public let pid: pid_t
    public let pidversion: UInt64

    public init(pid: pid_t, pidversion: UInt64) {
        self.pid = pid
        self.pidversion = pidversion
    }

    public static func < (lhs: Pid, rhs: Pid) -> Bool {
        lhs.pid == rhs.pid ? lhs.pidversion < rhs.pidversion : lhs.pid < rhs.pid
    }
}

/// 进程基因（ProcessGene）：一次进程执行的可归因身份快照。
///
/// 这是「进程归因」的核心数据结构，把 `es_process_t` 中用于回答
/// 「谁拉起了它、它是谁、它何时开始」的字段提取为自有 Swift 值（不再持有 es_message_t）。
public struct ProcGene: Equatable {
    /// 进程号（audit token 第 5 槽）。
    public var pid: pid_t = 0
    /// 进程执行版本（exec 递增；audit token 第 7 槽）。
    public var pidversion: UInt64 = 0
    /// 可执行文件绝对路径（es_process_t.executable.path，无 FDA 时可能为空）。
    public var path: String = ""
    /// 启动参数 argv（种子经 KERN_PROCARGS2，实时经 exec 后补查；解释型进程的「脚本身份」在此）。
    public var arguments: [String] = []
    /// 代码签名标识（= bundleId，未签名进程为空）。
    public var signingId: String = ""
    /// 开发团队标识（Team ID）。
    public var teamId: String = ""
    /// 是否平台二进制。
    public var isPlatformBinary: Bool = false
    /// 是否 ES client（持有 endpoint-security 权限）。
    public var isEsClient: Bool = false
    /// 代码签名 flags 原始值（十六进制展示，诊断用）。
    public var codesigningFlags: UInt32 = 0
    /// 直接父进程号（es_process_t.ppid）。
    public var ppid: pid_t = 0
    /// 原始父进程号（不随 reparent 改变，es_process_t.original_ppid）。
    public var originalPpid: pid_t = 0
    /// 责任进程号（es_process_t.responsible_audit_token，v≥4）——「谁负责拉起/运行了它」。
    /// 0 表示消息版本不足或责任进程已退出。
    public var responsiblePid: pid_t = 0
    /// 父进程号（es_process_t.parent_audit_token，v≥4）——比 ppid 更权威（含 pidversion）。
    public var parentPid: pid_t = 0
    /// 父进程 pidversion（parent_audit_token 第 7 槽，v≥4）。
    public var parentPidversion: UInt64 = 0
    /// 进程启动时间（fork 时刻，秒；es_process_t.start_time.tv_sec，v≥3）。
    public var startTimeSec: Int64 = 0

    /// 进程唯一标识。
    public var id: Pid { Pid(pid: pid, pidversion: pidversion) }

    /// 用于建树的「有效父进程标识」：优先 parent_audit_token（含 pidversion），退回 ppid（pidversion=0）。
    public var effectiveParentId: Pid {
        if parentPid != 0 {
            return Pid(pid: parentPid, pidversion: parentPidversion)
        }
        return Pid(pid: ppid, pidversion: 0)
    }

    /// 进程链展示用短名（basename of path）。
    public var shortName: String {
        (path as NSString).lastPathComponent
    }
}

/// ES 事件种类（本项目订阅的 NOTIFY 事件子集）。
public enum EsEventKind: String, Equatable, CaseIterable {
    case exec      // 进程执行（新镜像，同 pid 新 pidversion）
    case fork      // 进程 fork（新 pid，同镜像）
    case exit      // 进程退出
    case open      // 打开文件
    case close     // 关闭文件（可能带 modified 标记）
    case create    // 创建文件
    case rename    // 重命名/移动文件
    case unlink    // 删除文件
    case write     // 写入文件
}

/// 一条已从 es_message_t 提取为自有数据的 ES 事件。
///
/// 只读数据，不持有 es_message_t（NOTIFY 事件无应答义务，handler 内同步提取后返回即可，
/// 符合「NOTIFY = 零持有」的内存模式）。
public struct EsEvent: Equatable {
    public var kind: EsEventKind
    /// 产生事件的进程（es_message_t.process）：exec 时为调用 exec 的进程（同 pid 旧镜像），
    /// fork 时为父进程，exit 时为退出进程，文件事件时为执行 I/O 的进程。
    public var actor: ProcGene
    /// exec 的目标进程（新镜像）或 fork 的子进程；其余事件为 nil。
    public var target: ProcGene?
    /// 主要文件路径（open/close/write/unlink 的 target；rename 的 source；create 的 destination）。
    public var path: String = ""
    /// 次要路径（rename 的 destination）。
    public var destPath: String = ""
    /// 退出状态（仅 exit；同 wait(2) 编码）。
    public var exitStatus: Int32 = 0
    /// 关闭时文件是否被修改（仅 close）。
    public var closeModified: Bool = false

    public init(kind: EsEventKind, actor: ProcGene, target: ProcGene? = nil,
                path: String = "", destPath: String = "", exitStatus: Int32 = 0,
                closeModified: Bool = false) {
        self.kind = kind
        self.actor = actor
        self.target = target
        self.path = path
        self.destPath = destPath
        self.exitStatus = exitStatus
        self.closeModified = closeModified
    }
}
