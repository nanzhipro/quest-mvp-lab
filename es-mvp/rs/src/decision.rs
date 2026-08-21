//! 裁决引擎：按 MIME 类型决定 AUTH_OPEN 放行/拒绝。
//! 纯函数式设计，无任何 ES 依赖，是全仓测试密度最高的模块。

use mime_guess::{Mime, from_ext};

/// 被拦截的 MIME 类型（MVP 规则：图片禁止打开）。
const DENIED_MIMES: [&str; 2] = ["image/png", "image/jpeg"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Allow,
    Deny,
}

/// 一次裁决的结果及其应答参数派生。
#[derive(Debug)]
pub struct Decision {
    pub verdict: Verdict,
    /// 命中拦截规则时的 MIME（仅诊断展示用）。
    pub denied_mime: Option<Mime>,
}

impl Decision {
    /// 应答 flags：DENY = 0；ALLOW = 透传事件原始 fflag。
    pub fn response_flags(&self, fflag: u32) -> u32 {
        match self.verdict {
            Verdict::Deny => 0,
            Verdict::Allow => fflag,
        }
    }

    /// 是否写入内核授权缓存：DENY 永不缓存（每次拦截都可观测，防误封固化）。
    pub fn cacheable(&self, cache_allow: bool) -> bool {
        cache_allow && self.verdict == Verdict::Allow
    }

    pub fn is_deny(&self) -> bool {
        self.verdict == Verdict::Deny
    }
}

pub struct DecisionEngine;

impl DecisionEngine {
    /// 裁决一次 open。仅普通文件参与类型判定；目录等其余类型一律放行。
    pub fn decide(path: &str, st_mode: u32) -> Decision {
        if !is_regular_file(st_mode) {
            return allow();
        }
        let Some(mime) = mime_of(path) else {
            return allow();
        };
        if DENIED_MIMES.contains(&mime.essence_str()) {
            Decision {
                verdict: Verdict::Deny,
                denied_mime: Some(mime),
            }
        } else {
            allow()
        }
    }
}

fn allow() -> Decision {
    Decision {
        verdict: Verdict::Allow,
        denied_mime: None,
    }
}

fn is_regular_file(st_mode: u32) -> bool {
    // macOS 的 mode_t 为 u16，事件通路统一用 u32 承载，此处显式提升后比较
    st_mode & u32::from(libc::S_IFMT) == u32::from(libc::S_IFREG)
}

/// 扩展名 → MIME（标准 mime_guess 映射，等价 ObjC 版的 UTType 路线）；
/// 无扩展名或未知类型返回 None。不读文件内容，无 TCC/FDA 依赖。
fn mime_of(path: &str) -> Option<Mime> {
    let ext = std::path::Path::new(path).extension()?.to_str()?;
    from_ext(ext).first()
}

#[cfg(test)]
mod tests {
    use super::*;

    const REG: u32 = libc::S_IFREG as u32 | 0o644; // 普通文件
    const DIR: u32 = libc::S_IFDIR as u32 | 0o755; // 目录

    #[test]
    fn denies_png_and_jpeg() {
        for path in ["/w/a.png", "/w/b.jpg", "/w/c.jpeg"] {
            let d = DecisionEngine::decide(path, REG);
            assert!(d.is_deny(), "{path} 应被拒绝");
            assert!(d.denied_mime.is_some());
        }
    }

    #[test]
    fn extension_matching_is_case_insensitive() {
        for path in ["/w/A.PNG", "/w/B.JPG", "/w/C.JpeG"] {
            assert!(
                DecisionEngine::decide(path, REG).is_deny(),
                "{path} 大小写不应逃逸"
            );
        }
    }

    #[test]
    fn allows_other_types_and_extensionless() {
        for path in [
            "/w/a.txt",
            "/w/b.pdf",
            "/w/Makefile",
            "/w/.gitignore",
            "/w/data.bin",
        ] {
            assert!(
                !DecisionEngine::decide(path, REG).is_deny(),
                "{path} 应放行"
            );
        }
    }

    #[test]
    fn allows_non_regular_files_even_with_image_suffix() {
        // 名为 x.png 的目录不是拦截对象
        assert!(!DecisionEngine::decide("/w/x.png", DIR).is_deny());
    }

    #[test]
    fn response_flags_semantics() {
        let fflag = 0x4; // O_RDONLY 之类，原样透传即可
        assert_eq!(
            DecisionEngine::decide("/w/a.txt", REG).response_flags(fflag),
            fflag
        );
        assert_eq!(
            DecisionEngine::decide("/w/a.png", REG).response_flags(fflag),
            0
        );
    }

    #[test]
    fn deny_is_never_cached() {
        let deny = DecisionEngine::decide("/w/a.png", REG);
        assert!(!deny.cacheable(true));
        let allow = DecisionEngine::decide("/w/a.txt", REG);
        assert!(allow.cacheable(true));
        assert!(!allow.cacheable(false));
    }
}
