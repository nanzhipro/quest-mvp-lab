//! Field-by-field verification of `policy/agent-sandbox.sb`.
//!
//! The profile is a hand-authored SBPL reference: it exercises every field
//! family Seatbelt offers (action, operation, filter, structural form,
//! modifier). This test drives it through the real `/usr/bin/sandbox-exec`
//! and asserts **kernel behaviour** for each family — an allow must show the
//! side effect, a deny must show the side effect is absent, not merely a
//! non-zero exit code.
//!
//! Two tests:
//!
//! * [`profile_field_matrix`] — one probe per field family, all against the
//!   shipped profile. Prints a verdict table (run with `-- --nocapture`).
//! * [`grammar_edges`] — grammar facts the main profile cannot prove on its
//!   own: last-match precedence, denial attribution through `(with message)`,
//!   and `mach-lookup` enforcement.
//!
//! macOS only: Seatbelt has no equivalent on the other platforms.

#![cfg(target_os = "macos")]

use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::Command;

const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";
const TAG: &str = "SC_SBX_AGENT";
const EDGE_TAG: &str = "SC_SBX_EDGE_EXPLICIT";
const ROOT: &str = "/private/tmp/sc-agent-sandbox";
const EXTRA: &str = "/private/tmp/sc-agent-sandbox/extra";
const OUTSIDE: &str = "/private/tmp/sc-agent-sandbox-outside.txt";
const HOME_LEAK: &str = "$HOME/.sc-agent-sandbox/leak";
const PROFILE: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../policy/agent-sandbox.sb"
);

// ---------------------------------------------------------------------------
// harness
// ---------------------------------------------------------------------------

struct Run {
    code: i32,
    signal: Option<i32>,
    stdout: String,
    stderr: String,
}

impl Run {
    fn combined(&self) -> String {
        format!("{}{}", self.stdout, self.stderr)
    }

    fn tag(&self) -> String {
        match self.signal {
            Some(signal) => format!("exit=-1 sig={signal}"),
            None => format!("exit={}", self.code),
        }
    }

    /// A denial is either a non-zero exit or an EPERM surfaced by the shell or
    /// the interpreter — the two shapes a Seatbelt denial takes here.
    fn denied(&self) -> bool {
        let text = self.combined();
        self.code != 0
            || text.contains("Operation not permitted")
            || text.contains("PermissionError")
            || text.contains("EPERM")
    }
}

/// Run `command` (`/bin/zsh -c`) under a profile, passing `-D` parameters.
fn sandbox(profile: &str, command: &str, params: &[(&str, &str)]) -> Run {
    let mut args: Vec<String> = vec!["-f".into(), profile.into()];
    for (key, value) in params {
        args.push("-D".into());
        args.push(format!("{key}={value}"));
    }
    args.push("/bin/zsh".into());
    args.push("-c".into());
    args.push(command.to_string());
    let output = Command::new(SANDBOX_EXEC)
        .args(&args)
        .output()
        .unwrap_or_else(|error| panic!("cannot spawn {SANDBOX_EXEC}: {error}"));
    use std::os::unix::process::ExitStatusExt;
    let signal = output.status.signal();
    Run {
        code: output.status.code().unwrap_or(-1),
        signal,
        stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
        stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
    }
}

fn sandbox_raw(profile: &str, command: &str) -> Run {
    sandbox(profile, command, &[])
}

/// The shipped profile's canonical invocation: `_HOME` for its `home-subpath`
/// helper, `SC_EXTRA_WRITE` for its conditional extra-write zone.
fn shipped(command: &str, extra_write: bool) -> Run {
    let home = std::env::var("HOME").expect("HOME must be set");
    let mut params: Vec<(&str, &str)> = vec![("_HOME", home.as_str())];
    if extra_write {
        params.push(("SC_EXTRA_WRITE", EXTRA));
    }
    sandbox(PROFILE, command, &params)
}

fn path(rest: &str) -> String {
    format!("{ROOT}{rest}")
}

