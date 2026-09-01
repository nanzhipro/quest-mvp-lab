//! 真实系统调用封装（libproc / mach / sysctl / csops）。
//!
//! 全部 unsafe 集中于此模块与 ffi.rs。实现 [`crate::proctree::ProcSource`]，
//! 供进程树种子回填使用；socket 枚举复用 C shim（proc_info.h 结构复杂，
//! C 侧直接用系统头文件更可靠）。
//!
//! 权限语义：枚举本用户进程无需 root；跨用户进程的 task/路径可能取不到
//! （返回 None，由调用方跳过）；daemon 以 root 运行时可枚举全系统。

use std::ffi::c_void;

use crate::error::SensorError;
use crate::ffi;
use crate::proctree::{AuditInfo, ProcSource};

const PROC_ALL_PIDS: u32 = 1;
const PROC_PIDT_SHORTBSDINFO: i32 = 13;
const PROC_PIDPATHINFO_MAXSIZE: usize = 4096;
const TASK_AUDIT_TOKEN: u32 = 15;
const TASK_AUDIT_TOKEN_COUNT: u32 = 8;
const CTL_KERN: i32 = 1;
const KERN_PROCARGS2: i32 = 49;
const CS_OPS_PIDSTATUS: u32 = 0;

/// `audit_token_t` 镜像（`bsm/audit.h`：8 个 u32）。
/// val[0]=pid val[1]=auid val[2]=euid val[3]=egid val[4]=pidversion? 布局见下。
#[repr(C)]
struct AuditToken {
    val: [u32; 8],
}

/// `proc_bsdshortinfo` 镜像（`sys/proc_info.h`，全 u32/char[16]，无填充）。
/// 只读 pbsi_pid/pbsi_ppid，其余字段占位保证布局。
#[repr(C)]
struct ProcBsdShortInfo {
    pbsi_pid: u32,
    pbsi_ppid: u32,
    pbsi_pgid: u32,
    pbsi_status: u32,
    pbsi_comm: [i8; 16],
    pbsi_flags: u32,
    pbsi_uid: u32,
    pbsi_gid: u32,
    pbsi_ruid: u32,
    pbsi_rgid: u32,
    pbsi_svuid: u32,
    pbsi_svgid: u32,
    pbsi_rfu: u32,
}

unsafe extern "C" {
    fn proc_listpids(ty: u32, typeinfo: u32, buffer: *mut c_void, buffersize: i32) -> i32;
    fn proc_pidinfo(pid: i32, flavor: i32, arg: u64, buffer: *mut c_void, buffersize: i32) -> i32;
    fn proc_pidpath(pid: i32, buffer: *mut c_void, buffersize: u32) -> i32;
    fn task_name_for_pid(target: u32, pid: i32, t: *mut u32) -> i32;
    fn task_info(task: u32, flavor: u32, info: *mut c_void, count: *mut u32) -> i32;
    fn mach_port_deallocate(task: u32, name: u32) -> i32;
    fn mach_timebase_info(info: *mut MachTimebaseInfo) -> i32;
    fn mach_absolute_time() -> u64;
    fn geteuid() -> u32;
    fn sysctl(
        name: *mut i32,
        namelen: u32,
        oldp: *mut c_void,
        oldlenp: *mut usize,
        newp: *mut c_void,
        newlen: usize,
    ) -> i32;
    fn csops(pid: i32, ops: u32, useraddr: *mut c_void, usersize: usize) -> i32;
    static mach_task_self_: u32;
}

#[repr(C)]
struct MachTimebaseInfo {
    numer: u32,
    denom: u32,
}

/// 当前进程 effective uid（root 检查用）。
pub fn effective_uid() -> u32 {
    // SAFETY: geteuid 无参数无副作用，总是安全。
    unsafe { geteuid() }
}

