//! Which field *combination* the shipped profile needs to stay runnable.
//!
//! `seatbelt_construct_acceptance.rs` proves each field family is accepted in
//! isolation. This test answers the next question: when the whole profile is
//! applied, which single restriction is responsible for the process aborting
//! at startup? Each case relaxes exactly one restriction in the shipped
//! profile text and reports the verdict, so the answer is measured rather than
//! guessed. macOS only.

#![cfg(target_os = "macos")]

use std::process::Command;

const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";
const PROFILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../policy/agent-sandbox.sb"
);
const ROOT: &str = "/private/tmp/sc-agent-sandbox";
const EXTRA: &str = "/private/tmp/sc-agent-sandbox/extra";
/// The tag the shipped profile stamps on every denial.
const TAG: &str = "SC_SBX_AGENT";

/// One relaxation: a substring of the shipped profile and its replacement.
struct Relaxation {
    id: &'static str,
    field: &'static str,
    from: &'static str,
    to: &'static str,
    /// `true` when this variant is *expected* to abort: it narrows a surface
    /// below what the runtime needs at init time. That is the finding, not a
    /// failure — the test asserts the abort actually happens.
    expect_abort: bool,
    /// When set, the tagged kernel log must contain this string after the run —
    /// used where the only observable form of a denial is the kernel log.
    expect_log: Option<&'static str>,
}

fn relaxations() -> Vec<Relaxation> {
    vec![
        Relaxation {
            id: "R-00",
            field: "原样：不做任何放宽",
            from: "",
            to: "",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-01",
            field: "读面去掉 (literal \"/\")（其余一字不动）",
            from: "       ;; (literal \"/\") 不是装饰：进程初始化时会读根目录本身，少了它直接 SIGABRT。\n       (literal \"/\")\n",
            to: "",
            expect_abort: true,
            expect_log: None,
        },
        Relaxation {
            id: "R-02",
            field: "读面收敛为仅 /usr /System /bin /sbin /private/etc /dev",
            from: "       (subpath \"/Library\")\n       (subpath \"/Applications\")\n       (subpath \"/private/etc\")\n       (subpath \"/private/var/db/timezone\")\n",
            to: "       (subpath \"/private/etc\")\n",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-03",
            field: "读面放宽为 (allow file-read*)",
            from: "(allow file-read* file-read-metadata file-test-existence",
            to: "(allow file-read*)\n(allow file-read* file-read-metadata file-test-existence",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-04",
            field: "sysctl 放宽为 (allow sysctl-read)（去掉名字白名单）",
            from: "(allow sysctl-read\n       (sysctl-name \"kern.osrelease\")",
            to: "(allow sysctl-read)\n(allow sysctl-read\n       (sysctl-name \"kern.osrelease\")",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-05",
            field: "ipc-posix-shm 放宽为整族放行（去掉 name 过滤）",
            from: "(allow ipc-posix-shm-read* ipc-posix-shm-write-create\n       (require-any\n         (ipc-posix-name-regex #\"^/sc_agent_\")\n         ;; 通知中心的共享内存：zsh 启动路径会读它，漏放行会让命令异常退出。\n         (ipc-posix-name \"apple.shm.notification_center\")))",
            to: "(allow ipc-posix-shm)",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-06",
            field: "exec 放宽为 (allow process-exec*)（去掉 literal 白名单）",
            from: "(allow process-exec\n       (literal \"/bin/zsh\")",
            to: "(allow process-exec*)\n(allow process-exec\n       (literal \"/bin/zsh\")",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-07",
            field: "去掉 deny mach-lookup 段",
            from: "(deny mach-lookup\n      (global-name \"com.apple.tccd\")\n      (global-name \"com.apple.windowserver.active\")\n      (with message \"SC_SBX_AGENT\"))",
            to: "",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-08",
            field: "去掉全局删除保护 deny file-write-unlink",
            from: "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_AGENT\"))",
            to: "",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-09",
            field: "去掉 secrets / home 凭据目录 deny",
            from: "(deny file-write* (subpath (under \"/app-home/secrets\")) (with message \"SC_SBX_AGENT\"))",
            to: "",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-10",
            field: "去掉 (with no-log) 的 deny",
            from: "(deny file-write* (subpath (under \"/app-home/noisy-deny\")) (with no-log))",
            to: "",
            expect_abort: false,
            expect_log: None,
        },
        Relaxation {
            id: "R-11",
            field: "sysctl 白名单删掉 kern.version（其余一字不动）",
            from: "       (sysctl-name \"kern.version\")\n",
            to: "",
            expect_abort: false,
            expect_log: Some("sysctl-read kern.version"),
        },
    ]
}

