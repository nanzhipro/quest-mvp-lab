//! SBPL construct acceptance matrix.
//!
//! Every field family used by `policy/agent-sandbox.sb` is applied **in
//! isolation** on top of one known-good base profile, then driven through
//! `/usr/bin/sandbox-exec /usr/bin/true`. The point is to tell three outcomes
//! apart, which a whole-profile run cannot:
//!
//! * the construct is accepted and the sandbox still runs (`ran`),
//! * sandbox-exec rejects the profile at evaluation time (`rejected`, exit 65,
//!   message on stderr),
//! * the sandbox aborts the process at apply time (`aborted`, SIGABRT) — the
//!   failure mode that looks like "nothing ran" and hides its own cause.
//!
//! Prints a table (run with `-- --nocapture`). macOS only.

#![cfg(target_os = "macos")]

use std::path::Path;
use std::process::Command;

const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";
const TAG: &str = "SC_SBX_CONSTRUCT";
const ROOT: &str = "/private/tmp/sc-agent-sandbox";

/// A profile every construct test can build on: the minimum that lets `zsh`
/// and a plain binary start. Derived by bisection — see the table's `base` row.
const BASE: &str = concat!(
    "(version 1)\n",
    "(debug deny)\n",
    "(deny default (with message \"SC_SBX_CONSTRUCT\"))\n",
    "(allow process-exec*)\n",
    "(allow process-fork)\n",
    "(allow signal (target self))\n",
    "(allow sysctl-read)\n",
    "(allow ipc-posix-shm)\n",
    "(allow file-read*)\n",
    "(allow file-ioctl)\n",
    "(allow file-write* (subpath \"/dev\"))\n",
    "(allow mach-lookup\n",
    "  (global-name \"com.apple.system.notification_center\")\n",
    "  (global-name \"com.apple.system.opendirectoryd.libinfo\")\n",
    "  (global-name \"com.apple.system.logger\")\n",
    "  (global-name \"com.apple.logd\")\n",
    "  (global-name \"com.apple.diagnosticd\")\n",
    "  (global-name \"com.apple.cfprefsd.agent\")\n",
    "  (global-name \"com.apple.cfprefsd.daemon\")\n",
    "  (global-name \"com.apple.distributed_notifications@Uv3\")\n",
    "  (global-name \"com.apple.SecurityServer\"))\n"
);

struct Construct {
    id: &'static str,
    field: &'static str,
    snippet: &'static str,
}

