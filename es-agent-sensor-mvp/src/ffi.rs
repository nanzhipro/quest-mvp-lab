//! libEndpointSecurity 垫片的 FFI 声明。
//!
//! 事件数据全部经 `csrc/es_shim.c` 提取为扁平 C 结构体，此处做 `#[repr(C)]` 镜像。
//! 镜像字段必须与 `csrc/es_shim.h` 逐一对应（类型、顺序、数组容量），
//! 由 `#[cfg(test)]` 中的布局断言守护。

use std::ffi::{c_char, c_int, c_void};

/// 路径缓冲容量，与 `ESSH_PATH_CAP` 一致。
pub const PATH_CAP: usize = 4096;
/// 短 token（signing_id / team_id）缓冲容量，与 `ESSH_TOKEN_CAP` 一致。
pub const TOKEN_CAP: usize = 256;
/// argv 最大条数，与 `ESSH_ARGV_MAX` 一致。
pub const ARGV_MAX: usize = 64;
/// argv 拼接缓冲容量，与 `ESSH_ARGV_BUFSIZE` 一致。
pub const ARGV_BUFSIZE: usize = 8192;
/// IP 地址字符串缓冲容量，与 `ESSH_ADDR_CAP` 一致。
pub const ADDR_CAP: usize = 64;

/// 默认订阅集事件位（essh_subscribe 的 event_mask，位序见 es_shim.h）。
pub const EV_EXEC: u32 = 1 << 0;
pub const EV_FORK: u32 = 1 << 1;
pub const EV_EXIT: u32 = 1 << 2;
pub const EV_CREATE: u32 = 1 << 3;
pub const EV_OPEN: u32 = 1 << 4;
pub const EV_CLOSE: u32 = 1 << 5;
pub const EV_RENAME: u32 = 1 << 6;
pub const EV_UNLINK: u32 = 1 << 7;
pub const EV_LINK: u32 = 1 << 8;
pub const EV_CLONE: u32 = 1 << 9;
pub const EV_SETATTRLIST: u32 = 1 << 10;
pub const EV_TRUNCATE: u32 = 1 << 11;
pub const EV_UIPC_BIND: u32 = 1 << 12;
pub const EV_UIPC_CONNECT: u32 = 1 << 13;
/// 默认订阅集：14 个 NOTIFY 事件（macOS ES 无 RMDIR 事件，rmdir 归并到 UNLINK）。
pub const EV_DEFAULT_MASK: u32 = (1 << 14) - 1;

/// 额外订阅事件位（essh_subscribe 的 extra_mask）。
pub const EVX_WRITE: u32 = 1 << 0;
pub const EVX_READDIR: u32 = 1 << 1;
pub const EVX_SIGNAL: u32 = 1 << 2;

/// ES 事件类型数值（`es_event_type_t` 枚举序，取自 SDK ESTypes.h 的稳定 ABI 顺序）。
pub mod es_type {
    pub const NOTIFY_EXEC: u32 = 9;
    pub const NOTIFY_OPEN: u32 = 10;
    pub const NOTIFY_FORK: u32 = 11;
    pub const NOTIFY_CLOSE: u32 = 12;
    pub const NOTIFY_CREATE: u32 = 13;
    pub const NOTIFY_EXIT: u32 = 15;
    pub const NOTIFY_LINK: u32 = 19;
    pub const NOTIFY_RENAME: u32 = 25;
    pub const NOTIFY_SETATTRLIST: u32 = 26;
    pub const NOTIFY_SIGNAL: u32 = 31;
    pub const NOTIFY_UNLINK: u32 = 32;
    pub const NOTIFY_WRITE: u32 = 33;
    pub const NOTIFY_TRUNCATE: u32 = 41;
    pub const NOTIFY_CLONE: u32 = 63;
    pub const NOTIFY_READDIR: u32 = 70;
    pub const NOTIFY_UIPC_BIND: u32 = 78;
    pub const NOTIFY_UIPC_CONNECT: u32 = 80;

