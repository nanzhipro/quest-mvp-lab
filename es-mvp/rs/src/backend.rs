//! ES 后端抽象：真实实现走 FFI（`RealEs`），测试用内存实现（`MockEs`）。
//! 业务编排只依赖 [`EsBackend`]  trait，从而无需 root 即可完整单测。

use std::ffi::{CString, c_void};
use std::fmt;
use std::sync::{Arc, Mutex};

use crate::ffi;

/// 一条已从 `es_message_t` 提取为自有数据的 AUTH_OPEN 事件。
#[derive(Debug)]
pub struct OpenEvent {
    /// 不透明应答令牌，仅在回调期间有效，只允许回传给 [`EsBackend::respond_open`]。
    pub msg: *const c_void,
    pub path: String,
    pub fflag: u32,
    pub st_mode: u32,
}

// msg 只是回传令牌，Rust 侧从不解引用，跨线程传递安全。
unsafe impl Send for OpenEvent {}

/// 事件处理器：仅接受共享引用，事件数据不得存留到回调返回之后（msg 令牌失效）。
pub type OpenHandler = Box<dyn Fn(&OpenEvent) + Send + 'static>;

/// 应答器句柄：创建 client 后才存在（`es_new_client` 与 handler 注册同时发生，
/// 存在先有鸡先有蛋问题），因此由后端在 `new_client` 成功后提供。
pub type Responder = Arc<dyn Fn(*const c_void, u32, bool) -> Result<(), EsError> + Send + Sync>;

#[derive(Debug, PartialEq, Eq)]
pub enum EsError {
    /// `es_new_client` 失败，rc 为 `es_new_client_result_t`。
    NewClient(i32),
    /// 其余 ES 调用失败，rc 为 `es_return_t` / `es_respond_result_t`。
    Call { op: &'static str, rc: i32 },
    /// inversion 自检未通过。
    NotInverted,
}

impl fmt::Display for EsError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            EsError::NewClient(rc) => {
                let hint = match rc {
                    3 => {
                        "缺少 com.apple.developer.endpoint-security.client entitlement（检查签名/embedded profile）"
                    }
                    4 => "缺少 TCC 完全磁盘访问授权（系统设置 → 隐私与安全性 → 完全磁盘访问权限）",
                    5 => "需要 root 运行（使用 sudo）",
                    _ => "见 es_new_client_result_t 定义",
                };
                write!(f, "es_new_client 失败 rc={rc}：{hint}")
            }
            EsError::Call { op, rc } => write!(f, "{op} 失败 rc={rc}"),
            EsError::NotInverted => write!(f, "es_muting_inverted 自检未通过（inversion 未生效）"),
        }
    }
}

impl std::error::Error for EsError {}

fn check(op: &'static str, rc: i32) -> Result<(), EsError> {
    if rc == 0 {
        Ok(())
    } else {
        Err(EsError::Call { op, rc })
    }
}

pub trait EsBackend {
    fn new_client(&mut self, handler: OpenHandler) -> Result<(), EsError>;
    fn responder(&self) -> Responder;
    fn default_target_mute_count(&self) -> Result<i64, EsError>;
    fn unmute_all_target_paths(&self) -> Result<(), EsError>;
    fn invert_target_path_muting(&self) -> Result<(), EsError>;
    fn ensure_target_muting_inverted(&self) -> Result<(), EsError>;
    fn mute_target_prefix(&self, path: &str) -> Result<(), EsError>;
    fn subscribe_auth_open(&self) -> Result<(), EsError>;
}

/// 真实后端：经 C shim 调用 libEndpointSecurity。
#[derive(Default)]
pub struct RealEs {
    client: *mut ffi::EsMvpClient,
    /// handler 的所有权驻留块，生命周期与 client 一致（Drop 时回收）。
    handler_ctx: *mut c_void,
}

// ES API 可在任意线程调用（handler 线程内 respond 是官方用法）；
// client 指针只经由 shim 使用，无 Rust 侧数据竞争。
unsafe impl Send for RealEs {}
unsafe impl Sync for RealEs {}

