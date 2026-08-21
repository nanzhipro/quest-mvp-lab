//! Config loading and API-key security tests.
//!
//! These tests mutate the process environment, so they MUST run serially —
//! a global mutex serializes them against each other.

use ds_vision::config::Config;

static ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());

fn with_env_lock<T>(f: impl FnOnce() -> T) -> T {
    let _guard = ENV_LOCK.lock().unwrap();
    f()
}

fn set_env(key: &str, val: &str) {
    unsafe { std::env::set_var(key, val) };
}
fn unset_env(key: &str) {
    unsafe { std::env::remove_var(key) };
}

/// 隔离环境：清除全部相关变量，避免本机 .env / shell 污染测试。
struct EnvGuard;
impl EnvGuard {
    fn clean() -> Self {
        for k in ["DEEPSEEK_API_KEY", "DS_MODEL", "DS_BASE_URL"] {
            unset_env(k);
        }
        EnvGuard
    }
}
impl Drop for EnvGuard {
    fn drop(&mut self) {
        for k in ["DEEPSEEK_API_KEY", "DS_MODEL", "DS_BASE_URL"] {
            unset_env(k);
        }
    }
}

#[test]
fn from_env_reads_key_model_base_url() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        set_env("DEEPSEEK_API_KEY", "sk-test-123");
        set_env("DS_MODEL", "deepseek-v4-flash-vision-exp");
        set_env("DS_BASE_URL", "https://api.deepseek.com");
        let cfg = Config::from_env().unwrap();
        assert_eq!(cfg.api_key(), "sk-test-123");
        assert_eq!(cfg.model(), "deepseek-v4-flash-vision-exp");
        assert_eq!(cfg.base_url(), "https://api.deepseek.com");
    });
}

#[test]
fn defaults_are_model_and_official_base_url() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        set_env("DEEPSEEK_API_KEY", "sk-test-123");
        let cfg = Config::from_env().unwrap();
        assert_eq!(cfg.model(), "deepseek-v4-flash-vision-exp");
        assert_eq!(cfg.base_url(), "https://api.deepseek.com");
    });
}

#[test]
fn missing_key_is_an_error() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        let err = Config::from_env().unwrap_err();
        assert!(
            err.to_string().contains("DEEPSEEK_API_KEY"),
            "should name the missing var: {err}"
        );
    });
}

#[test]
fn redact_never_leaves_the_key_in_messages() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        set_env("DEEPSEEK_API_KEY", "sk-supersecret-98765");
        let cfg = Config::from_env().unwrap();
        let leaked = format!("连接失败: Bearer {}", cfg.api_key());
        let redacted = cfg.redact(&leaked);
        assert!(!redacted.contains("sk-supersecret-98765"));
        assert!(redacted.contains("[REDACTED]"));
    });
}

#[test]
fn api_key_is_not_debug_printed() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        set_env("DEEPSEEK_API_KEY", "sk-debugcheck-42");
        let cfg = Config::from_env().unwrap();
        let dbg = format!("{:?}", cfg);
        assert!(
            !dbg.contains("sk-debugcheck-42"),
            "Debug impl must redact the key: {dbg}"
        );
    });
}

#[test]
fn display_never_prints_key() {
    with_env_lock(|| {
        let _g = EnvGuard::clean();
        set_env("DEEPSEEK_API_KEY", "sk-displaycheck-42");
        let cfg = Config::from_env().unwrap();
        let disp = format!("{}", cfg);
        assert!(!disp.contains("sk-displaycheck-42"));
    });
}