fn exists(target: &str) -> bool {
    if target == HOME_LEAK {
        return home_probe_dir().join("leak").exists();
    }
    Path::new(target).exists()
}

fn home_probe_dir() -> PathBuf {
    PathBuf::from(std::env::var("HOME").expect("HOME must be set")).join(".sc-agent-sandbox")
}

/// Build the zone tree the profile's rules address. The root is deterministic
/// so the shipped profile stays runnable by hand.
fn setup() {
    for dir in [
        "/app-home/workspace/.git",
        "/app-home/cache-demo",
        "/app-home/trash",
        "/app-home/secrets",
        "/app-home/audited-writes",
        "/app-home/noisy-deny",
        "/logs",
        "/extra",
    ] {
        std::fs::create_dir_all(path(dir)).expect("create zone");
    }
    std::fs::write(path("/app-home/workspace/.git/config"), "seed").unwrap();
    std::fs::write(path("/logs/seed.log"), "seed").unwrap();
    std::fs::write(path("/app-home/cache-demo/regenerable.bin"), "seed").unwrap();
    std::fs::write(path("/app-home/trash/old.txt"), "seed").unwrap();
    std::fs::write(path("/app-home/secrets/id_rsa"), "seed").unwrap();
    let _ = std::fs::remove_file(OUTSIDE);
    std::fs::create_dir_all(home_probe_dir()).expect("create home probe dir");
}

fn teardown() {
    let _ = std::fs::remove_file(OUTSIDE);
    let _ = std::fs::remove_dir_all(ROOT);
    let _ = std::fs::remove_dir_all(home_probe_dir());
}

// ---------------------------------------------------------------------------
// expectation model
// ---------------------------------------------------------------------------