    /// 事件号 → 枚举名字符串（schema `es_event.type`）。
    pub fn name(id: u32) -> &'static str {
        match id {
            NOTIFY_EXEC => "ES_EVENT_TYPE_NOTIFY_EXEC",
            NOTIFY_OPEN => "ES_EVENT_TYPE_NOTIFY_OPEN",
            NOTIFY_FORK => "ES_EVENT_TYPE_NOTIFY_FORK",
            NOTIFY_CLOSE => "ES_EVENT_TYPE_NOTIFY_CLOSE",
            NOTIFY_CREATE => "ES_EVENT_TYPE_NOTIFY_CREATE",
            NOTIFY_EXIT => "ES_EVENT_TYPE_NOTIFY_EXIT",
            NOTIFY_LINK => "ES_EVENT_TYPE_NOTIFY_LINK",
            NOTIFY_RENAME => "ES_EVENT_TYPE_NOTIFY_RENAME",
            NOTIFY_SETATTRLIST => "ES_EVENT_TYPE_NOTIFY_SETATTRLIST",
            NOTIFY_SIGNAL => "ES_EVENT_TYPE_NOTIFY_SIGNAL",
            NOTIFY_UNLINK => "ES_EVENT_TYPE_NOTIFY_UNLINK",
            NOTIFY_WRITE => "ES_EVENT_TYPE_NOTIFY_WRITE",
            NOTIFY_TRUNCATE => "ES_EVENT_TYPE_NOTIFY_TRUNCATE",
            NOTIFY_CLONE => "ES_EVENT_TYPE_NOTIFY_CLONE",
            NOTIFY_READDIR => "ES_EVENT_TYPE_NOTIFY_READDIR",
            NOTIFY_UIPC_BIND => "ES_EVENT_TYPE_NOTIFY_UIPC_BIND",
            NOTIFY_UIPC_CONNECT => "ES_EVENT_TYPE_NOTIFY_UIPC_CONNECT",
            _ => "ES_EVENT_TYPE_UNKNOWN",
        }
    }
}