/// 可移入闭包的 client 句柄副本（线程安全性同上）。
#[derive(Clone, Copy)]
struct RawClient(*mut ffi::EsMvpClient);
unsafe impl Send for RawClient {}
unsafe impl Sync for RawClient {}

impl RawClient {
    // 以方法形式封装 FFI 调用：确保闭包按整个 RawClient 捕获
    // （若直接访问 .0，精确捕获语义会只捕获裸指针字段，绕过 Send/Sync 实现）
    fn respond_open(self, msg: *const c_void, flags: u32, cache: bool) -> Result<(), EsError> {
        check("es_respond_flags_result", unsafe {
            ffi::esmvp_respond_open(self.0, msg, flags, cache)
        })
    }
}

type SharedHandler = Arc<dyn Fn(&OpenEvent) + Send + 'static>;

extern "C" fn trampoline(
    ctx: *mut c_void,
    msg: *const c_void,
    path: *const std::ffi::c_char,
    path_len: usize,
    fflag: u32,
    st_mode: std::ffi::c_uint,
) {
    let handler = unsafe { &*(ctx as *const SharedHandler) };
    let bytes = unsafe { std::slice::from_raw_parts(path.cast::<u8>(), path_len) };
    let event = OpenEvent {
        msg,
        path: String::from_utf8_lossy(bytes).into_owned(),
        fflag,
        st_mode,
    };
    handler(&event);
}

impl EsBackend for RealEs {
    fn new_client(&mut self, handler: OpenHandler) -> Result<(), EsError> {
        let shared: SharedHandler = handler.into();
        let ctx = Box::into_raw(Box::new(shared)).cast::<c_void>();
        let mut client: *mut ffi::EsMvpClient = std::ptr::null_mut();
        let rc = unsafe { ffi::esmvp_client_new(&mut client, trampoline, ctx) };
        if rc != 0 {
            unsafe { drop(Box::from_raw(ctx.cast::<SharedHandler>())) };
            return Err(EsError::NewClient(rc));
        }
        self.client = client;
        self.handler_ctx = ctx;
        Ok(())
    }

    fn responder(&self) -> Responder {
        let client = RawClient(self.client);
        Arc::new(move |msg, flags, cache| client.respond_open(msg, flags, cache))
    }

    fn default_target_mute_count(&self) -> Result<i64, EsError> {
        let n = unsafe { ffi::esmvp_default_target_mute_count(self.client) };
        if n < 0 {
            Err(EsError::Call {
                op: "es_muted_paths_events",
                rc: n as i32,
            })
        } else {
            Ok(n)
        }
    }

    fn unmute_all_target_paths(&self) -> Result<(), EsError> {
        check("es_unmute_all_target_paths", unsafe {
            ffi::esmvp_unmute_all_target_paths(self.client)
        })
    }

    fn invert_target_path_muting(&self) -> Result<(), EsError> {
        check("es_invert_muting", unsafe {
            ffi::esmvp_invert_target_path_muting(self.client)
        })
    }

    fn ensure_target_muting_inverted(&self) -> Result<(), EsError> {
        match unsafe { ffi::esmvp_target_muting_is_inverted(self.client) } {
            1 => Ok(()),
            0 => Err(EsError::NotInverted),
            rc => Err(EsError::Call {
                op: "es_muting_inverted",
                rc,
            }),
        }
    }

    fn mute_target_prefix(&self, path: &str) -> Result<(), EsError> {
        let c_path = CString::new(path).expect("静音路径不含内嵌 NUL");
        check("es_mute_path", unsafe {
            ffi::esmvp_mute_target_prefix(self.client, c_path.as_ptr())
        })
    }

    fn subscribe_auth_open(&self) -> Result<(), EsError> {
        check("es_subscribe", unsafe {
            ffi::esmvp_subscribe_auth_open(self.client)
        })
    }
}