fn constructs() -> Vec<Construct> {
    vec![
        Construct {
            id: "C-00",
            field: "base（对照：无附加构造）",
            snippet: "",
        },
        Construct {
            id: "C-01",
            field: "(version)/(debug)/(deny default (with message))",
            snippet: "(allow process-fork)\n(deny default)\n(deny default (with message \"x\"))\n",
        },
        Construct {
            id: "C-02",
            field: "(define (f x) (string-append ...))",
            snippet: "(define (under rest) (string-append \"/private/tmp/sc-agent-sandbox\" rest))\n(allow file-write* (subpath (under \"/app-home/cache-demo\")))\n",
        },
        Construct {
            id: "C-03",
            field: "(when (param \"X\") ...)",
            snippet: "(when (param \"SC_EXTRA_WRITE\") (allow file-write* (subpath (param \"SC_EXTRA_WRITE\"))))\n",
        },
        Construct {
            id: "C-04",
            field: "process-exec: literal 列表",
            snippet: "(deny process-exec (regex #\"^/usr/bin/true$\") (with message \"SC_SBX_CONSTRUCT\"))\n(allow process-exec (literal \"/usr/bin/true\") (literal \"/bin/zsh\"))\n",
        },
        Construct {
            id: "C-05",
            field: "process-exec: regex 过滤器",
            snippet: "(allow process-exec (regex #\"^/usr/bin/true$\"))\n",
        },
        Construct {
            id: "C-06",
            field: "signal (target self) (target children)",
            snippet: "(allow signal (target self) (target children))\n",
        },
        Construct {
            id: "C-07",
            field: "process-info-pidinfo (target self)",
            snippet: "(allow process-info-pidinfo (target self))\n",
        },
        Construct {
            id: "C-08",
            field: "ipc-posix-shm-* + (ipc-posix-name-regex)",
            snippet: "(allow ipc-posix-shm-read* ipc-posix-shm-write-create (ipc-posix-name-regex #\"^/sc_agent_\"))\n",
        },
        Construct {
            id: "C-09",
            field: "sysctl-read + (sysctl-name …)",
            snippet: "(allow sysctl-read (sysctl-name \"kern.osrelease\") (sysctl-name \"kern.ostype\"))\n",
        },
        Construct {
            id: "C-10",
            field: "file-read* + (subpath …) 列表",
            snippet: "(allow file-read* (subpath \"/usr\") (subpath \"/System\") (subpath \"/bin\") (subpath \"/sbin\"))\n",
        },
        Construct {
            id: "C-11",
            field: "deny file-read* + (subpath …) + message",
            snippet: "(deny file-read* (subpath \"/private/tmp/sc-agent-sandbox/app-home/secrets\") (with message \"SC_SBX_CONSTRUCT\"))\n",
        },
        Construct {
            id: "C-12",
            field: "require-all + require-not",
            snippet: "(allow file-write* (require-all (subpath \"/private/tmp/sc-agent-sandbox/app-home/workspace\") (require-not (subpath \"/private/tmp/sc-agent-sandbox/app-home/workspace/.git\"))))\n",
        },
        Construct {
            id: "C-13",
            field: "require-any",
            snippet: "(allow file-write* (require-any (subpath \"/private/tmp/sc-agent-sandbox/app-home/workspace\") (subpath \"/private/tmp/sc-agent-sandbox/extra\")))\n",
        },
        Construct {
            id: "C-14",
            field: "file-write* + (regex #\"…\")",
            snippet: "(allow file-write* (regex #\"^/private/tmp/sc-agent-sandbox/app-home/workspace/logs/[^/]+\\.log$\"))\n",
        },
        Construct {
            id: "C-15",
            field: "file-write* + (literal …) 设备列表",
            snippet: "(allow file-write* (literal \"/dev/null\") (literal \"/dev/stdout\") (literal \"/dev/stderr\") (literal \"/dev/tty\"))\n",
        },
        Construct {
            id: "C-16",
            field: "file-write* + (vnode-type REGULAR)",
            snippet: "(allow file-write* (require-all (subpath \"/private/tmp/sc-agent-sandbox/app-home/cache-demo\") (vnode-type REGULAR)))\n",
        },
        Construct {
            id: "C-17",
            field: "file-write* + (prefix …)",
            snippet: "(allow file-write* (prefix \"/private/tmp/sc-agent-sandbox/app-home/cache-demo/\"))\n",
        },
        Construct {
            id: "C-18",
            field: "delete 三段式：deny file-write-unlink (subpath \"/\")",
            snippet: "(deny file-write-unlink (subpath \"/\") (with message \"SC_SBX_CONSTRUCT\"))\n(allow file-write-unlink (subpath \"/private/tmp/sc-agent-sandbox/app-home/trash\"))\n",
        },
        Construct {
            id: "C-19",
            field: "home-subpath（自定义辅助函数）",
            snippet: "(define (home-subpath rest) (subpath (string-append (param \"_HOME\") rest)))\n(deny file-write* (home-subpath \"/.ssh\") (with message \"SC_SBX_CONSTRUCT\"))\n",
        },
        Construct {
            id: "C-20",
            field: "network-outbound (remote ip \"localhost:*\")",
            snippet: "(allow network-outbound (remote ip \"localhost:*\"))\n",
        },
        Construct {
            id: "C-21",
            field: "network-inbound (local ip \"localhost:*\")",
            snippet: "(allow network-inbound (local ip \"localhost:*\"))\n",
        },
        Construct {
            id: "C-22",
            field: "network-outbound (remote unix-socket (path-regex …))",
            snippet: "(allow network-outbound (remote unix-socket (path-regex #\"^/private/tmp/sc-agent-sandbox/.*\")))\n",
        },
        Construct {
            id: "C-23",
            field: "system-socket (socket-domain AF_UNIX)",
            snippet: "(allow system-socket (socket-domain AF_UNIX))\n",
        },
        Construct {
            id: "C-24",
            field: "deny network* + message",
            snippet: "(deny network* (with message \"SC_SBX_CONSTRUCT\"))\n",
        },
        Construct {
            id: "C-25",
            field: "deny mach-lookup (global-name …)",
            snippet: "(deny mach-lookup (global-name \"com.apple.tccd\") (global-name \"com.apple.windowserver.active\") (with message \"SC_SBX_CONSTRUCT\"))\n",
        },
        Construct {
            id: "C-26",
            field: "deny mach-lookup 命中启动期服务",
            snippet: "(deny mach-lookup (global-name \"com.apple.diagnosticd\") (with message \"SC_SBX_CONSTRUCT\"))\n",
        },
        Construct {
            id: "C-27",
            field: "user-preference-read (preference-domain …)",
            snippet: "(allow user-preference-read (preference-domain \"com.apple.sandbox-agent-demo\"))\n",
        },
        Construct {
            id: "C-28",
            field: "(allow … (with report))（allow 侧）",
            snippet: "(allow file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/audited-writes\") (with report))\n",
        },
        Construct {
            id: "C-29",
            field: "(deny … (with report))（deny 侧）",
            snippet: "(deny file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/noisy-deny\") (with report))\n",
        },
        Construct {
            id: "C-30",
            field: "(deny … (with no-log))",
            snippet: "(deny file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/noisy-deny\") (with no-log))\n",
        },
        Construct {
            id: "C-31",
            field: "(deny … (with errno EACCES))",
            snippet: "(deny file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/noisy-deny\") (with errno EACCES))\n",
        },
        Construct {
            id: "C-32",
            field: "(deny … (with telemetry))",
            snippet: "(deny file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/noisy-deny\") (with telemetry))\n",
        },
        Construct {
            id: "C-33",
            field: "(allow (with report) <operation>)（修饰符前置）",
            snippet: "(allow (with report) file-write* (subpath \"/private/tmp/sc-agent-sandbox/app-home/audited-writes\"))\n",
        },
        Construct {
            id: "C-34",
            field: "(import \"/System/Library/Sandbox/Profiles/bsd.sb\")：绝对路径导入",
            snippet: "(import \"/System/Library/Sandbox/Profiles/bsd.sb\")\n",
        },
    ]
}