/// 不透明 client 句柄（对应 shim 的 `essh_client_t`）。
pub enum EsshClient {}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshFile {
    pub ino: u64,
    pub size: i64,
    pub mtime_sec: i64,
    pub mode: u32,
    pub uid: u32,
    pub gid: u32,
    pub path_truncated: u8,
    pub path_len: u32,
    pub path: [u8; PATH_CAP],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshProcess {
    pub pid: i32,
    pub pidversion: u32,
    pub ppid: i32,
    pub original_ppid: i32,
    pub responsible_pid: i32,
    pub ruid: u32,
    pub euid: u32,
    pub rgid: u32,
    pub egid: u32,
    pub auid: u32,
    pub codesigning_flags: u32,
    pub is_platform_binary: u8,
    pub is_es_client: u8,
    pub start_time_sec: i64,
    pub cdhash: [u8; 20],
    pub executable_len: u32,
    pub executable: [u8; PATH_CAP],
    pub signing_id_len: u32,
    pub signing_id: [u8; TOKEN_CAP],
    pub team_id_len: u32,
    pub team_id: [u8; TOKEN_CAP],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvExec {
    pub target: EsshProcess,
    pub argc: u32,
    pub argv_offsets: [u32; ARGV_MAX],
    pub argv_lens: [u16; ARGV_MAX],
    pub argv_buf: [u8; ARGV_BUFSIZE],
    pub has_script: u8,
    pub script: EsshFile,
    pub cwd_len: u32,
    pub cwd: [u8; PATH_CAP],
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvFork {
    pub child: EsshProcess,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvExit {
    pub stat: i32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvCreate {
    pub file: EsshFile,
    pub mode: u32,
    pub created_new: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvOpen {
    pub file: EsshFile,
    pub fflag: i32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvClose {
    pub file: EsshFile,
    pub modified: u8,
    pub was_mapped_writable: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvRename {
    pub src: EsshFile,
    pub dst: EsshFile,
    pub dst_existed: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvUnlink {
    pub target: EsshFile,
    pub parent_dir: EsshFile,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvLink {
    pub src: EsshFile,
    pub dst: EsshFile,
    pub is_dir: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvClone {
    pub src: EsshFile,
    pub dst: EsshFile,
    pub is_dir: u8,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvSetattrlist {
    pub target: EsshFile,
    pub commonattr: u32,
    pub volattr: u32,
    pub dirattr: u32,
    pub fileattr: u32,
    pub forkattr: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvTruncate {
    pub target: EsshFile,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvUipcBind {
    pub sock: EsshFile,
    pub mode: u32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvUipcConnect {
    pub file: EsshFile,
    pub domain: i32,
    pub sock_type: i32,
    pub protocol: i32,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvWrite {
    pub target: EsshFile,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvReaddir {
    pub dir: EsshFile,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshEvSignal {
    pub sig: i32,
    pub target: EsshProcess,
}

/// 事件负载 union，判别标签为 [`EsshMessage::event_type`]。
#[repr(C)]
#[derive(Clone, Copy)]
pub union EsshEvent {
    pub exec: EsshEvExec,
    pub fork: EsshEvFork,
    pub exit: EsshEvExit,
    pub create: EsshEvCreate,
    pub open: EsshEvOpen,
    pub close: EsshEvClose,
    pub rename: EsshEvRename,
    pub unlink: EsshEvUnlink,
    pub link: EsshEvLink,
    pub clone: EsshEvClone,
    pub setattrlist: EsshEvSetattrlist,
    pub truncate: EsshEvTruncate,
    pub uipc_bind: EsshEvUipcBind,
    pub uipc_connect: EsshEvUipcConnect,
    pub write: EsshEvWrite,
    pub readdir: EsshEvReaddir,
    pub signal: EsshEvSignal,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshMessage {
    pub mach_time: u64,
    pub thread_id: u64,
    pub time_sec: i64,
    pub time_nsec: i64,
    pub seq_num: u64,
    pub global_seq_num: u64,
    pub event_type: u32,
    pub message_version: u32,
    pub process: EsshProcess,
    pub event: EsshEvent,
}

#[repr(C)]
#[derive(Clone, Copy)]
pub struct EsshSocket {
    pub family: i32,
    pub sock_type: i32,
    pub protocol: i32,
    pub state: i32,
    pub lport: u32,
    pub fport: u32,
    pub laddr: [u8; ADDR_CAP],
    pub faddr: [u8; ADDR_CAP],
}

/// 事件回调签名，与 shim 的 `essh_cb` 一一对应。
/// `msg` 仅在回调期间有效，接收方必须整体拷贝。
pub type EsshCallback = extern "C" fn(ctx: *mut c_void, msg: *const EsshMessage);

unsafe extern "C" {
    pub fn essh_client_new(out: *mut *mut EsshClient, cb: EsshCallback, ctx: *mut c_void) -> c_int;
    pub fn essh_subscribe(client: *mut EsshClient, event_mask: u32, extra_mask: u32) -> c_int;
    pub fn essh_mute_path_prefix(client: *mut EsshClient, path: *const c_char) -> c_int;
    pub fn essh_client_delete(client: *mut EsshClient);
    pub fn essh_list_sockets(pid: i32, out: *mut *mut EsshSocket, count: *mut usize) -> c_int;
    pub fn essh_free_sockets(s: *mut EsshSocket, count: usize);
}

impl EsshFile {
    /// path 按 data+length 切片（不依赖 NUL 结尾）。
    pub fn path_str(&self) -> &str {
        let n = (self.path_len as usize).min(self.path.len());
        std::str::from_utf8(&self.path[..n]).unwrap_or("")
    }
}

impl EsshProcess {
    pub fn executable_str(&self) -> &str {
        let n = (self.executable_len as usize).min(self.executable.len());
        std::str::from_utf8(&self.executable[..n]).unwrap_or("")
    }

    pub fn signing_id_str(&self) -> Option<&str> {
        let n = (self.signing_id_len as usize).min(self.signing_id.len());
        if n == 0 {
            return None;
        }
        std::str::from_utf8(&self.signing_id[..n]).ok()
    }

    pub fn team_id_str(&self) -> Option<&str> {
        let n = (self.team_id_len as usize).min(self.team_id.len());
        if n == 0 {
            return None;
        }
        std::str::from_utf8(&self.team_id[..n]).ok()
    }

    /// cdhash 小写 hex（全零视为无签名，返回 None）。
    pub fn cdhash_hex(&self) -> Option<String> {
        if self.cdhash.iter().all(|&b| b == 0) {
            return None;
        }
        Some(self.cdhash.iter().map(|b| format!("{b:02x}")).collect())
    }
}

impl EsshEvExec {
    /// 迭代提取 exec argv（按 offset+len 切片，不依赖 NUL）。
    pub fn argv(&self) -> Vec<String> {
        let n = (self.argc as usize).min(ARGV_MAX);
        let mut out = Vec::with_capacity(n);
        for i in 0..n {
            let off = self.argv_offsets[i] as usize;
            let len = self.argv_lens[i] as usize;
            if off + len > self.argv_buf.len() {
                break;
            }
            out.push(String::from_utf8_lossy(&self.argv_buf[off..off + len]).into_owned());
        }
        out
    }

    pub fn cwd_str(&self) -> Option<&str> {
        let n = (self.cwd_len as usize).min(self.cwd.len());
        if n == 0 {
            return None;
        }
        std::str::from_utf8(&self.cwd[..n]).ok()
    }
}

// ---- 安全包装层：全部 unsafe 收敛于本模块，上层（backend/decode/sysproc）不再出现 unsafe ----

macro_rules! variant_accessor {
    ($name:ident, $ty:ty, $tag:expr) => {
        variant_accessor!($name, $ty, $tag, $name);
    };
    ($name:ident, $ty:ty, $tag:expr, $field:ident) => {
        /// 按 event_type 标签访问 union 变体；标签不匹配返回 None。
        pub fn $name(&self) -> Option<&$ty> {
            if self.event_type == $tag {
                // SAFETY: union 判别标签 event_type 由 C shim 与消息一同填充，
                // 标签匹配时读取的变体即 C 侧写入的变体。
                Some(unsafe { &self.event.$field })
            } else {
                None
            }
        }
    };
}

impl EsshMessage {
    variant_accessor!(exec, EsshEvExec, es_type::NOTIFY_EXEC);
    variant_accessor!(fork, EsshEvFork, es_type::NOTIFY_FORK);
    variant_accessor!(exit, EsshEvExit, es_type::NOTIFY_EXIT);
    variant_accessor!(create, EsshEvCreate, es_type::NOTIFY_CREATE);
    variant_accessor!(open, EsshEvOpen, es_type::NOTIFY_OPEN);
    variant_accessor!(close, EsshEvClose, es_type::NOTIFY_CLOSE);
    variant_accessor!(rename, EsshEvRename, es_type::NOTIFY_RENAME);
    variant_accessor!(unlink, EsshEvUnlink, es_type::NOTIFY_UNLINK);
    variant_accessor!(link, EsshEvLink, es_type::NOTIFY_LINK);
    variant_accessor!(clone_ev, EsshEvClone, es_type::NOTIFY_CLONE, clone);
    variant_accessor!(setattrlist, EsshEvSetattrlist, es_type::NOTIFY_SETATTRLIST);
    variant_accessor!(truncate, EsshEvTruncate, es_type::NOTIFY_TRUNCATE);
    variant_accessor!(uipc_bind, EsshEvUipcBind, es_type::NOTIFY_UIPC_BIND);
    variant_accessor!(
        uipc_connect,
        EsshEvUipcConnect,
        es_type::NOTIFY_UIPC_CONNECT
    );
    variant_accessor!(write, EsshEvWrite, es_type::NOTIFY_WRITE);
    variant_accessor!(readdir, EsshEvReaddir, es_type::NOTIFY_READDIR);
    variant_accessor!(signal, EsshEvSignal, es_type::NOTIFY_SIGNAL);
}

use std::ffi::CStr;
use std::ptr;
use std::sync::Arc;

/// 共享事件处理器（堆驻留，生命周期与 client 一致）。
pub type SharedHandler = Arc<dyn Fn(&EsshMessage) + Send + 'static>;

extern "C" fn trampoline(ctx: *mut c_void, msg: *const EsshMessage) {
    // SAFETY: ctx 由 Client::new 设置为 SharedHandler 的堆分配副本，
    // 生命周期覆盖 client 存续期（Drop 中先删 client 后回收 ctx）。
    let handler = unsafe { &*(ctx as *const SharedHandler) };
    // SAFETY: msg 由 C shim 在回调期间提供且布局与 EsshMessage 一致；
    // ptr::read 整体拷贝，回调返回后不再触碰原指针。
    let owned: EsshMessage = unsafe { ptr::read(msg) };
    handler(&owned);
}

/// ES client 句柄：拥有 shim client 与 handler 驻留块，Drop 配对回收。
pub struct Client {
    ptr: *mut EsshClient,
    ctx: *mut c_void,
}

// ES API 可在任意线程调用（官方用法）；client 指针只经由 shim 使用，
// Rust 侧无数据竞争。
unsafe impl Send for Client {}
unsafe impl Sync for Client {}

impl Client {
    /// 创建 client 并注册 handler。失败返回 `es_new_client_result_t`。
    pub fn new(handler: SharedHandler) -> Result<Self, i32> {
        let ctx = Box::into_raw(Box::new(handler)).cast::<c_void>();
        let mut ptr: *mut EsshClient = ptr::null_mut();
        // SAFETY: out/cb/ctx 均有效；失败时回收 ctx。
        let rc = unsafe { essh_client_new(&mut ptr, trampoline, ctx) };
        if rc != 0 {
            // SAFETY: ctx 由上面 Box::into_raw 产生，此处取回所有权。
            unsafe { drop(Box::from_raw(ctx.cast::<SharedHandler>())) };
            return Err(rc);
        }
        Ok(Self { ptr, ctx })
    }

    /// 订阅事件（mask 位序见 es_shim.h 与本模块常量）。返回 `es_return_t`。
    pub fn subscribe(&self, event_mask: u32, extra_mask: u32) -> i32 {
        // SAFETY: client 已由 new 初始化且存活。
        unsafe { essh_subscribe(self.ptr, event_mask, extra_mask) }
    }

    /// 内核级 target path 前缀静音。返回 `es_return_t`。
    pub fn mute_path_prefix(&self, path: &CStr) -> i32 {
        // SAFETY: path 为有效 NUL 结尾字符串，调用期间存活。
        unsafe { essh_mute_path_prefix(self.ptr, path.as_ptr()) }
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        if !self.ptr.is_null() {
            // SAFETY: ptr 由 shim 分配，此处归还；此后回调不再触发。
            unsafe { essh_client_delete(self.ptr) };
        }
        if !self.ctx.is_null() {
            // SAFETY: client 已删除，handler 不再被调用，回收驻留块。
            unsafe { drop(Box::from_raw(self.ctx.cast::<SharedHandler>())) };
        }
    }
}

/// 枚举指定 pid 的 INET/INET6 socket 快照（net-poller 用）。
/// 失败返回 shim 返回码（通常为 -1）。
pub fn list_sockets(pid: i32) -> Result<Vec<EsshSocket>, i32> {
    let mut out: *mut EsshSocket = ptr::null_mut();
    let mut count: usize = 0;
    // SAFETY: out/count 为有效指针；成功时 out 为 shim malloc 的数组。
    let rc = unsafe { essh_list_sockets(pid, &mut out, &mut count) };
    if rc != 0 {
        return Err(rc);
    }
    if out.is_null() || count == 0 {
        return Ok(Vec::new());
    }
    // SAFETY: out 指向 count 个连续 EsshSocket，拷贝后立即经 shim 释放。
    let sockets = unsafe { std::slice::from_raw_parts(out, count) }.to_vec();
    // SAFETY: out 由 essh_list_sockets 分配，配对释放。
    unsafe { essh_free_sockets(out, count) };
    Ok(sockets)
}

impl EsshSocket {
    pub fn laddr_str(&self) -> &str {
        nul_terminated(&self.laddr)
    }

    pub fn faddr_str(&self) -> &str {
        nul_terminated(&self.faddr)
    }
}

fn nul_terminated(buf: &[u8]) -> &str {
    let end = buf.iter().position(|&b| b == 0).unwrap_or(buf.len());
    std::str::from_utf8(&buf[..end]).unwrap_or("")
}

#[cfg(test)]
pub mod testutil {
    //! 测试构造辅助：unsafe 集中在 ffi.rs，其他模块的测试经此构造样例。
    use super::*;

    pub fn zeroed_message() -> EsshMessage {
        // SAFETY: 全零是这些 POD 结构的有效测试初值，由测试逐字段填充。
        unsafe { std::mem::zeroed() }
    }

    pub fn zeroed_process() -> EsshProcess {
        // SAFETY: 同上。
        unsafe { std::mem::zeroed() }
    }

    pub fn zeroed_file() -> EsshFile {
        // SAFETY: 同上。
        unsafe { std::mem::zeroed() }
    }

    pub fn zeroed_exec() -> EsshEvExec {
        // SAFETY: 同上。
        unsafe { std::mem::zeroed() }
    }

    pub fn zeroed_socket() -> EsshSocket {
        // SAFETY: 同上。
        unsafe { std::mem::zeroed() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::mem::{align_of, size_of};

    /// 事件号钉桩：数值取自本机 SDK（macOS 26）`ESTypes.h` 的枚举序，
    /// 曾与旧 SDK 值漂移导致 CLONE/UIPC 事件被静默丢弃/误分类。
    #[test]
    fn event_type_values_pinned() {
        assert_eq!(es_type::NOTIFY_EXEC, 9);
        assert_eq!(es_type::NOTIFY_OPEN, 10);
        assert_eq!(es_type::NOTIFY_FORK, 11);
        assert_eq!(es_type::NOTIFY_CLOSE, 12);
        assert_eq!(es_type::NOTIFY_CREATE, 13);
        assert_eq!(es_type::NOTIFY_EXIT, 15);
        assert_eq!(es_type::NOTIFY_LINK, 19);
        assert_eq!(es_type::NOTIFY_RENAME, 25);
        assert_eq!(es_type::NOTIFY_SETATTRLIST, 26);
        assert_eq!(es_type::NOTIFY_SIGNAL, 31);
        assert_eq!(es_type::NOTIFY_UNLINK, 32);
        assert_eq!(es_type::NOTIFY_WRITE, 33);
        assert_eq!(es_type::NOTIFY_TRUNCATE, 41);
        assert_eq!(es_type::NOTIFY_CLONE, 63);
        assert_eq!(es_type::NOTIFY_READDIR, 70);
        assert_eq!(es_type::NOTIFY_UIPC_BIND, 78);
        assert_eq!(es_type::NOTIFY_UIPC_CONNECT, 80);
        // name() 必须与常量一致
        assert_eq!(es_type::name(63), "ES_EVENT_TYPE_NOTIFY_CLONE");
        assert_eq!(es_type::name(78), "ES_EVENT_TYPE_NOTIFY_UIPC_BIND");
        assert_eq!(es_type::name(80), "ES_EVENT_TYPE_NOTIFY_UIPC_CONNECT");
        assert_eq!(es_type::name(9999), "ES_EVENT_TYPE_UNKNOWN");
    }

    /// 布局守护：镜像结构体必须与 C shim 的期望量级一致。
    /// 精确一致性由字段顺序/类型的双盲比对保证，此处锁住容量与对齐特征，
    /// 防止改 Rust 侧时忘记同步 C 侧。
    #[test]
    fn layout_guards() {
        assert_eq!(size_of::<EsshFile>(), 4144);
        assert_eq!(align_of::<EsshFile>(), 8);
        assert_eq!(size_of::<EsshProcess>() % 8, 0);
        assert_eq!(size_of::<EsshMessage>() % 8, 0);
        assert_eq!(size_of::<EsshSocket>(), 152);
        // exec 是 union 中最大变体，消息整体 ≈ 26 KiB 量级。
        assert!(size_of::<EsshMessage>() > 20 * 1024);
        assert!(size_of::<EsshMessage>() < 64 * 1024);
    }

    #[test]
    fn file_path_slicing() {
        let mut f = EsshFile {
            ino: 0,
            size: 0,
            mtime_sec: 0,
            mode: 0,
            uid: 0,
            gid: 0,
            path_truncated: 0,
            path_len: 0,
            path: [0; PATH_CAP],
        };
        let s = b"/tmp/a.txt";
        f.path[..s.len()].copy_from_slice(s);
        f.path_len = s.len() as u32;
        assert_eq!(f.path_str(), "/tmp/a.txt");
    }

    #[test]
    fn exec_argv_slicing() {
        let mut e = EsshEvExec {
            target: unsafe { std::mem::zeroed() },
            argc: 0,
            argv_offsets: [0; ARGV_MAX],
            argv_lens: [0; ARGV_MAX],
            argv_buf: [0; ARGV_BUFSIZE],
            has_script: 0,
            script: unsafe { std::mem::zeroed() },
            cwd_len: 0,
            cwd: [0; PATH_CAP],
        };
        let mut pos = 0usize;
        for (i, a) in ["python3", "-m", "tui.entry"].iter().enumerate() {
            e.argv_buf[pos..pos + a.len()].copy_from_slice(a.as_bytes());
            e.argv_offsets[i] = pos as u32;
            e.argv_lens[i] = a.len() as u16;
            pos += a.len();
        }
        e.argc = 3;
        assert_eq!(e.argv(), vec!["python3", "-m", "tui.entry"]);
    }

    #[test]
    fn es_type_name_covers_all_known() {
        let known = [
            (es_type::NOTIFY_EXEC, "ES_EVENT_TYPE_NOTIFY_EXEC"),
            (es_type::NOTIFY_OPEN, "ES_EVENT_TYPE_NOTIFY_OPEN"),
            (es_type::NOTIFY_FORK, "ES_EVENT_TYPE_NOTIFY_FORK"),
            (es_type::NOTIFY_CLOSE, "ES_EVENT_TYPE_NOTIFY_CLOSE"),
            (es_type::NOTIFY_CREATE, "ES_EVENT_TYPE_NOTIFY_CREATE"),
            (es_type::NOTIFY_EXIT, "ES_EVENT_TYPE_NOTIFY_EXIT"),
            (es_type::NOTIFY_LINK, "ES_EVENT_TYPE_NOTIFY_LINK"),
            (es_type::NOTIFY_RENAME, "ES_EVENT_TYPE_NOTIFY_RENAME"),
            (
                es_type::NOTIFY_SETATTRLIST,
                "ES_EVENT_TYPE_NOTIFY_SETATTRLIST",
            ),
            (es_type::NOTIFY_SIGNAL, "ES_EVENT_TYPE_NOTIFY_SIGNAL"),
            (es_type::NOTIFY_UNLINK, "ES_EVENT_TYPE_NOTIFY_UNLINK"),
            (es_type::NOTIFY_WRITE, "ES_EVENT_TYPE_NOTIFY_WRITE"),
            (es_type::NOTIFY_TRUNCATE, "ES_EVENT_TYPE_NOTIFY_TRUNCATE"),
            (es_type::NOTIFY_CLONE, "ES_EVENT_TYPE_NOTIFY_CLONE"),
            (es_type::NOTIFY_READDIR, "ES_EVENT_TYPE_NOTIFY_READDIR"),
            (es_type::NOTIFY_UIPC_BIND, "ES_EVENT_TYPE_NOTIFY_UIPC_BIND"),
            (
                es_type::NOTIFY_UIPC_CONNECT,
                "ES_EVENT_TYPE_NOTIFY_UIPC_CONNECT",
            ),
        ];
        for (id, name) in known {
            assert_eq!(es_type::name(id), name);
        }
        assert_eq!(es_type::name(0), "ES_EVENT_TYPE_UNKNOWN");
    }

    #[test]
    fn process_accessors() {
        let mut p = testutil::zeroed_process();
        assert_eq!(p.signing_id_str(), None);
        assert_eq!(p.team_id_str(), None);
        assert_eq!(p.cdhash_hex(), None); // 全零 = 无签名

        p.cdhash[0] = 0xab;
        p.cdhash[19] = 0x01;
        let hex = p.cdhash_hex().unwrap();
        assert!(hex.starts_with("ab") && hex.ends_with("01") && hex.len() == 40);

        let sid = b"com.example.tool";
        p.signing_id[..sid.len()].copy_from_slice(sid);
        p.signing_id_len = sid.len() as u32;
        assert_eq!(p.signing_id_str(), Some("com.example.tool"));

        let tid = b"TEAM123456";
        p.team_id[..tid.len()].copy_from_slice(tid);
        p.team_id_len = tid.len() as u32;
        assert_eq!(p.team_id_str(), Some("TEAM123456"));
    }

    #[test]
    fn exec_cwd_and_socket_addr_accessors() {
        let mut e = testutil::zeroed_exec();
        assert_eq!(e.cwd_str(), None);
        let cwd = b"/tmp/work";
        e.cwd[..cwd.len()].copy_from_slice(cwd);
        e.cwd_len = cwd.len() as u32;
        assert_eq!(e.cwd_str(), Some("/tmp/work"));

        let mut s = testutil::zeroed_socket();
        s.laddr[..9].copy_from_slice(b"127.0.0.1");
        s.faddr[..8].copy_from_slice(b"10.0.0.2");
        assert_eq!(s.laddr_str(), "127.0.0.1");
        assert_eq!(s.faddr_str(), "10.0.0.2");
        // 全零缓冲 → 空串
        let empty = testutil::zeroed_socket();
        assert_eq!(empty.laddr_str(), "");
    }

    #[test]
    fn variant_accessors_match_tag() {
        let mut msg = testutil::zeroed_message();
        msg.event_type = es_type::NOTIFY_FORK;
        assert!(msg.fork().is_some());
        assert!(msg.exec().is_none());
        assert!(msg.exit().is_none());
        assert!(msg.create().is_none());
        assert!(msg.open().is_none());
        assert!(msg.close().is_none());
        assert!(msg.rename().is_none());
        assert!(msg.unlink().is_none());
        assert!(msg.link().is_none());
        assert!(msg.clone_ev().is_none());
        assert!(msg.setattrlist().is_none());
        assert!(msg.truncate().is_none());
        assert!(msg.uipc_bind().is_none());
        assert!(msg.uipc_connect().is_none());
        assert!(msg.write().is_none());
        assert!(msg.readdir().is_none());
        assert!(msg.signal().is_none());
    }

    #[test]
    fn trampoline_delivers_owned_copy() {
        use std::sync::{Arc, Mutex};
        let got: Arc<Mutex<Option<(u32, u64)>>> = Arc::new(Mutex::new(None));
        let got2 = got.clone();
        let handler: SharedHandler = Arc::new(move |msg: &EsshMessage| {
            *got2.lock().unwrap() = Some((msg.event_type, msg.mach_time));
        });
        let ctx = Box::into_raw(Box::new(handler)).cast::<std::ffi::c_void>();
        let mut msg = testutil::zeroed_message();
        msg.event_type = es_type::NOTIFY_FORK;
        msg.mach_time = 12_345;
        trampoline(ctx, &msg);
        // SAFETY: ctx 由上面 Box::into_raw 产生，trampoline 不接管所有权，此处回收。
        unsafe { drop(Box::from_raw(ctx.cast::<SharedHandler>())) };
        assert_eq!(*got.lock().unwrap(), Some((es_type::NOTIFY_FORK, 12_345)));
    }

    /// 测试二进制无 ES entitlement（且非 root），es_new_client 必失败；
    /// 覆盖 Client::new 的错误路径（含 handler ctx 回收）。
    #[test]
    fn client_new_fails_without_entitlement() {
        let handler: SharedHandler = std::sync::Arc::new(|_| {});
        assert!(Client::new(handler).is_err());
    }

    #[test]
    fn list_sockets_self_and_invalid_pid() {
        // 本进程查询无需 root（可能为空列表，但须成功）。
        assert!(list_sockets(std::process::id() as i32).is_ok());
        // 非法 pid → shim 返回 -1。
        assert!(list_sockets(-1).is_err());
    }
}