impl Drop for RealEs {
    fn drop(&mut self) {
        if !self.client.is_null() {
            unsafe { ffi::esmvp_client_delete(self.client) };
        }
        if !self.handler_ctx.is_null() {
            unsafe { drop(Box::from_raw(self.handler_ctx.cast::<SharedHandler>())) };
        }
    }
}

/// 内存后端：录制 setup 调用序列与应答内容，可手动回放事件，支撑无 root 的完整测试。
pub struct MockEs {
    pub calls: Arc<Mutex<Vec<String>>>,
    pub responds: Arc<Mutex<Vec<(u32, bool)>>>,
    handler: Mutex<Option<OpenHandler>>,
    /// 预设 `es_new_client` 失败码（如 4 模拟未授 FDA）。
    pub new_client_rc: i32,
    /// inversion 自检结果。
    pub inverted: Mutex<bool>,
    /// invert 调用是否真正生效（false 用于模拟 inversion 不被接受）。
    pub invert_effective: bool,
}

impl Default for MockEs {
    fn default() -> Self {
        Self {
            calls: Arc::new(Mutex::new(Vec::new())),
            responds: Arc::new(Mutex::new(Vec::new())),
            handler: Mutex::new(None),
            new_client_rc: 0,
            inverted: Mutex::new(false),
            invert_effective: true,
        }
    }
}

impl MockEs {
    /// 预设 `es_new_client` 失败码（如 4 模拟未授 FDA）。
    pub fn failing_new_client(rc: i32) -> Self {
        Self {
            new_client_rc: rc,
            ..Self::default()
        }
    }

    /// inversion 调用不被内核接受的场景。
    pub fn inversion_rejected() -> Self {
        Self {
            invert_effective: false,
            ..Self::default()
        }
    }

    /// 回放一条 AUTH_OPEN 事件，驱动已注册的 handler。
    pub fn fire(&self, path: &str, st_mode: u32, fflag: u32) {
        let guard = self.handler.lock().unwrap();
        let handler = guard.as_ref().expect("fire 前须先 new_client");
        handler(&OpenEvent {
            msg: std::ptr::null(),
            path: path.to_owned(),
            fflag,
            st_mode,
        });
    }

    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }

    pub fn responds(&self) -> Vec<(u32, bool)> {
        self.responds.lock().unwrap().clone()
    }

    fn record(&self, op: impl Into<String>) {
        self.calls.lock().unwrap().push(op.into());
    }
}

impl EsBackend for MockEs {
    fn new_client(&mut self, handler: OpenHandler) -> Result<(), EsError> {
        self.record("new_client");
        if self.new_client_rc != 0 {
            return Err(EsError::NewClient(self.new_client_rc));
        }
        *self.handler.lock().unwrap() = Some(handler);
        Ok(())
    }

    fn responder(&self) -> Responder {
        let responds = self.responds.clone();
        Arc::new(move |_, flags, cache| {
            responds.lock().unwrap().push((flags, cache));
            Ok(())
        })
    }

    fn default_target_mute_count(&self) -> Result<i64, EsError> {
        self.record("default_target_mute_count");
        Ok(0)
    }

    fn unmute_all_target_paths(&self) -> Result<(), EsError> {
        self.record("unmute_all_target_paths");
        Ok(())
    }

    fn invert_target_path_muting(&self) -> Result<(), EsError> {
        self.record("invert_muting");
        *self.inverted.lock().unwrap() = self.invert_effective;
        Ok(())
    }

    fn ensure_target_muting_inverted(&self) -> Result<(), EsError> {
        self.record("ensure_inverted");
        if *self.inverted.lock().unwrap() {
            Ok(())
        } else {
            Err(EsError::NotInverted)
        }
    }

    fn mute_target_prefix(&self, path: &str) -> Result<(), EsError> {
        self.record(format!("mute_target_prefix:{path}"));
        Ok(())
    }

    fn subscribe_auth_open(&self) -> Result<(), EsError> {
        self.record("subscribe_auth_open");
        Ok(())
    }
}
