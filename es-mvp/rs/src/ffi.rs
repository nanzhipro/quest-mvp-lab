//! libEndpointSecurity 的 FFI 声明。全部类型为不透明指针，
//! 布局细节由 `csrc/es_shim.c` 持有，此处只做签名映射。

use std::ffi::{c_char, c_int, c_long, c_uint, c_void};

/// 不透明 client 句柄（对应 shim 的 `esmvp_client_t`）。
pub enum EsMvpClient {}

/// AUTH_OPEN 事件回调签名，与 shim 的 `esmvp_open_cb` 一一对应。
pub type OpenCallback = extern "C" fn(
    ctx: *mut c_void,
    msg: *const c_void,
    path: *const c_char,
    path_len: usize,
    fflag: u32,
    st_mode: c_uint,
);

unsafe extern "C" {
    pub fn esmvp_client_new(
        out: *mut *mut EsMvpClient,
        cb: OpenCallback,
        ctx: *mut c_void,
    ) -> c_int;
    pub fn esmvp_unmute_all_target_paths(client: *mut EsMvpClient) -> c_int;
    pub fn esmvp_invert_target_path_muting(client: *mut EsMvpClient) -> c_int;
    pub fn esmvp_target_muting_is_inverted(client: *mut EsMvpClient) -> c_int;
    pub fn esmvp_mute_target_prefix(client: *mut EsMvpClient, path: *const c_char) -> c_int;
    pub fn esmvp_subscribe_auth_open(client: *mut EsMvpClient) -> c_int;
    pub fn esmvp_respond_open(
        client: *mut EsMvpClient,
        msg: *const c_void,
        flags: u32,
        cache: bool,
    ) -> c_int;
    pub fn esmvp_default_target_mute_count(client: *mut EsMvpClient) -> c_long;
    pub fn esmvp_client_delete(client: *mut EsMvpClient);
}