enum Verdict {
    Ran,
    Rejected(String),
    Aborted,
    Other(String),
}

#[test]
fn construct_acceptance() {
    std::fs::create_dir_all(format!("{ROOT}/app-home/workspace/logs")).expect("zone tree");
    let dir = std::env::temp_dir().join(format!("sc-construct-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("construct dir");
    let home = std::env::var("HOME").expect("HOME");
    let mut failures: Vec<String> = Vec::new();
    let mut rows: Vec<String> = Vec::new();

    for construct in constructs() {
        let profile = dir.join(format!("{}.sb", construct.id));
        std::fs::write(&profile, format!("{BASE}{}", construct.snippet)).expect("write profile");
        let mut args: Vec<String> = vec!["-f".into(), profile.to_string_lossy().into_owned()];
        if construct.id == "C-19" {
            args.push("-D".into());
            args.push(format!("_HOME={home}"));
        }
        args.push("/usr/bin/true".into());
        let output = Command::new(SANDBOX_EXEC)
            .args(&args)
            .output()
            .expect("run sandbox-exec");
        use std::os::unix::process::ExitStatusExt;
        let verdict = match output.status.signal() {
            Some(6) => Verdict::Aborted,
            Some(signal) => Verdict::Other(format!("signal {signal}")),
            None if output.status.code() == Some(0) => Verdict::Ran,
            None => {
                let stderr = String::from_utf8_lossy(&output.stderr).trim().to_string();
                if stderr.contains("profile") || stderr.contains("modifier") || stderr.contains("unbound")
                {
                    Verdict::Rejected(stderr)
                } else {
                    Verdict::Other(format!(
                        "exit {:?} {}",
                        output.status.code(),
                        if stderr.is_empty() { "(no stderr)" } else { &stderr }
                    ))
                }
            }
        };
        let (label, detail) = match &verdict {
            Verdict::Ran => ("ran".to_string(), String::new()),
            Verdict::Rejected(message) => ("rejected".to_string(), message.clone()),
            Verdict::Aborted => ("ABORTED".to_string(), "SIGABRT".to_string()),
            Verdict::Other(message) => ("other".to_string(), message.clone()),
        };
        if !matches!(verdict, Verdict::Ran) {
            failures.push(format!("{} ({}) — {label} {detail}", construct.id, construct.field));
        }
        rows.push(format!(
            "  {:<6} {:<10} {:<46} {}",
            construct.id,
            label,
            construct.field,
            truncate(&detail, 60)
        ));
    }

    println!("\nSBPL construct acceptance ({} constructs)\n", rows.len());
    for row in &rows {
        println!("{row}");
    }
    let _ = std::fs::remove_dir_all(&dir);
    let _ = std::fs::remove_dir_all(ROOT);

    assert!(
        failures.is_empty(),
        "constructs rejected or aborting:\n{}",
        failures.join("\n")
    );
}

fn truncate(text: &str, limit: usize) -> String {
    let text = text.replace('\n', " ");
    if text.chars().count() > limit {
        let cut: String = text.chars().take(limit).collect();
        format!("{cut}…")
    } else {
        text
    }
}

#[allow(dead_code)]
fn unused(path: &Path) {}
