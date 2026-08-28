import Foundation

// libproc + mach 的进程枚举接口（种子阶段用）。libproc 是独立 dylib，需 -lproc 链接
// （见 Package.swift linkerSettings）；mach 接口在 libSystem。这里用 @_silgen_name
// 直接声明 C 符号，避免为几个函数单开 C shim target。
//
// 对齐 Santa `process_tree_macos.mm` 的 Backfill：
//   - pidversion 经 `task_name_for_pid` + `task_info(TASK_AUDIT_TOKEN)` 取得（唯一权威来源）；
//   - 路径经 `proc_pidpath`；父进程经 `proc_pidinfo(PROC_PIDT_SHORTBSDINFO)`。

private let PROC_ALL_PIDS: UInt32 = 1
private let PROC_PIDT_SHORTBSDINFO: Int32 = 13
private let PROC_PIDPATHINFO_MAXSIZE = 4096
private let TASK_AUDIT_TOKEN: UInt32 = 15
private let TASK_AUDIT_TOKEN_COUNT: UInt32 = 8

@_silgen_name("proc_listpids")
private func proc_listpids(_ type: UInt32, _ typeinfo: UInt32,
                           _ buffer: UnsafeMutableRawPointer?, _ buffersize: Int32) -> Int32

@_silgen_name("proc_listchildpids")
private func proc_listchildpids(_ ppid: Int32, _ buffer: UnsafeMutableRawPointer?, _ buffersize: Int32) -> Int32

@_silgen_name("proc_pidinfo")
private func proc_pidinfo(_ pid: Int32, _ flavor: Int32, _ arg: UInt64,
                          _ buffer: UnsafeMutableRawPointer?, _ buffersize: Int32) -> Int32

@_silgen_name("proc_pidpath")
private func proc_pidpath(_ pid: Int32, _ buffer: UnsafeMutableRawPointer?, _ buffersize: UInt32) -> Int32

@_silgen_name("mach_task_self_")
private var mach_task_self_: UInt32

@_silgen_name("task_name_for_pid")
private func task_name_for_pid(_ target: UInt32, _ pid: Int32, _ t: UnsafeMutablePointer<UInt32>) -> Int32

@_silgen_name("task_info")
private func task_info(_ task: UInt32, _ flavor: UInt32, _ info: UnsafeMutableRawPointer?,
                       _ count: UnsafeMutablePointer<UInt32>) -> Int32

@_silgen_name("mach_port_deallocate")
private func mach_port_deallocate(_ task: UInt32, _ name: UInt32) -> Int32

@_silgen_name("sysctl")
private func sysctl(_ name: UnsafeMutablePointer<Int32>, _ namelen: UInt32,
                    _ oldp: UnsafeMutableRawPointer?, _ oldlenp: UnsafeMutablePointer<Int>?,
                    _ newp: UnsafeMutableRawPointer?, _ newlen: Int) -> Int32

private let CTL_KERN: Int32 = 1
private let KERN_PROCARGS2: Int32 = 49

/// 进程枚举器：种子阶段枚举「已运行」进程及其父子关系（对齐 Santa Backfill）。
/// 枚举本用户进程无需 root；跨用户进程路径/task 可能取不到（返回 nil，跳过）。
/// daemon 以 root 运行时可枚举全系统。
public enum ProcEnumerator {
    /// 枚举系统中所有 pid。
    public static func allPids() -> [pid_t] {
        let needed = proc_listpids(PROC_ALL_PIDS, 0, nil, 0)
        guard needed > 0 else { return [] }
        let count = Int(needed) / MemoryLayout<Int32>.size
        var pids = [Int32](repeating: 0, count: count)
        let got = pids.withUnsafeMutableBufferPointer { buf in
            proc_listpids(PROC_ALL_PIDS, 0, buf.baseAddress, Int32(needed))
        }
        guard got > 0 else { return [] }
        return Array(pids.prefix(Int(got) / MemoryLayout<Int32>.size))
    }

    /// 进程可执行文件绝对路径（失败 / 无权限返回 nil）。
    public static func path(of pid: pid_t) -> String? {
        var buf = [UInt8](repeating: 0, count: PROC_PIDPATHINFO_MAXSIZE)
        let len = buf.withUnsafeMutableBytes { raw -> Int32 in
            proc_pidpath(pid, raw.baseAddress, UInt32(raw.count))
        }
        guard len > 0 else { return nil }
        return String(bytes: buf.prefix(Int(len)), encoding: .utf8)
    }

    /// 进程的完整 audit token（含权威 pidversion），经 mach `task_info(TASK_AUDIT_TOKEN)`。
    /// 失败（无权限 / 进程已退）返回 nil。
    public static func auditToken(of pid: pid_t) -> audit_token_t? {
        var task: UInt32 = 0
        guard task_name_for_pid(mach_task_self_, pid, &task) == 0, task != 0 else { return nil }
        defer { _ = mach_port_deallocate(mach_task_self_, task) }
        var token = audit_token_t()
        var count: UInt32 = TASK_AUDIT_TOKEN_COUNT
        let kr = withUnsafeMutablePointer(to: &token) {
            task_info(task, TASK_AUDIT_TOKEN, UnsafeMutableRawPointer($0), &count)
        }
        guard kr == 0 else { return nil }
        return token
    }