#[test]
fn profile_dependency_bisect() {
    let profile_text = std::fs::read_to_string(PROFILE).expect("read shipped profile");
    assert!(
        std::path::Path::new(ROOT).exists() || std::fs::create_dir_all(ROOT).is_ok(),
        "cannot create {ROOT}"
    );
    let dir = std::env::temp_dir().join(format!("sc-bisect-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("bisect dir");
    let home = std::env::var("HOME").expect("HOME");
    let mut failures: Vec<String> = Vec::new();
    let mut rows: Vec<String> = Vec::new();

    for relaxation in relaxations() {
        let body = if relaxation.from.is_empty() {
            profile_text.clone()
        } else {
            assert!(
                profile_text.contains(relaxation.from),
                "{}: anchor not found in the shipped profile — update the bisect",
                relaxation.id
            );
            profile_text.replace(relaxation.from, relaxation.to)
        };
        let path = dir.join(format!("{}.sb", relaxation.id));
        std::fs::write(&path, body).expect("write variant");
        let output = Command::new(SANDBOX_EXEC)
            .args([
                "-f",
                &path.to_string_lossy(),
                "-D",
                &format!("_HOME={home}"),
                "-D",
                &format!("SC_EXTRA_WRITE={EXTRA}"),
                "/bin/zsh",
                "/dev/null",
            ])
            .output()
            .expect("run sandbox-exec");
        use std::os::unix::process::ExitStatusExt;
        let aborted = matches!((output.status.signal(), output.status.code()), (Some(6), _));
        let log_hit = relaxation
            .expect_log
            .map(|needle| tagged_log_contains(needle));
        let verdict = match (output.status.signal(), output.status.code()) {
            (Some(6), _) => "ABORTED (SIGABRT)".to_string(),
            (Some(signal), _) => format!("signal {signal}"),
            (None, Some(0)) => "ran".to_string(),
            (None, code) => format!(
                "exit {:?} {}",
                code,
                String::from_utf8_lossy(&output.stderr).trim()
            ),
        };
        let note = if relaxation.expect_abort {
            if aborted {
                "  ← 预期：读面收敛即 abort"
            } else {
                failures.push(format!("{} should have aborted", relaxation.id));
                "  ← 预期 abort，但没有"
            }
        } else if aborted {
            failures.push(format!("{} must not abort", relaxation.id));
            "  ← 不该 abort"
        } else if let Some(hit) = log_hit {
            if hit {
                "  ← 预期：删掉的白名单名字出现在拒绝日志"
            } else {
                failures.push(format!("{} must log the denied name", relaxation.id));
                "  ← 拒绝日志里没找到该名字"
            }
        } else {
            ""
        };
        rows.push(format!(
            "  {:<6} {:<52} {verdict}{note}",
            relaxation.id, relaxation.field
        ));
    }

    println!("\nshipped profile — single-relaxation bisect\n");
    for row in &rows {
        println!("{row}");
    }

    // A narrowing that aborts gives no stderr; the kernel denial log is the
    // only place the missing path shows up.
    let log = Command::new("/usr/bin/log")
        .args([
            "show",
            "--last",
            "2m",
            "--style",
            "compact",
            "--predicate",
            "eventMessage CONTAINS \"SC_SBX_AGENT\"",
        ])
        .output()
        .expect("run log show");
    let text = String::from_utf8_lossy(&log.stdout).into_owned();
    let mut denials: Vec<&str> = text
        .lines()
        .filter(|line| line.contains("deny"))
        .collect();
    denials.truncate(12);
    println!(
        "\nkernel denials carrying the profile tag (first {} of {}):",
        denials.len(),
        text.lines().filter(|line| line.contains("deny")).count()
    );
    for line in &denials {
        println!(
            "  {}",
            if line.chars().count() > 150 {
                line.chars().take(150).collect::<String>()
            } else {
                (*line).to_string()
            }
        );
    }

    let _ = std::fs::remove_dir_all(&dir);

    assert!(
        failures.is_empty(),
        "bisect expectations not met:\n{}",
        failures.join("\n")
    );
}

/// Whether the tagged (profile tag) denial log mentions `needle` at all.
fn tagged_log_contains(needle: &str) -> bool {
    let output = Command::new("/usr/bin/log")
        .args([
            "show",
            "--last",
            "2m",
            "--style",
            "compact",
            "--predicate",
            &format!("eventMessage CONTAINS \"{TAG}\""),
        ])
        .output()
        .expect("run log show");
    String::from_utf8_lossy(&output.stdout).contains(needle)
}
