//! 统一错误类型。

use thiserror::Error;

#[derive(Debug, Error)]
pub enum SensorError {
    /// `es_new_client` 失败，rc 为 `es_new_client_result_t`。
    #[error("es_new_client 失败 rc={rc}：{}", new_client_hint(*rc))]
    NewClient { rc: i32 },

    /// 其余 ES 调用失败，rc 为 `es_return_t`。
    #[error("{op} 失败 rc={rc}")]
    EsCall { op: &'static str, rc: i32 },

    #[error("配置错误: {0}")]
    Config(String),

    #[error(transparent)]
    Io(#[from] std::io::Error),
}

fn new_client_hint(rc: i32) -> &'static str {
    match rc {
        3 => "缺少 com.apple.developer.endpoint-security.client entitlement（检查签名/embedded profile）",
        4 => "缺少 TCC 完全磁盘访问授权（系统设置 → 隐私与安全性 → 完全磁盘访问权限）",
        5 => "需要 root 运行（使用 sudo）",
        _ => "见 es_new_client_result_t 定义",
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn new_client_hints_per_rc() {
        assert!(SensorError::NewClient { rc: 3 }
            .to_string()
            .contains("entitlement"));
        assert!(SensorError::NewClient { rc: 4 }
            .to_string()
            .contains("完全磁盘访问"));
        assert!(SensorError::NewClient { rc: 5 }
            .to_string()
            .contains("root"));
        assert!(SensorError::NewClient { rc: 99 }
            .to_string()
            .contains("es_new_client_result_t"));
        assert!(SensorError::NewClient { rc: 3 }
            .to_string()
            .contains("rc=3"));
    }

    #[test]
    fn es_call_display() {
        let e = SensorError::EsCall {
            op: "es_subscribe",
            rc: 1,
        };
        assert_eq!(e.to_string(), "es_subscribe 失败 rc=1");
    }

    #[test]
    fn config_display() {
        let e = SensorError::Config("bad agent spec".into());
        assert_eq!(e.to_string(), "配置错误: bad agent spec");
    }

    #[test]
    fn io_from_and_display() {
        let io = std::io::Error::new(std::io::ErrorKind::NotFound, "no such file");
        let e: SensorError = io.into();
        assert!(matches!(e, SensorError::Io(_)));
        assert!(e.to_string().contains("no such file"));
    }
}