/// mach absolute time 当前值。
pub fn mach_absolute_now() -> u64 {
    // SAFETY: mach_absolute_time 无参数无副作用。
    unsafe { mach_absolute_time() }
}

/// 秒数 → mach_time 刻度（mach_timebase 换算，供延迟回收窗口使用）。
pub fn ticks_for_seconds(secs: u64) -> u64 {
    let mut info = MachTimebaseInfo { numer: 0, denom: 1 };
    // SAFETY: info 为有效的 mach_timebase_info_data_t 缓冲。
    let kr = unsafe { mach_timebase_info(&mut info) };
    if kr != 0 || info.denom == 0 {
        return secs * 1_000_000_000; // 兜底按 1:1 ns
    }
    secs * 1_000_000_000 * u64::from(info.denom) / u64::from(info.numer)
}

/// 真实系统调用源。
pub struct RealProcSource;

impl RealProcSource {
    pub fn new() -> Self {
        Self
    }

    /// 枚举指定 pid 的 INET/INET6 socket 快照（经 C shim 安全包装）。
    pub fn list_sockets(pid: i32) -> Result<Vec<ffi::EsshSocket>, SensorError> {
        ffi::list_sockets(pid).map_err(|rc| SensorError::EsCall {
            op: "essh_list_sockets",
            rc,
        })
    }
}

impl Default for RealProcSource {
    fn default() -> Self {
        Self::new()
    }
}

impl ProcSource for RealProcSource {
    fn all_pids(&self) -> Vec<i32> {
        // SAFETY: 第一次空调用取所需缓冲大小。
        let needed = unsafe { proc_listpids(PROC_ALL_PIDS, 0, std::ptr::null_mut(), 0) };
        if needed <= 0 {
            return Vec::new();
        }
        let count = needed as usize / std::mem::size_of::<i32>();
        let mut pids = vec![0i32; count];
        // SAFETY: pids 缓冲容量与传入大小一致。
        let got = unsafe { proc_listpids(PROC_ALL_PIDS, 0, pids.as_mut_ptr().cast(), needed) };
        if got <= 0 {
            return Vec::new();
        }
        pids.truncate(got as usize / std::mem::size_of::<i32>());
        pids
    }

    fn ppid(&self, pid: i32) -> Option<i32> {
        let mut info = ProcBsdShortInfo {
            pbsi_pid: 0,
            pbsi_ppid: 0,
            pbsi_pgid: 0,
            pbsi_status: 0,
            pbsi_comm: [0; 16],
            pbsi_flags: 0,
            pbsi_uid: 0,
            pbsi_gid: 0,
            pbsi_ruid: 0,
            pbsi_rgid: 0,
            pbsi_svuid: 0,
            pbsi_svgid: 0,
            pbsi_rfu: 0,
        };
        // SAFETY: info 布局与 proc_bsdshortinfo 一致，大小匹配。
        let size = unsafe {
            proc_pidinfo(
                pid,
                PROC_PIDT_SHORTBSDINFO,
                0,
                (&mut info as *mut ProcBsdShortInfo).cast(),
                std::mem::size_of::<ProcBsdShortInfo>() as i32,
            )
        };
        if size < 8 {
            return None; // 至少读到 pbsi_ppid
        }
        Some(info.pbsi_ppid as i32)
    }