    /// 进程的直接父进程号（失败返回 nil）。读 proc_bsdshortinfo.pbsi_ppid（偏移 4）。
    public static func ppid(of pid: pid_t) -> pid_t? {
        var info = proc_bsdshortinfo_t()
        let size = withUnsafeMutablePointer(to: &info) { p -> Int32 in
            proc_pidinfo(pid, PROC_PIDT_SHORTBSDINFO, 0, UnsafeMutableRawPointer(p), Int32(MemoryLayout<proc_bsdshortinfo_t>.size))
        }
        guard size >= 8 else { return nil } // 至少读到 pbsi_ppid
        return pid_t(info.pbsi_ppid)
    }

    /// 进程的直接子进程 pid 列表（无子进程返回空）。
    public static func childPids(of pid: pid_t) -> [pid_t] {
        let needed = proc_listchildpids(pid, nil, 0)
        guard needed > 0 else { return [] }
        let count = Int(needed) / MemoryLayout<Int32>.size
        var pids = [Int32](repeating: 0, count: count)
        let got = pids.withUnsafeMutableBufferPointer { buf in
            proc_listchildpids(pid, buf.baseAddress, Int32(needed))
        }
        guard got > 0 else { return [] }
        return Array(pids.prefix(Int(got) / MemoryLayout<Int32>.size))
    }

    /// 进程的启动参数 argv（经 `KERN_PROCARGS2`，对齐 Santa `ProcessArgumentsForPID`）。
    /// 这是 AI Agent 进程归因的关键：解释型进程（python/node）的「脚本身份」在 argv，
    /// 而非可执行文件路径（后者是解析后的解释器二进制，如 `.../uv/python/.../python3.11`）。
    public static func arguments(of pid: pid_t) -> [String] {
        var mib: [Int32] = [CTL_KERN, KERN_PROCARGS2, pid]
        var size: Int = 0
        guard sysctl(&mib, 3, nil, &size, nil, 0) == 0, size > 4 else { return [] }

        // exec 竞态重试：两次 sysctl 之间进程可能 exec 使字符串区变大，最多重试 3 次。
        var tries = 3
        var actual = size
        var buffer = [UInt8](repeating: 0, count: size + 1)
        repeat {
            buffer = [UInt8](repeating: 0, count: size + 1)
            actual = size
            let rv = buffer.withUnsafeMutableBytes { raw -> Int32 in
                sysctl(&mib, 3, raw.baseAddress, &actual, nil, 0)
            }
            guard rv == 0 else { return [] }
            tries -= 1
        } while actual == size + 1 && tries > 0
        if actual == size + 1 { return [] } // 仍可能竞态，放弃

        return parseProcArgs(buffer)
    }

    /// 解析 KERN_PROCARGS2 返回的缓冲区：`[int argc][exec path \0][argv... \0]`。
    private static func parseProcArgs(_ buf: [UInt8]) -> [String] {
        guard buf.count > 4 else { return [] }
        let argc = Int(buf.withUnsafeBytes { $0.load(fromByteOffset: 0, as: Int32.self) })
        guard argc > 0 else { return [] }

        var result: [String] = []
        var i = 4
        // 跳过可执行文件路径（第一个 \0 前）
        while i < buf.count && buf[i] != 0 { i += 1 }
        // 跳过到 argv 起始（连续 \0 对齐）
        while i < buf.count && buf[i] == 0 { i += 1 }
        // 依次读 argc 个 argv 字符串
        for _ in 0..<argc {
            let start = i
            while i < buf.count && buf[i] != 0 { i += 1 }
            if start < i {
                result.append(String(decoding: buf[start..<i], as: UTF8.self))
            }
            i += 1 // 跳过 \0
        }
        return result
    }
}

/// `proc_bsdshortinfo` 的 Swift 镜像（只用到 pbsi_pid/pbsi_ppid，其余字段占位保证布局）。
/// 布局对齐 `sys/proc_info.h`（全 u32/uid_t/gid_t/char[16]，无填充）。
private struct proc_bsdshortinfo_t {
    var pbsi_pid: UInt32 = 0
    var pbsi_ppid: UInt32 = 0
    var pbsi_pgid: UInt32 = 0
    var pbsi_status: UInt32 = 0
    var pbsi_comm: (Int8, Int8, Int8, Int8, Int8, Int8, Int8, Int8,
                    Int8, Int8, Int8, Int8, Int8, Int8, Int8, Int8) =
        (0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
    var pbsi_flags: UInt32 = 0
    var pbsi_uid: UInt32 = 0
    var pbsi_gid: UInt32 = 0
    var pbsi_ruid: UInt32 = 0
    var pbsi_rgid: UInt32 = 0
    var pbsi_svuid: UInt32 = 0
    var pbsi_svgid: UInt32 = 0
    var pbsi_rfu: UInt32 = 0
}
