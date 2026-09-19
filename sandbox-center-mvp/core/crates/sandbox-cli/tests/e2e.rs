//! End-to-end tests: a real `sandbox-center`, a real `sandbox-cli` and real
//! `/usr/bin/sandbox-exec` enforcement (macOS only).
//!
//! These cover the full tool-call loop — policy resolution, Seatbelt
//! enforcement, kernel-denial reclamation, auto-grant decisions, the retry and
//! the audit chain — without mocking any process boundary.

#![cfg(target_os = "macos")]

use std::io::{BufRead, BufReader, Read};
use std::path::PathBuf;
use std::process::{Child, Command, Output, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{Duration, Instant};

static HARNESS_SEQ: AtomicU64 = AtomicU64::new(0);

const CLI: &str = env!("CARGO_BIN_EXE_sandbox-cli");
const CENTER_NAME: &str = "sandbox-center";

/// `sandbox-center` ships in a sibling crate, so Cargo does not export a
/// `CARGO_BIN_EXE_` variable for it. Both binaries land in the same directory.
fn center_binary() -> PathBuf {
    let cli = PathBuf::from(CLI);
    let path = cli
        .parent()
        .expect("sandbox-cli lives in a target directory")
        .join(CENTER_NAME);
    assert!(
        path.exists(),
        "{} is missing — run `cargo build --workspace` before the e2e tests",
        path.display()
    );
    path
}
const POLICY: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../policy/default-policy.json"
);
const STRICT_POLICY: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../policy/strict-policy.json"
);
const WORKBUDDY_POLICY: &str = concat!(
    env!("CARGO_MANIFEST_DIR"),
    "/../../../policy/workbuddy-policy.json"
);

struct Harness {
    root: PathBuf,
    app_home: PathBuf,
    workspace: PathBuf,
    socket: PathBuf,
    center: Child,
}

impl Harness {
    fn start(policy: &str) -> Self {
        Self::start_with(policy, true)
    }

    fn start_with(policy: &str, auto_grant: bool) -> Self {
        let root = std::env::temp_dir().join(format!(
            "sc-e2e-{}-{}",
            std::process::id(),
            HARNESS_SEQ.fetch_add(1, Ordering::SeqCst)
        ));
        let app_home = root.join("app-home");
        let workspace = root.join("workspace");
        let socket = root.join("center.sock");
        std::fs::create_dir_all(workspace.join(".sc-trash")).unwrap();
        std::fs::create_dir_all(app_home.join("cache-demo")).unwrap();
        std::fs::create_dir_all(app_home.join("trash")).unwrap();

        let child = Command::new(center_binary())
            .arg("--app-home")
            .arg(&app_home)
            .arg("--policy")
            .arg(policy)
            .arg("--workspace")
            .arg(&workspace)
            .arg("--socket")
            .arg(&socket)
            .arg("--quiet")
            .arg("--auto-grant")
            .arg(if auto_grant { "true" } else { "false" })
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .expect("failed to spawn sandbox-center");

        let mut harness = Self {
            root,
            app_home,
            workspace,
            socket,
            center: child,
        };
        harness.wait_until_ready();
        harness
    }