    fn audit(&self, pid: i32) -> Option<AuditInfo> {
        let mut task: u32 = 0;
        // SAFETY: mach_task_self_ 为 libSystem 导出的有效 task port。
        let kr = unsafe { task_name_for_pid(mach_task_self_, pid, &mut task) };
        if kr != 0 || task == 0 {
            return None;
        }
        let mut token = AuditToken { val: [0; 8] };
        let mut count: u32 = TASK_AUDIT_TOKEN_COUNT;
        // SAFETY: task 为有效 task port；token 布局与 audit_token_t 一致（8×u32）。
        let kr = unsafe {
            task_info(
                task,
                TASK_AUDIT_TOKEN,
                (&mut token as *mut AuditToken).cast(),
                &mut count,
            )
        };
        // SAFETY: task port 由 task_name_for_pid 授予，配对释放。
        unsafe { mach_port_deallocate(mach_task_self_, task) };
        if kr != 0 {
            return None;
        }
        let v = &token.val;
        // audit_token_t 布局（bsm/audit.h，实测探针验证）：
        // val[0]=auid val[1]=euid val[2]=egid val[3]=ruid val[4]=rgid
        // val[5]=pid val[6]=asid val[7]=pidversion
        Some(AuditInfo {
            pidversion: u64::from(v[7]),
            auid: v[0],
            euid: v[1],
            egid: v[2],
            ruid: v[3],
            rgid: v[4],
        })
    }

    fn path(&self, pid: i32) -> Option<String> {
        let mut buf = vec![0u8; PROC_PIDPATHINFO_MAXSIZE];
        // SAFETY: buf 容量与传入大小一致。
        let len = unsafe { proc_pidpath(pid, buf.as_mut_ptr().cast(), buf.len() as u32) };
        if len <= 0 {
            return None;
        }
        Some(String::from_utf8_lossy(&buf[..len as usize]).into_owned())
    }

    fn argv(&self, pid: i32) -> Vec<String> {
        kern_procargs2(pid).map_or_else(Vec::new, |buf| parse_procargs(&buf))
    }

    fn cs_flags(&self, pid: i32) -> Option<u32> {
        let mut flags: u32 = 0;
        // SAFETY: flags 为有效 u32 缓冲，CS_OPS_PIDSTATUS 读取签名标志。
        let rc = unsafe {
            csops(
                pid,
                CS_OPS_PIDSTATUS,
                (&mut flags as *mut u32).cast(),
                std::mem::size_of::<u32>(),
            )
        };
        if rc != 0 {
            None
        } else {
            Some(flags)
        }
    }
}

/// sysctl(KERN_PROCARGS2) 取原始缓冲区；exec 竞态重试最多 3 次。
fn kern_procargs2(pid: i32) -> Option<Vec<u8>> {
    let mut mib: [i32; 3] = [CTL_KERN, KERN_PROCARGS2, pid];
    let mut size: usize = 0;
    // SAFETY: 第一次空调用取所需大小。
    let rc = unsafe {
        sysctl(
            mib.as_mut_ptr(),
            3,
            std::ptr::null_mut(),
            &mut size,
            std::ptr::null_mut(),
            0,
        )
    };
    if rc != 0 || size <= 4 {
        return None;
    }
    let mut tries = 3;
    loop {
        let mut buffer = vec![0u8; size + 1];
        let mut actual = size;
        // SAFETY: buffer 容量 size+1 ≥ 传入的 actual=size。
        let rc = unsafe {
            sysctl(
                mib.as_mut_ptr(),
                3,
                buffer.as_mut_ptr().cast(),
                &mut actual,
                std::ptr::null_mut(),
                0,
            )
        };
        if rc != 0 {
            return None;
        }
        // actual == size + 1 表示两次调用之间进程 exec 使字符串区变大，重试。
        if actual != size + 1 || tries == 0 {
            buffer.truncate(actual.min(size + 1));
            return Some(buffer);
        }
        tries -= 1;
    }
}

