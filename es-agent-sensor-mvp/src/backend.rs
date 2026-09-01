//! ES 后端抽象：真实实现走 FFI（`RealEs`），测试用内存实现（`MockEs`）。
//! 业务编排只依赖 [`EsBackend`] trait，从而无需 root 即可完整单测。
//! 本模块不含 unsafe（全部收敛于 ffi.rs 的安全包装层）。

use std::ffi::CString;
use std::sync::{Arc, Mutex};

use crate::error::SensorError;
use crate::ffi;

/// 一条已从 `es_message_t` 提取为自有数据的扁平事件（所有权在 Rust 侧）。
pub type RawEvent = ffi::EsshMessage;

/// 事件处理器：仅接受共享引用调用，事件按值交付（已从 C 栈整体拷贝）。
pub type RawHandler = Box<dyn Fn(&RawEvent) + Send + 'static>;

pub trait EsBackend {
    fn new_client(&mut self, handler: RawHandler) -> Result<(), SensorError>;
    /// 订阅事件：mask 位序见 `csrc/es_shim.h` 与 [`crate::ffi`] 常量。
    fn subscribe(&self, event_mask: u32, extra_mask: u32) -> Result<(), SensorError>;
    /// 内核级 target path 前缀静音。
    fn mute_path_prefix(&self, path: &str) -> Result<(), SensorError>;
}

fn check(op: &'static str, rc: i32) -> Result<(), SensorError> {
    if rc == 0 {
        Ok(())
    } else {
        Err(SensorError::EsCall { op, rc })
    }
}

/// 真实后端：经 C shim 调用 libEndpointSecurity。
#[derive(Default)]
pub struct RealEs {
    client: Option<ffi::Client>,
}

impl EsBackend for RealEs {
    fn new_client(&mut self, handler: RawHandler) -> Result<(), SensorError> {
        let shared: ffi::SharedHandler = handler.into();
        self.client = Some(ffi::Client::new(shared).map_err(|rc| SensorError::NewClient { rc })?);
        Ok(())
    }

    fn subscribe(&self, event_mask: u32, extra_mask: u32) -> Result<(), SensorError> {
        let client = self.client.as_ref().expect("subscribe 前须先 new_client");
        check("es_subscribe", client.subscribe(event_mask, extra_mask))
    }

    fn mute_path_prefix(&self, path: &str) -> Result<(), SensorError> {
        let client = self
            .client
            .as_ref()
            .expect("mute_path_prefix 前须先 new_client");
        let c_path = CString::new(path).expect("静音路径不含内嵌 NUL");
        check("es_mute_path", client.mute_path_prefix(&c_path))
    }
}

/// 内存后端：录制 setup 调用序列，可手动回放事件流，支撑无 root 的完整测试。
#[derive(Default)]
pub struct MockEs {
    pub calls: Arc<Mutex<Vec<String>>>,
    handler: Mutex<Option<RawHandler>>,
    /// 预设 `es_new_client` 失败码（如 5 模拟非 root）。
    pub new_client_rc: i32,
}

impl MockEs {
    /// 预设 `es_new_client` 失败码。
    pub fn failing_new_client(rc: i32) -> Self {
        Self {
            new_client_rc: rc,
            ..Self::default()
        }
    }

    /// 回放一条事件，驱动已注册的 handler。
    pub fn fire(&self, msg: &RawEvent) {
        let guard = self.handler.lock().unwrap();
        let handler = guard.as_ref().expect("fire 前须先 new_client");
        handler(msg);
    }

    pub fn calls(&self) -> Vec<String> {
        self.calls.lock().unwrap().clone()
    }

    fn record(&self, op: impl Into<String>) {
        self.calls.lock().unwrap().push(op.into());
    }
}

impl EsBackend for MockEs {
    fn new_client(&mut self, handler: RawHandler) -> Result<(), SensorError> {
        self.record("new_client");
        if self.new_client_rc != 0 {
            return Err(SensorError::NewClient {
                rc: self.new_client_rc,
            });
        }
        *self.handler.lock().unwrap() = Some(handler);
        Ok(())
    }

    fn subscribe(&self, event_mask: u32, extra_mask: u32) -> Result<(), SensorError> {
        self.record(format!("subscribe:{event_mask:#x},{extra_mask:#x}"));
        Ok(())
    }

    fn mute_path_prefix(&self, path: &str) -> Result<(), SensorError> {
        self.record(format!("mute_path_prefix:{path}"));
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    #[test]
    fn mock_records_setup_calls() {
        let mut mock = MockEs::default();
        mock.new_client(Box::new(|_| {})).unwrap();
        mock.subscribe(ffi::EV_DEFAULT_MASK, 0).unwrap();
        mock.mute_path_prefix("/System/").unwrap();
        assert_eq!(
            mock.calls(),
            vec![
                "new_client".to_string(),
                format!("subscribe:{:#x},0x0", ffi::EV_DEFAULT_MASK),
                "mute_path_prefix:/System/".to_string(),
            ]
        );
    }

    #[test]
    fn mock_replays_events_to_handler() {
        let mut mock = MockEs::default();
        let count = Arc::new(AtomicUsize::new(0));
        let count2 = count.clone();
        mock.new_client(Box::new(move |msg| {
            assert_eq!(msg.event_type, ffi::es_type::NOTIFY_OPEN);
            count2.fetch_add(1, Ordering::SeqCst);
        }))
        .unwrap();
        let mut msg = ffi::testutil::zeroed_message();
        msg.event_type = ffi::es_type::NOTIFY_OPEN;
        mock.fire(&msg);
        mock.fire(&msg);
        assert_eq!(count.load(Ordering::SeqCst), 2);
    }

    #[test]
    fn mock_new_client_failure() {
        let mut mock = MockEs::failing_new_client(5);
        let err = mock.new_client(Box::new(|_| {})).unwrap_err();
        assert!(matches!(err, SensorError::NewClient { rc: 5 }));
        assert!(err.to_string().contains("root"));
    }

    /// 测试二进制无 ES entitlement（且非 root），RealEs 创建 client 必失败。
    #[test]
    fn real_es_new_client_fails_without_entitlement() {
        let mut real = RealEs::default();
        let err = real.new_client(Box::new(|_| {})).unwrap_err();
        assert!(matches!(err, SensorError::NewClient { .. }));
    }

    #[test]
    #[should_panic(expected = "subscribe 前须先 new_client")]
    fn real_es_subscribe_before_client_panics() {
        let real = RealEs::default();
        let _ = real.subscribe(ffi::EV_DEFAULT_MASK, 0);
    }

    #[test]
    #[should_panic(expected = "mute_path_prefix 前须先 new_client")]
    fn real_es_mute_before_client_panics() {
        let real = RealEs::default();
        let _ = real.mute_path_prefix("/System/");
    }
}