    fn wait_until_ready(&mut self) {
        let stdout = self.center.stdout.take().expect("center stdout");
        let mut reader = BufReader::new(stdout);
        let mut line = String::new();
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            line.clear();
            let read = reader.read_line(&mut line).unwrap_or(0);
            if read > 0 {
                let value: serde_json::Value =
                    serde_json::from_str(line.trim()).unwrap_or(serde_json::Value::Null);
                if value["event"] == "ready" {
                    // Keep draining the pipe so the child never blocks on it.
                    std::thread::spawn(move || {
                        let mut sink = String::new();
                        let _ = reader.read_to_string(&mut sink);
                    });
                    return;
                }
                continue;
            }
            if let Ok(Some(status)) = self.center.try_wait() {
                let mut stderr = String::new();
                if let Some(mut pipe) = self.center.stderr.take() {
                    let _ = pipe.read_to_string(&mut stderr);
                }
                panic!("sandbox-center exited early ({status}): {stderr}");
            }
            assert!(
                Instant::now() < deadline,
                "sandbox-center did not report readiness"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }

    /// Run a shell command inside the sandbox and capture the result.
    fn run(&self, command: &str) -> CliRun {
        self.run_with(&[], command)
    }

    fn run_with(&self, extra: &[&str], command: &str) -> CliRun {
        let mut args: Vec<String> = vec![
            "--socket".into(),
            self.socket.to_string_lossy().into_owned(),
            "--cwd".into(),
            self.workspace.to_string_lossy().into_owned(),
        ];
        args.extend(extra.iter().map(|value| value.to_string()));
        args.push("--".into());
        args.push("/bin/zsh".into());
        args.push("-c".into());
        args.push(command.to_string());

        let output = Command::new(CLI)
            .args(&args)
            .output()
            .expect("failed to run sandbox-cli");
        CliRun::from(output)
    }

    fn audit_text(&self) -> String {
        let dir = self.app_home.join("audit");
        let mut text = String::new();
        for entry in std::fs::read_dir(&dir).expect("audit dir") {
            let path = entry.expect("audit entry").path();
            if path.extension().and_then(|value| value.to_str()) == Some("jsonl") {
                text.push_str(&std::fs::read_to_string(&path).unwrap_or_default());
            }
        }
        text
    }

    fn audit_files(&self) -> Vec<PathBuf> {
        std::fs::read_dir(self.app_home.join("audit"))
            .expect("audit dir")
            .filter_map(|entry| entry.ok().map(|entry| entry.path()))
            .filter(|path| path.extension().and_then(|value| value.to_str()) == Some("jsonl"))
            .collect()
    }
}

impl Drop for Harness {
    fn drop(&mut self) {
        let _ = self.center.kill();
        let _ = self.center.wait();
        let _ = std::fs::remove_dir_all(&self.root);
    }
}

struct CliRun {
    exit_code: i32,
    stdout: String,
    stderr: String,
}

impl CliRun {
    fn from(output: Output) -> Self {
        Self {
            exit_code: output.status.code().unwrap_or(-1),
            stdout: String::from_utf8_lossy(&output.stdout).into_owned(),
            stderr: String::from_utf8_lossy(&output.stderr).into_owned(),
        }
    }

    fn events(&self) -> Vec<serde_json::Value> {
        self.stderr
            .lines()
            .filter_map(|line| line.trim().strip_prefix("SC_EVENT|"))
            .filter_map(|payload| serde_json::from_str(payload).ok())
            .collect()
    }

    fn event_of(&self, kind: &str) -> Option<serde_json::Value> {
        self.events()
            .into_iter()
            .find(|event| event["type"] == kind)
    }