/// 解析 KERN_PROCARGS2 缓冲区：`[int argc][exec path \0][argv... \0]`。
fn parse_procargs(buf: &[u8]) -> Vec<String> {
    if buf.len() <= 4 {
        return Vec::new();
    }
    let argc = i32::from_ne_bytes([buf[0], buf[1], buf[2], buf[3]]);
    if argc <= 0 {
        return Vec::new();
    }
    let mut result = Vec::with_capacity(argc as usize);
    let mut i = 4;
    // 跳过可执行文件路径（第一个 \0 前）
    while i < buf.len() && buf[i] != 0 {
        i += 1;
    }
    // 跳过到 argv 起始（连续 \0 对齐）
    while i < buf.len() && buf[i] == 0 {
        i += 1;
    }
    // 依次读 argc 个 argv 字符串
    for _ in 0..argc {
        let start = i;
        while i < buf.len() && buf[i] != 0 {
            i += 1;
        }
        if start < i {
            result.push(String::from_utf8_lossy(&buf[start..i]).into_owned());
        }
        i += 1; // 跳过 \0
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parse_procargs_buffer() {
        // [argc=3]["/usr/bin/python3"\0]["python3"\0"-m"\0"tui.entry"\0]
        let mut buf = Vec::new();
        buf.extend_from_slice(&3i32.to_ne_bytes());
        buf.extend_from_slice(b"/usr/bin/python3\0");
        buf.extend_from_slice(b"python3\0-m\0tui.entry\0");
        assert_eq!(parse_procargs(&buf), vec!["python3", "-m", "tui.entry"]);
    }

    #[test]
    fn parse_procargs_handles_padding_and_short_buffers() {
        assert!(parse_procargs(&[]).is_empty());
        assert!(parse_procargs(&[1, 0]).is_empty());
        // exec_path 后有连续 \0 对齐
        let mut buf = Vec::new();
        buf.extend_from_slice(&1i32.to_ne_bytes());
        buf.extend_from_slice(b"/bin/sh\0\0\0");
        buf.extend_from_slice(b"sh\0");
        assert_eq!(parse_procargs(&buf), vec!["sh"]);
        // argc=0 / 负值
        let buf = 0i32.to_ne_bytes().to_vec();
        assert!(parse_procargs(&buf).is_empty());
    }

    /// 本进程查询不需要 root，作为 smoke test。
    #[test]
    fn self_process_queries() {
        let src = RealProcSource::new();
        let pid = std::process::id() as i32;
        assert!(src.path(pid).is_some());
        assert!(src.ppid(pid).is_some());
        let audit = src.audit(pid).expect("本进程 audit token 应可取");
        assert!(src.all_pids().contains(&pid));
        assert!(!src.argv(pid).is_empty());
        // 数值正确性：ruid 应等于 getuid()；pidversion 为正整数
        // （曾与 ruid/rgid 字段错位导致种子树 pidversion 全错）。
        assert_eq!(audit.ruid, unsafe { libc::getuid() });
        assert_eq!(audit.euid, unsafe { libc::geteuid() });
        assert!(audit.pidversion > 0);
        assert!(audit.pidversion < 100_000_000); // 量级合理（非 uid/gid 错填）
    }

    #[test]
    fn invalid_pid_queries_return_none() {
        let src = RealProcSource::new();
        assert!(src.path(-1).is_none());
        assert!(src.ppid(-1).is_none());
        assert!(src.audit(-1).is_none());
        assert!(src.argv(-1).is_empty());
        assert!(src.cs_flags(-1).is_none());
    }

    #[test]
    fn cs_flags_and_socket_listing_on_self() {
        let src = RealProcSource::new();
        let pid = std::process::id() as i32;
        // csops PIDSTATUS 对本进程应成功（即使 adhoc 签名也返回标志）。
        assert!(src.cs_flags(pid).is_some());
        // socket 枚举对本进程应成功（无 socket 时为空列表）。
        assert!(RealProcSource::list_sockets(pid).is_ok());
        // 非法 pid → EsCall 错误。
        assert!(RealProcSource::list_sockets(-1).is_err());
    }

    #[test]
    fn misc_system_helpers() {
        assert_eq!(effective_uid(), unsafe { libc::geteuid() });
        assert!(mach_absolute_now() > 0);
        assert!(ticks_for_seconds(5) > 0);
        // 不存在的 pid：KERN_PROCARGS2 失败 → 空 argv。
        assert!(kern_procargs2(-1).is_none());
    }
}