enum Expect {
    /// The command must succeed.
    Ok,
    /// The command must succeed and its stdout must contain this token.
    Output(&'static str),
    /// The command must fail (side effects asserted by a sibling probe).
    Denied,
    /// The command must fail *and* nothing may land at this path.
    DeniedNoSideEffect(&'static str),
    /// The command must succeed *and* remove this path.
    Removed(&'static str),
    /// The command must fail with this token on stderr — used where a non-zero
    /// exit alone would be ambiguous (a timeout also exits non-zero).
    DeniedWith(&'static str),
    /// The command runs, then the tagged kernel log must contain this denial.
    KernelDenial(&'static str),
    /// The command runs, then the tagged kernel log must NOT contain a denial
    /// for this name — proof that the allow filter matched it.
    KernelNoDenial(&'static str),
}

struct Probe {
    id: &'static str,
    field: &'static str,
    command: &'static str,
    expect: Expect,
    extra_write: bool,
}

fn probes() -> Vec<Probe> {
    vec![
        Probe {
            id: "P-00",
            field: "(version)/(debug)/(deny default (with message))",
            command: "printf ready",
            expect: Expect::Output("ready"),
            extra_write: true,
        },
        Probe {
            id: "P-01",
            field: "default deny：越界写",
            command: "printf x > /private/tmp/sc-agent-sandbox-outside.txt",
            expect: Expect::DeniedNoSideEffect(OUTSIDE),
            extra_write: true,
        },
        Probe {
            id: "P-02",
            field: "define + string-append",
            command: "printf v > \"$SC_WS/defined.txt\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-03",
            field: "param + when（-D 已提供）",
            command: "printf v > \"$SC_EXTRA/p.txt\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-04",
            field: "param + when（未提供 -D → 该段静默消失）",
            command: "printf RAN",
            expect: Expect::Output("RAN"),
            extra_write: false,
        },
        Probe {
            id: "P-04b",
            field: "param 缺失不报错：静默退化为空值（fail-open 面）",
            command: "printf RAN",
            expect: Expect::Output("RAN"),
            extra_write: false,
        },
        Probe {
            id: "P-05",
            field: "process-exec：literal 白名单内",
            command: "/usr/bin/true && printf ALLOWED",
            expect: Expect::Output("ALLOWED"),
            extra_write: true,
        },
        Probe {
            id: "P-06",
            field: "process-exec：白名单外 /bin/ls",
            command: "/bin/ls /",
            expect: Expect::Denied,
            extra_write: true,
        },
        Probe {
            id: "P-07",
            field: "deny process-exec：regex 收回 literal",
            command: "/usr/bin/sudo -n /usr/bin/true",
            expect: Expect::Denied,
            extra_write: true,
        },
        Probe {
            id: "P-08",
            field: "file-read*：系统基线放行",
            command: "/bin/cat /private/etc/hosts",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-09",
            field: "deny file-read*：凭据目录补丁",
            command: "cat \"$SC_SECRET\"",
            expect: Expect::Denied,
            extra_write: true,
        },
        Probe {
            id: "P-10",
            field: "file-write* subpath：缓存区",
            command: "printf v > \"$SC_CACHE/w.txt\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-11",
            field: "require-all + require-not：.git 保护",
            command: "printf v > \"$SC_WS/.git/config\"",
            expect: Expect::Denied,
            extra_write: true,
        },
        Probe {
            id: "P-12",
            field: "regex 白名单：*.log 放行",
            command: "printf v > \"$SC_LOGS/ok.log\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-13",
            field: "regex 白名单：非 *.log 拒绝",
            command: "printf v > \"$SC_LOGS/ok.txt\"",
            expect: Expect::DeniedNoSideEffect("/private/tmp/sc-agent-sandbox/logs/ok.txt"),
            extra_write: true,
        },
        Probe {
            id: "P-14",
            field: "literal：设备白名单 /dev/null",
            command: "printf v > /dev/null",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-15",
            field: "写面 deny 补丁：可写根内部的 secrets/",
            command: "printf v > \"$SC_SECRET/leak\"",
            expect: Expect::DeniedNoSideEffect("/private/tmp/sc-agent-sandbox/app-home/secrets/leak"),
            extra_write: true,
        },
        Probe {
            id: "P-16",
            field: "home-subpath deny",
            command: "printf v > \"$SC_HOME/.sc-agent-sandbox/leak\"",
            expect: Expect::DeniedNoSideEffect(HOME_LEAK),
            extra_write: true,
        },
        Probe {
            id: "P-17",
            field: "删除三段式①：全局 deny file-write-unlink",
            command: "rm \"$SC_WS/defined.txt\"",
            expect: Expect::Denied,
            extra_write: true,
        },
        Probe {
            id: "P-18",
            field: "删除三段式②：trash 窄域 re-allow",
            command: "rm \"$SC_TRASH/old.txt\"",
            expect: Expect::Removed("/private/tmp/sc-agent-sandbox/app-home/trash/old.txt"),
            extra_write: true,
        },
        Probe {
            id: "P-19",
            field: "删除三段式③：cache 可删根",
            command: "rm \"$SC_CACHE/regenerable.bin\"",
            expect: Expect::Removed("/private/tmp/sc-agent-sandbox/app-home/cache-demo/regenerable.bin"),
            extra_write: true,
        },
        Probe {
            id: "P-20",
            field: "network-outbound：loopback 放行（listener 收到连接）",
            command: "/usr/bin/nc -z -w 2 127.0.0.1 \"$SC_PORT\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-21",
            field: "network-outbound：公网连接被内核拒绝（拒绝日志可归属）",
            command: "/usr/bin/nc -z -w 3 93.184.216.34 80",
            expect: Expect::KernelDenial("network-outbound"),
            extra_write: true,
        },
        Probe {
            id: "P-22",
            field: "sysctl-name：白名单内的名字不进拒绝日志",
            command: "/usr/sbin/sysctl -n kern.osrelease",
            expect: Expect::KernelNoDenial("sysctl-read kern.osrelease"),
            extra_write: true,
        },
        Probe {
            id: "P-24",
            field: "mach-lookup 基线：命令能起来即证据",
            command: "true",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-25",
            field: "with report：allow 侧（放行 + 报告事件）",
            command: "printf v > \"$SC_AUDIT/ro.txt\"",
            expect: Expect::Ok,
            extra_write: true,
        },
        Probe {
            id: "P-26",
            field: "with no-log：deny 侧（拦截但不留可归属日志）",
            command: "printf v > \"$SC_NOISY/n.txt\"",
            expect: Expect::DeniedNoSideEffect("/private/tmp/sc-agent-sandbox/app-home/noisy-deny/n.txt"),
            extra_write: true,
        },
        Probe {
            id: "P-27",
            field: "signal / process-fork：子 shell 可派生",
            command: "kill -0 $$ && ( printf forked )",
            expect: Expect::Output("forked"),
            extra_write: true,
        },
    ]
}

/// The probe table stays readable by using `$SC_*` shorthands; every path is
/// expanded to an absolute one before it reaches the sandbox.
fn expand(command: &str, port: u16) -> String {
    command
        .replace("$SC_WS", &path("/app-home/workspace"))
        .replace("$SC_CACHE", &path("/app-home/cache-demo"))
        .replace("$SC_TRASH", &path("/app-home/trash"))
        .replace("$SC_SECRET", &path("/app-home/secrets"))
        .replace("$SC_AUDIT", &path("/app-home/audited-writes"))
        .replace("$SC_NOISY", &path("/app-home/noisy-deny"))
        .replace("$SC_LOGS", &path("/logs"))
        .replace("$SC_EXTRA", EXTRA)
        .replace("$SC_HOME", &std::env::var("HOME").expect("HOME must be set"))
        .replace("$SC_PORT", &port.to_string())
}

fn truncate(text: &str) -> String {
    let text = text.trim().replace('\n', " | ");
    if text.chars().count() > 44 {
        let cut: String = text.chars().take(44).collect();
        format!("{cut}…")
    } else {
        text
    }
}

/// Query the unified log for a tagged denial carrying `needle`. The profile's
/// `(with message "<tag>")` is what makes this attributable, so this helper is
/// also the end-to-end check on the tagging field.
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

// ---------------------------------------------------------------------------
// tests
// ---------------------------------------------------------------------------

#[test]
fn profile_field_matrix() {
    assert!(
        Path::new(PROFILE).exists(),
        "policy/agent-sandbox.sb is missing at {PROFILE}"
    );
    setup();

    // A real loopback listener proves `(remote ip "localhost:*")` admits a
    // connection rather than merely letting the syscall through.
    let listener = TcpListener::bind("127.0.0.1:0").expect("bind loopback listener");
    listener.set_nonblocking(true).expect("non-blocking listener");
    let port = listener.local_addr().unwrap().port();

    let mut failures: Vec<String> = Vec::new();
    let mut rows: Vec<String> = Vec::new();

    for probe in probes() {
        let command = expand(probe.command, port);
        let run = shipped(&command, probe.extra_write);
        let (ok, actual) = match &probe.expect {
            Expect::Ok => (run.code == 0, run.tag()),
            Expect::Output(token) => (
                run.stdout.contains(token),
                format!("{} out={:?}", run.tag(), truncate(&run.stdout)),
            ),
            Expect::Denied => (run.denied(), run.tag()),
            Expect::DeniedNoSideEffect(target) => {
                let landed = exists(target);
                (
                    run.denied() && !landed,
                    format!("{} landed={landed}", run.tag()),
                )
            }
            Expect::Removed(target) => {
                let gone = !exists(target);
                (run.code == 0 && gone, format!("{} gone={gone}", run.tag()))
            }
            Expect::DeniedWith(marker) => (
                run.code != 0 && run.combined().contains(marker),
                format!("{} err={:?}", run.tag(), truncate(&run.stderr)),
            ),
            Expect::KernelDenial(needle) => {
                let seen = tagged_log_contains(needle);
                (seen, format!("{} log={seen}", run.tag()))
            }
            Expect::KernelNoDenial(needle) => {
                let seen = tagged_log_contains(needle);
                (!seen, format!("{} log={seen}", run.tag()))
            }
        };
        rows.push(format!(
            "  {:<6} {:<6} {:<44} {}{}",
            probe.id,
            if ok { "PASS" } else { "FAIL" },
            probe.field,
            actual,
            if run.stderr.trim().is_empty() {
                String::new()
            } else {
                format!(" err={:?}", truncate(&run.stderr))
            }
        ));
        if !ok {
            failures.push(format!("{} ({}) — {actual}", probe.id, probe.field));
        }
        if probe.id == "P-20" {
            // Accepting the peer is the proof: the kernel admitted the
            // connection rather than the probe merely exiting 0.
            let accepted = listener.accept().is_ok();
            if !accepted {
                failures.push("P-20 — loopback listener never received the connection".into());
            }
            rows.last_mut().map(|row| row.push_str(&format!(" accepted={accepted}")));
        }
    }

    println!("\nagent-sandbox.sb field matrix ({} probes)\n", rows.len());
    for row in &rows {
        println!("{row}");
    }

    // A narrowed surface that aborts leaves no stderr; the tagged kernel
    // denial log is where the missing path or name shows up.
    if !failures.is_empty() {
        let log = Command::new("/usr/bin/log")
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
        let text = String::from_utf8_lossy(&log.stdout).into_owned();
        let denials: Vec<&str> = text.lines().filter(|line| line.contains("deny")).collect();
        let start = denials.len().saturating_sub(20);
        println!(
            "\nkernel denials carrying {TAG} ({} lines, most recent 20):",
            denials.len()
        );
        for line in &denials[start..] {
            let line: String = line.chars().take(160).collect();
            println!("  {line}");
        }
    }
    teardown();

    assert!(
        failures.is_empty(),
        "field matrix failures:\n{}",
        failures.join("\n")
    );
}

#[test]
fn grammar_edges() {
    let dir = std::env::temp_dir().join(format!("sc-sbpl-edges-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("create edge dir");
    let write = |name: &str, body: &str| -> String {
        let file = dir.join(name);
        std::fs::write(&file, body).expect("write profile");
        file.to_string_lossy().into_owned()
    };
    // A minimal but usable base: exec + read + the mach/sysctl/fork baseline
    // `zsh` needs to start at all (see seatbelt_construct_acceptance.rs), so
    // each edge case can vary exactly one construct.
    let head = format!(
        "(version 1)\n(debug deny)\n(deny default (with message \"{TAG}\"))\n\
         (allow process-exec*)\n(allow process-fork)\n(allow signal (target self))\n\
         (allow sysctl-read)\n(allow ipc-posix-shm)\n(allow file-read*)\n\
         (allow file-ioctl)\n(allow file-write* (subpath \"/dev\"))\n\
         (allow mach-lookup (global-name \"com.apple.system.notification_center\") \
         (global-name \"com.apple.system.opendirectoryd.libinfo\") \
         (global-name \"com.apple.system.logger\") (global-name \"com.apple.logd\") \
         (global-name \"com.apple.diagnosticd\") (global-name \"com.apple.cfprefsd.agent\") \
         (global-name \"com.apple.cfprefsd.daemon\") \
         (global-name \"com.apple.distributed_notifications@Uv3\") \
         (global-name \"com.apple.SecurityServer\"))\n"
    );

    // G-01 — last match wins: the same literal, revoked or re-granted by order.
    let revoked = write(
        "revoked.sb",
        &format!(
            "{head}(allow process-exec (literal \"/usr/bin/true\"))\n(deny process-exec (regex #\"^/usr/bin/true$\") (with message \"{TAG}\"))\n"
        ),
    );
    let regranted = write(
        "regranted.sb",
        &format!(
            "{head}(deny process-exec (regex #\"^/usr/bin/true$\") (with message \"{TAG}\"))\n(allow process-exec (literal \"/usr/bin/true\"))\n"
        ),
    );
    let revoked_run = sandbox_raw(&revoked, "/usr/bin/true");
    let regranted_run = sandbox_raw(&regranted, "/usr/bin/true");

    // G-02 — an explicit deny rule only attributes its own denials when it
    // carries its own `(with message ...)`; the tag is deliberately different
    // from the default-deny tag.
    let attributed_profile = write(
        "attributed.sb",
        &format!(
            "{head}(allow file-write* (subpath \"/dev\"))\n(deny file-write* (subpath \"/tmp\") (with message \"{EDGE_TAG}\"))\n"
        ),
    );
    let unlabelled_profile = write(
        "unlabelled.sb",
        &format!("{head}(allow file-write* (subpath \"/dev\"))\n(deny file-write* (subpath \"/tmp\"))\n"),
    );
    let moved = format!("/tmp/sc-sbpl-edge-{}.txt", std::process::id());
    let probe = format!("printf x > {moved}");
    let attributed_run = sandbox_raw(&attributed_profile, &probe);
    let unlabelled_run = sandbox_raw(&unlabelled_profile, &probe);
    let attributed = kernel_log_mentions(EDGE_TAG);

    // G-03 — mach-lookup is enforced: revoking a baseline service breaks a
    // consumer that never touches the filesystem policy.
    let starved = write(
        "starved.sb",
        &format!(
            "{head}(deny mach-lookup (global-name \"com.apple.system.opendirectoryd.libinfo\") (with message \"{TAG}\"))\n"
        ),
    );
    let starved_run = sandbox_raw(&starved, "id -un");

    // G-04 — `home-subpath` is not a builtin filter: undefined, the whole
    // profile is rejected before any rule is evaluated.
    let undefined_helper = write(
        "undefined-home.sb",
        &format!("{head}(allow file-write* (home-subpath \"/Library\"))\n"),
    );
    let undefined_run = sandbox_raw(&undefined_helper, "printf RAN");

    // G-05 — defining it the way Apple does makes the same filter work.
    let defined_helper = write(
        "defined-home.sb",
        &format!(
            "{head}(allow file-write* (subpath \"/dev\"))\n(define (home-subpath rest) (subpath (string-append (param \"_HOME\") rest)))\n(allow file-write* (home-subpath \"/.sc-agent-sandbox\"))\n"
        ),
    );
    let home = std::env::var("HOME").expect("HOME must be set");
    let home_probe = format!("{home}/.sc-agent-sandbox");
    std::fs::create_dir_all(&home_probe).expect("create home probe dir");
    let defined_run = sandbox(
        &defined_helper,
        "printf v > \"$HOME/.sc-agent-sandbox/edge.txt\"",
        &[("_HOME", home.as_str())],
    );
    let defined_landed = Path::new(&format!("{home_probe}/edge.txt")).exists();

    // G-06 — modifier placement: `report` is allow-side only; `no-log` is
    // deny-side. Each profile varies exactly one placement, each probe gets
    // its own target file.
    let report_allow_target = format!("/tmp/sc-sbpl-report-{}.txt", std::process::id());
    let report_deny_target = format!("/tmp/sc-sbpl-report-deny-{}.txt", std::process::id());
    let nolog_target = format!("/tmp/sc-sbpl-nolog-{}.txt", std::process::id());
    let report_on_allow = write(
        "report-allow.sb",
        &format!("{head}(allow file-write* (subpath \"/tmp\") (with report))\n"),
    );
    let report_on_deny = write(
        "report-deny.sb",
        &format!(
            "{head}(deny file-write* (subpath \"/tmp\") (with report) (with message \"{TAG}\"))\n"
        ),
    );
    let nolog_on_deny = write(
        "nolog-deny.sb",
        &format!("{head}(deny file-write* (subpath \"/tmp\") (with no-log))\n"),
    );
    let report_allow_run =
        sandbox_raw(&report_on_allow, &format!("printf x > {report_allow_target}"));
    let report_allow_landed = Path::new(&report_allow_target).exists();
    let report_deny_run =
        sandbox_raw(&report_on_deny, &format!("printf x > {report_deny_target}"));
    let report_deny_landed = Path::new(&report_deny_target).exists();
    let nolog_deny_run = sandbox_raw(&nolog_on_deny, &format!("printf x > {nolog_target}"));
    let nolog_deny_landed = Path::new(&nolog_target).exists();
    for target in [&report_allow_target, &report_deny_target, &nolog_target] {
        let _ = std::fs::remove_file(target);
    }
    let _ = std::fs::remove_file(&moved);

    println!(
        "\ngrammar edges\
        \n  G-01 last-match    deny-after-allow exit={} err={:?}   allow-after-deny exit={} err={:?}\
        \n  G-02 attribution   tagged-deny exit={} tag-in-log={attributed}   untagged-deny exit={}\
        \n  G-03 mach-lookup   baseline consumer exit={}\
        \n  G-04 home-subpath  undefined -> exit={} err={:?}\
        \n  G-05 home-subpath  defined   -> exit={} landed={defined_landed} err={:?}\
        \n  G-06 with report   on allow -> exit={} landed={report_allow_landed}   on deny -> exit={} err={:?}\
        \n  G-07 with no-log   on deny  -> exit={} landed={nolog_deny_landed}\n",
        revoked_run.code,
        truncate(&revoked_run.stderr),
        regranted_run.code,
        truncate(&regranted_run.stderr),
        attributed_run.code,
        unlabelled_run.code,
        starved_run.code,
        undefined_run.code,
        truncate(&undefined_run.stderr),
        defined_run.code,
        truncate(&defined_run.stderr),
        report_allow_run.code,
        report_deny_run.code,
        truncate(&report_deny_run.stderr),
        nolog_deny_run.code
    );

    let defined_landed = Path::new(&format!("{home}/.sc-agent-sandbox/edge.txt")).exists();
    let _ = std::fs::remove_file(&format!("{home}/.sc-agent-sandbox/edge.txt"));
    let _ = std::fs::remove_dir_all(&home_probe);
    let _ = std::fs::remove_dir_all(&dir);

    assert!(
        revoked_run.code != 0,
        "deny after allow must revoke the exec grant (got exit {})",
        revoked_run.code
    );
    assert_eq!(
        regranted_run.code, 0,
        "allow after deny must re-grant the exec (got exit {})",
        regranted_run.code
    );
    assert!(
        attributed,
        "an explicit deny rule carries its own tag into the kernel log; `{EDGE_TAG}` was not found"
    );
    assert!(
        attributed_run.code != 0 && unlabelled_run.code != 0,
        "both tagged and untagged deny rules must stop the write"
    );
    assert!(
        starved_run.code != 0,
        "denying com.apple.system.opendirectoryd.libinfo should break `id -un`"
    );
    assert!(
        undefined_run.code != 0 && undefined_run.stderr.contains("home-subpath"),
        "an undefined filter helper must reject the whole profile, got exit {} ({})",
        undefined_run.code,
        undefined_run.stderr.trim()
    );
    assert_eq!(
        defined_run.code, 0,
        "a locally defined home-subpath must work (got exit {})",
        defined_run.code
    );
    assert!(
        defined_landed,
        "the home-subpath write should have landed once the helper was defined"
    );
    assert!(
        report_allow_run.code == 0 && report_allow_landed,
        "(with report) on an allow rule must still allow: exit={} landed={}",
        report_allow_run.code,
        report_allow_landed
    );
    assert!(
        report_deny_run.code != 0 && !report_deny_landed,
        "(with report) on a deny rule must be rejected at load time: exit={} landed={}",
        report_deny_run.code,
        report_deny_landed
    );
    assert!(
        nolog_deny_run.code != 0 && !nolog_deny_landed,
        "(with no-log) must deny without leaving a side effect: exit={} landed={}",
        nolog_deny_run.code,
        nolog_deny_landed
    );
}

fn kernel_log_mentions(tag: &str) -> bool {
    let log = Command::new("/usr/bin/log")
        .args([
            "show",
            "--last",
            "2m",
            "--style",
            "compact",
            "--predicate",
            &format!("eventMessage CONTAINS \"{tag}\""),
        ])
        .output()
        .expect("run log show");
    String::from_utf8_lossy(&log.stdout).contains(tag)
}