    fn require_event(&self, kind: &str) -> serde_json::Value {
        self.event_of(kind)
            .unwrap_or_else(|| panic!("no `{kind}` event in:\n{}", self.stderr))
    }
}

#[test]
fn seatbelt_is_usable_in_this_environment() {
    let output = Command::new(CLI)
        .arg("--probe")
        .output()
        .expect("failed to run sandbox-cli --probe");
    assert!(
        output.status.success(),
        "sandbox-exec unusable: {}",
        String::from_utf8_lossy(&output.stderr)
    );
    assert!(String::from_utf8_lossy(&output.stdout).contains("seatbelt-ok"));
}

#[test]
fn write_inside_the_workspace_is_allowed() {
    let harness = Harness::start(POLICY);
    let run = harness.run("echo hello > inside.txt && cat inside.txt");
    assert_eq!(run.exit_code, 0, "stderr: {}", run.stderr);
    assert!(run.stdout.contains("hello"));
    assert!(harness.workspace.join("inside.txt").exists());
}

#[test]
fn write_outside_the_workspace_is_denied_and_audited() {
    let harness = Harness::start(POLICY);
    let escape = std::env::var("HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|_| PathBuf::from("/tmp"))
        .join(format!(".sc-e2e-escape-{}", std::process::id()));
    let _ = std::fs::remove_file(&escape);

    let run = harness.run(&format!("echo pwned > {}", escape.display()));
    assert_ne!(
        run.exit_code, 0,
        "write escaped the sandbox: {}",
        run.stdout
    );
    assert!(
        !escape.exists(),
        "the sandbox let a write outside the workspace through"
    );

    let violation = run
        .event_of("violation")
        .expect("no violation event was emitted");
    assert_eq!(violation["class"], "write");
    assert!(violation["target"]
        .as_str()
        .unwrap()
        .contains(".sc-e2e-escape"));

    let audit = harness.audit_text();
    assert!(audit.contains("violation.denied"), "audit: {audit}");
    assert!(audit.contains(".sc-e2e-escape"));
    let _ = std::fs::remove_file(&escape);
}

#[test]
fn delete_inside_the_workspace_is_denied() {
    let harness = Harness::start(POLICY);
    let target = harness.workspace.join("precious.txt");
    std::fs::write(&target, "keep me").unwrap();

    let run = harness.run("rm precious.txt");
    assert_ne!(run.exit_code, 0);
    assert!(target.exists(), "delete protection did not hold");
    let violation = run.require_event("violation");
    assert_eq!(violation["class"], "delete");
    assert_eq!(violation["operation"], "file-write-unlink");
}

#[test]
fn delete_in_the_trash_root_is_allowed() {
    let harness = Harness::start(POLICY);
    std::fs::write(harness.workspace.join(".sc-trash/gone.txt"), "bye").unwrap();
    let run = harness.run("rm .sc-trash/gone.txt");
    assert_eq!(run.exit_code, 0, "stderr: {}", run.stderr);
    assert!(!harness.workspace.join(".sc-trash/gone.txt").exists());
    assert!(run.event_of("violation").is_none());
}

#[test]
fn auto_grant_rewrites_the_profile_and_the_retry_succeeds() {
    let harness = Harness::start(POLICY);
    let cached = harness.app_home.join("cache-demo/blob.bin");
    std::fs::write(&cached, "regenerable").unwrap();

    let run = harness.run(&format!("rm {}", cached.display()));
    assert_eq!(run.exit_code, 0, "stderr: {}", run.stderr);
    assert!(!cached.exists(), "the retry never deleted the cached blob");

    let grant = run.require_event("grant");
    assert!(grant["root"].as_str().unwrap().ends_with("cache-demo"));
    assert!(grant["reason"].as_str().unwrap().contains("cache"));
    assert_eq!(run.require_event("retry")["attempt"], 2);

    let summary = run.require_event("session.closed");
    assert_eq!(summary["retried"], true);
    assert_eq!(summary["grants"], 1);

    let audit = harness.audit_text();
    assert!(audit.contains("grant.auto"), "audit: {audit}");
}

#[test]
fn auto_grant_can_be_disabled_by_the_center() {
    let harness = Harness::start_with(POLICY, false);
    let cached = harness.app_home.join("cache-demo/blob.bin");
    std::fs::write(&cached, "regenerable").unwrap();

    let run = harness.run(&format!("rm {}", cached.display()));
    assert_ne!(run.exit_code, 0);
    assert!(cached.exists(), "auto-grant ran although it was disabled");
    assert!(run.event_of("grant").is_none());
}

#[test]
fn public_network_is_denied_while_loopback_is_allowed() {
    let harness = Harness::start(POLICY);

    let public = harness.run("/usr/bin/nc -w 2 1.1.1.1 80 </dev/null");
    assert_ne!(public.exit_code, 0, "public network egress was not blocked");
    let violation = public.require_event("violation");
    assert_eq!(violation["class"], "network");

    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming().take(4) {
            drop(stream);
        }
    });
    let loopback = harness.run(&format!("/usr/bin/nc -w 2 127.0.0.1 {port} </dev/null"));
    assert_eq!(
        loopback.exit_code, 0,
        "loopback denied: {}",
        loopback.stderr
    );
}

#[test]
fn interpreters_run_inside_the_sandbox() {
    let harness = Harness::start(POLICY);
    let run = harness.run("python3 -c 'print(6*7)'; node -e 'console.log(6*7)'");
    assert_eq!(run.exit_code, 0, "stderr: {}", run.stderr);
    assert_eq!(
        run.stdout.matches("42").count(),
        2,
        "stdout: {}",
        run.stdout
    );
}

#[test]
fn strict_policy_denies_writes_outside_and_all_network() {
    let harness = Harness::start(STRICT_POLICY);
    let inside = harness.run("echo ok > strict.txt");
    assert_eq!(inside.exit_code, 0, "stderr: {}", inside.stderr);

    let outside = harness.run("echo nope > /private/etc/sc-e2e-probe.txt");
    assert_ne!(outside.exit_code, 0);

    let loopback = harness.run("/usr/bin/nc -w 2 127.0.0.1 9 </dev/null");
    assert_ne!(
        loopback.exit_code, 0,
        "strict policy must deny even loopback"
    );
}

#[test]
fn workbuddy_style_policy_allows_global_writes_but_protects_deletes() {
    let harness = Harness::start(WORKBUDDY_POLICY);
    let outside = harness.root.join("outside.txt");

    let write = harness.run(&format!("echo ok > {}", outside.display()));
    assert_eq!(write.exit_code, 0, "stderr: {}", write.stderr);
    assert!(outside.exists());

    let target = harness.workspace.join("protected.txt");
    std::fs::write(&target, "keep").unwrap();
    let delete = harness.run("rm protected.txt");
    assert_ne!(delete.exit_code, 0, "delete protection did not hold");
    assert!(target.exists());
}

#[test]
fn audit_chain_verifies_and_tampering_is_detected() {
    let harness = Harness::start(POLICY);
    harness.run("echo audit > audit.txt");
    harness.run("rm audit.txt");

    let files = harness.audit_files();
    assert_eq!(files.len(), 1, "expected exactly one audit file");
    let audit = &files[0];

    let verify = Command::new(center_binary())
        .arg("--verify-audit")
        .arg(audit)
        .output()
        .expect("verify-audit");
    assert!(
        verify.status.success(),
        "audit chain did not verify: {}",
        String::from_utf8_lossy(&verify.stderr)
    );

    let tampered = harness.root.join("tampered.jsonl");
    let text = std::fs::read_to_string(audit).unwrap();
    std::fs::write(
        &tampered,
        text.replacen("\"exit_code\":0", "\"exit_code\":7", 1),
    )
    .unwrap();
    let verify = Command::new(center_binary())
        .arg("--verify-audit")
        .arg(&tampered)
        .output()
        .expect("verify-audit");
    assert!(!verify.status.success(), "tampering went unnoticed");
    assert!(String::from_utf8_lossy(&verify.stderr).contains("hash mismatch"));
}

#[test]
fn print_profile_reflects_the_centers_policy() {
    let harness = Harness::start(POLICY);
    let output = Command::new(CLI)
        .arg("--socket")
        .arg(&harness.socket)
        .arg("--cwd")
        .arg(&harness.workspace)
        .arg("--print-profile")
        .output()
        .expect("print-profile");
    assert!(output.status.success());
    let profile = String::from_utf8_lossy(&output.stdout);
    assert!(profile.contains("(deny default (with message \"SC_SBX_"));
    assert!(profile.contains("(deny file-write-unlink (subpath \"/\") (with message"));
    assert!(profile.contains("(allow network-outbound (remote ip \"localhost:*\"))"));
    assert!(profile.contains(harness.workspace.canonicalize().unwrap().to_str().unwrap()));
}
