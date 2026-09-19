//! `sandbox-cli` — the one-shot, per-tool-call sandbox entry point.
//!
//! Lifecycle of a single tool call:
//!
//! ```text
//! sandbox-cli ──open_session──▶ sandbox-center        (policy + unique tag)
//!      │                        ◀── effective policy
//!      ├─ materialise SBPL profile ($TMPDIR/sandbox-center-mvp/<tag>.sb)
//!      ├─ spawn /usr/bin/sandbox-exec -f <profile> <command>     ← enforcement
//!      ├─ reclaim denials from the unified log (tag-scoped)
//!      ├─ ──report_violation──▶ sandbox-center            ← allow / auto-grant
//!      ├─ (once) re-run with the granted roots if the center said grant
//!      └─ ──close_session──▶ sandbox-center               (sealed audit record)
//! ```
//!
//! Structured events are written to stderr as `SC_EVENT|<json>` lines; the
//! command's own stdout/stderr pass through untouched.

mod events;
mod reclaim;

use sandbox_core::ipc::Channel;
use sandbox_core::policy::OperationClass;
use sandbox_core::protocol::{
    ClientInfo, CloseSessionRequest, OpenSessionRequest, OpenSessionResult, Request, Response,
};
use sandbox_core::seatbelt::{self, ProfileRequest};
use sandbox_core::violation::{self, Violation};
use sandbox_core::VERSION;
use serde::de::DeserializeOwned;
use serde_json::json;
use std::collections::HashSet;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::process::ExitStatusExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

const SANDBOX_EXEC: &str = "/usr/bin/sandbox-exec";
const NESTED_SANDBOX_MARKER: &str = "sandbox_apply: Operation not permitted";
const RPC_TIMEOUT: Duration = Duration::from_secs(15);

struct Args {
    socket: Option<PathBuf>,
    center_info: Option<PathBuf>,
    app_home: PathBuf,
    cwd: Option<PathBuf>,
    workspace: Option<PathBuf>,
    retry_on_grant: bool,
    profile_file: Option<PathBuf>,
    print_profile: bool,
    report_all: bool,
    violation_scan: bool,
    probe: bool,
    quiet_events: bool,
    argv: Vec<String>,
}

const USAGE: &str = "\
sandbox-cli — run one command inside a Seatbelt sandbox, decided by sandbox-center

USAGE:
  sandbox-cli [OPTIONS] -- <COMMAND> [ARGS...]

OPTIONS:
  --socket <PATH>        center socket (overrides discovery)
  --center-info <FILE>   center descriptor written by sandbox-center
  --app-home <DIR>       discovery root (default: $HOME/.sandbox-center-mvp)
  --cwd <DIR>            working directory of the command (default: current)
  --workspace <DIR>      ${WORKSPACE} value sent to the center (default: cwd)
  --no-retry-on-grant    do not re-run when the center auto-grants a denial
  --profile-file <PATH>  keep the generated profile at this path
  --print-profile        print the profile the center would enforce, then exit
  --report-all           report mach/sysctl denials too (noisy)
  --no-violation-scan    skip kernel-denial reclamation (saves the ~0.7s tail)
  --probe                check whether Seatbelt can be applied here, then exit
  --quiet-events         do not emit SC_EVENT lines on stderr
  --version              print the version
  --help                 print this help
";

impl Args {
    fn parse<I: Iterator<Item = String>>(mut argv: I) -> Result<Self, String> {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
        let mut args = Args {
            socket: None,
            center_info: None,
            app_home: PathBuf::from(format!("{home}/.sandbox-center-mvp")),
            cwd: None,
            workspace: None,
            retry_on_grant: true,
            profile_file: None,
            print_profile: false,
            report_all: false,
            violation_scan: true,
            probe: false,
            quiet_events: false,
            argv: Vec::new(),
        };
        let mut positional = false;
        while let Some(flag) = argv.next() {
            if positional {
                args.argv.push(flag);
                continue;
            }
            let mut value = |name: &str| -> Result<String, String> {
                argv.next()
                    .ok_or_else(|| format!("{name} requires a value"))
            };
            match flag.as_str() {
                "--" => positional = true,
                "--socket" => args.socket = Some(PathBuf::from(value("--socket")?)),
                "--center-info" => args.center_info = Some(PathBuf::from(value("--center-info")?)),
                "--app-home" => args.app_home = PathBuf::from(value("--app-home")?),
                "--cwd" => args.cwd = Some(PathBuf::from(value("--cwd")?)),
                "--workspace" => args.workspace = Some(PathBuf::from(value("--workspace")?)),
                "--profile-file" => {
                    args.profile_file = Some(PathBuf::from(value("--profile-file")?))
                }
                "--no-retry-on-grant" => args.retry_on_grant = false,
                "--print-profile" => args.print_profile = true,
                "--report-all" => args.report_all = true,
                "--no-violation-scan" => args.violation_scan = false,
                "--probe" => args.probe = true,
                "--quiet-events" => args.quiet_events = true,
                "--version" => {
                    println!("sandbox-cli {VERSION}");
                    std::process::exit(0);
                }
                "--help" | "-h" => {
                    print!("{USAGE}");
                    std::process::exit(0);
                }
                other if other.starts_with('-') && args.argv.is_empty() => {
                    return Err(format!("unknown argument `{other}` (try --help)"))
                }
                other => args.argv.push(other.to_string()),
            }
        }
        Ok(args)
    }

    fn emit(&self, value: serde_json::Value) {
        if !self.quiet_events {
            events::emit(value);
        }
    }
}

fn main() {
    let args = match Args::parse(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(error) => {
            eprintln!("sandbox-cli: {error}");
            std::process::exit(2);
        }
    };

    let code = match run(&args) {
        Ok(code) => code,
        Err(error) => {
            args.emit(events::error("sandbox_cli_failed", &error));
            eprintln!("sandbox-cli: {error}");
            2
        }
    };
    std::process::exit(code);
}

/// Thin RPC wrapper: one connection for the whole call, reconnecting once if the
/// center restarted or dropped the connection.
struct Rpc {
    channel: Channel,
    socket: PathBuf,
}

impl Rpc {
    fn connect(socket: PathBuf) -> Result<Self, String> {
        let channel =
            Channel::connect_with_retry(&socket, Duration::from_secs(5)).map_err(|error| {
                format!(
                    "cannot reach sandbox-center at {} ({error}); start it first or pass --socket",
                    socket.display()
                )
            })?;
        let _ = channel.set_read_timeout(Some(RPC_TIMEOUT));
        Ok(Self { channel, socket })
    }

    fn call(&mut self, request: &Request) -> Result<Response, String> {
        if let Ok(response) = self.channel.request(request) {
            return Ok(response);
        }
        self.channel = Channel::connect_with_retry(&self.socket, Duration::from_secs(2))
            .map_err(|error| format!("lost sandbox-center: {error}"))?;
        let _ = self.channel.set_read_timeout(Some(RPC_TIMEOUT));
        self.channel
            .request(request)
            .map_err(|error| format!("sandbox-center call failed: {error}"))
    }

    fn result<T: DeserializeOwned>(&mut self, request: &Request) -> Result<T, String> {
        self.call(request)?
            .into_result()
            .map_err(|error| error.to_string())
    }
}

fn run(args: &Args) -> Result<i32, String> {
    if args.probe {
        return probe();
    }
    if args.argv.is_empty() && !args.print_profile {
        return Err("no command given (usage: sandbox-cli -- <command> [args...])".to_string());
    }
    if !Path::new(SANDBOX_EXEC).exists() {
        args.emit(events::error(
            "seatbelt_unavailable",
            &format!("{SANDBOX_EXEC} is missing on this system"),
        ));
        return Ok(126);
    }

    let socket = resolve_socket(args)?;
    let mut rpc = Rpc::connect(socket)?;

    let cwd = match &args.cwd {
        Some(dir) => dir.clone(),
        None => std::env::current_dir().map_err(|error| format!("cannot read cwd: {error}"))?,
    };
    let workspace = args.workspace.clone().unwrap_or_else(|| cwd.clone());

    let opened: OpenSessionResult = rpc.result(&Request::OpenSession(OpenSessionRequest {
        client: ClientInfo {
            name: "sandbox-cli".to_string(),
            pid: std::process::id(),
            version: VERSION.to_string(),
        },
        cwd: cwd.to_string_lossy().into_owned(),
        argv: args.argv.clone(),
        workspace: Some(workspace.to_string_lossy().into_owned()),
        retry: false,
    }))?;

    if args.print_profile {
        print!("{}", build_profile(&opened, &[])?);
        seal_session(&mut rpc, &opened.session_id, 0, 0, 0, 0, false)?;
        return Ok(0);
    }

    args.emit(json!({
        "type": "session.opened",
        "session_id": opened.session_id,
        "tag": opened.tag,
        "command": args.argv.join(" "),
        "cwd": cwd.to_string_lossy(),
        "policy_id": opened.policy.policy_id,
        "policy": {
            "read_full": opened.policy.read_full,
            "write_default": opened.policy.write_default,
            "write_roots": opened.policy.write_roots,
            "delete_default": opened.policy.delete_default,
            "delete_roots": opened.policy.delete_roots,
            "network_mode": opened.policy.network_mode,
            "auto_grant_roots": opened.policy.auto_grant.iter().map(|rule| rule.root.clone()).collect::<Vec<_>>(),
        },
    }));

    let mut reported: HashSet<Violation> = HashSet::new();
    let mut total_violations = 0u32;
    let mut total_grants = 0u32;
    let mut retried = false;
    let mut all_violations: Vec<Violation> = Vec::new();

    let mut outcome = execute(args, &opened, &[])?;
    let mut fresh = collect_violations(args, &opened.tag, &outcome, &mut reported);
    let mut grants = report_violations(args, &mut rpc, &opened, &fresh)?;
    total_violations += fresh.len() as u32;
    total_grants += grants.len() as u32;
    all_violations.append(&mut fresh);

    if !grants.is_empty() && args.retry_on_grant {
        retried = true;
        let roots: Vec<String> = grants.iter().map(|grant| grant.root.clone()).collect();
        args.emit(json!({
            "type": "retry",
            "session_id": opened.session_id,
            "attempt": 2,
            "grants": roots,
        }));
        outcome = execute(args, &opened, &grants)?;
        fresh = collect_violations(args, &opened.tag, &outcome, &mut reported);
        let second = report_violations(args, &mut rpc, &opened, &fresh)?;
        total_violations += fresh.len() as u32;
        total_grants += second.len() as u32;
        all_violations.append(&mut fresh);
        grants.clear();
    }

    let report = violation::render_report(&all_violations);
    if !report.is_empty() {
        eprint!("{report}");
    }

    seal_session(
        &mut rpc,
        &opened.session_id,
        outcome.exit_code,
        outcome.duration_ms,
        total_violations,
        total_grants,
        retried,
    )?;

    args.emit(json!({
        "type": "session.closed",
        "session_id": opened.session_id,
        "exit_code": outcome.exit_code,
        "duration_ms": outcome.duration_ms,
        "violations": total_violations,
        "grants": total_grants,
        "retried": retried,
    }));

    if outcome.nested_sandbox_blocked {
        args.emit(events::error(
            "seatbelt_unavailable",
            "sandbox-exec could not apply the profile (nested sandbox?)",
        ));
        return Ok(126);
    }
    Ok(outcome.exit_code)
}

#[derive(Debug, Clone, PartialEq, Eq)]
struct Grant {
    class: OperationClass,
    root: String,
}

struct Outcome {
    exit_code: i32,
    duration_ms: u64,
    nested_sandbox_blocked: bool,
}

fn resolve_socket(args: &Args) -> Result<PathBuf, String> {
    if let Some(socket) = &args.socket {
        return Ok(socket.clone());
    }
    let descriptor = args
        .center_info
        .clone()
        .unwrap_or_else(|| args.app_home.join("center.json"));
    let text = std::fs::read_to_string(&descriptor).map_err(|error| {
        format!(
            "cannot read center descriptor {} ({error}); pass --socket or start sandbox-center",
            descriptor.display()
        )
    })?;
    let value: serde_json::Value =
        serde_json::from_str(&text).map_err(|error| format!("bad center descriptor: {error}"))?;
    value
        .get("socket")
        .and_then(|socket| socket.as_str())
        .map(PathBuf::from)
        .ok_or_else(|| format!("{} has no socket field", descriptor.display()))
}

fn build_profile(opened: &OpenSessionResult, grants: &[Grant]) -> Result<String, String> {
    let mut request = ProfileRequest::from_policy(&opened.policy, opened.tag.clone());
    for grant in grants {
        match grant.class {
            OperationClass::Delete => request.extra_delete_grants.push(grant.root.clone()),
            _ => request.extra_write_grants.push(grant.root.clone()),
        }
    }
    seatbelt::generate(&request)
}

fn write_profile(tag: &str, profile: &str, keep_at: Option<&Path>) -> Result<PathBuf, String> {
    use std::os::unix::fs::PermissionsExt;

    let path = match keep_at {
        Some(path) => path.to_path_buf(),
        None => {
            let tmpdir = std::env::var("TMPDIR").unwrap_or_else(|_| "/tmp".to_string());
            let dir = PathBuf::from(format!(
                "{}/sandbox-center-mvp",
                tmpdir.trim_end_matches('/')
            ));
            std::fs::create_dir_all(&dir)
                .map_err(|error| format!("cannot create {}: {error}", dir.display()))?;
            let _ = std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700));
            dir.join(format!("{tag}.sb"))
        }
    };
    std::fs::write(&path, profile).map_err(|error| format!("cannot write profile: {error}"))?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));
    Ok(path)
}

/// One `sandbox-exec` run: materialise the profile, stream output, wait.
fn execute(args: &Args, opened: &OpenSessionResult, grants: &[Grant]) -> Result<Outcome, String> {
    let profile = build_profile(opened, grants)?;
    let profile_path = write_profile(&opened.tag, &profile, args.profile_file.as_deref())?;

    let started = Instant::now();
    let mut command = Command::new(SANDBOX_EXEC);
    command
        .arg("-f")
        .arg(&profile_path)
        .args(&args.argv)
        .stdin(Stdio::inherit())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Some(cwd) = &args.cwd {
        command.current_dir(cwd);
    }

    let mut child = command
        .spawn()
        .map_err(|error| format!("cannot spawn {SANDBOX_EXEC}: {error}"))?;

    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let nested = Arc::new(AtomicBool::new(false));

    let stdout_handle =
        stdout.map(|mut reader| std::thread::spawn(move || copy_stream(&mut reader)));
    let stderr_handle = stderr.map(|reader| {
        let nested = Arc::clone(&nested);
        std::thread::spawn(move || pump_stderr(reader, nested))
    });

    let status = child
        .wait()
        .map_err(|error| format!("cannot wait for {SANDBOX_EXEC}: {error}"))?;
    if let Some(handle) = stdout_handle {
        let _ = handle.join();
    }
    if let Some(handle) = stderr_handle {
        let _ = handle.join();
    }
    let _ = std::fs::remove_file(&profile_path);

    Ok(Outcome {
        exit_code: status
            .code()
            .unwrap_or_else(|| 128 + status.signal().unwrap_or(0)),
        duration_ms: started.elapsed().as_millis() as u64,
        nested_sandbox_blocked: nested.load(Ordering::SeqCst),
    })
}

fn copy_stream<R: Read>(reader: &mut R) -> u64 {
    let stdout = std::io::stdout();
    let mut lock = stdout.lock();
    let copied = std::io::copy(reader, &mut lock).unwrap_or(0);
    let _ = lock.flush();
    copied
}

fn pump_stderr<R: Read>(reader: R, nested: Arc<AtomicBool>) -> u64 {
    let mut total = 0u64;
    for line in BufReader::new(reader).lines() {
        let Ok(line) = line else { break };
        if line.contains(NESTED_SANDBOX_MARKER) {
            nested.store(true, Ordering::SeqCst);
        }
        total += line.len() as u64 + 1;
        let stderr = std::io::stderr();
        let mut lock = stderr.lock();
        let _ = writeln!(lock, "{line}");
    }
    total
}

fn collect_violations(
    args: &Args,
    tag: &str,
    outcome: &Outcome,
    reported: &mut HashSet<Violation>,
) -> Vec<Violation> {
    // `log show` scans the requested time window, so keep it tight: the run's
    // own duration plus a small margin. A failed command gets one extra attempt
    // because log indexing can lag a few hundred milliseconds behind exit.
    if !args.violation_scan {
        return Vec::new();
    }
    let window = (outcome.duration_ms / 1000 + 3).clamp(3, 60);
    let attempts = if outcome.exit_code == 0 { 1 } else { 2 };
    let outcome_reclaim = reclaim::reclaim(tag, window, attempts, Duration::from_millis(400));
    if let Some(error) = &outcome_reclaim.error {
        args.emit(json!({
            "type": "warning",
            "code": "log_query_failed",
            "message": error,
        }));
    }
    let fresh: Vec<Violation> = outcome_reclaim
        .violations
        .into_iter()
        .filter(|item| args.report_all || item.is_policy_relevant())
        .filter(|item| !reported.contains(item))
        .collect();
    for item in &fresh {
        reported.insert(item.clone());
    }
    fresh
}

fn report_violations(
    args: &Args,
    rpc: &mut Rpc,
    opened: &OpenSessionResult,
    violations: &[Violation],
) -> Result<Vec<Grant>, String> {
    let mut grants = Vec::new();
    for item in violations {
        args.emit(json!({
            "type": "violation",
            "session_id": opened.session_id,
            "class": format!("{:?}", item.class()).to_lowercase(),
            "actor": item.actor,
            "pid": item.pid,
            "operation": item.operation,
            "target": item.target,
        }));
        let decision: sandbox_core::protocol::ReportViolationResult = rpc.result(
            &Request::ReportViolation(sandbox_core::protocol::ReportViolationRequest {
                session_id: opened.session_id.clone(),
                violation: item.clone(),
            }),
        )?;
        if decision.action == "grant" {
            if let Some(root) = decision.grant_root {
                if !grants.iter().any(|grant: &Grant| grant.root == root) {
                    args.emit(json!({
                        "type": "grant",
                        "session_id": opened.session_id,
                        "root": root,
                        "reason": decision.reason,
                        "target": item.target,
                    }));
                    grants.push(Grant {
                        class: item.class(),
                        root,
                    });
                }
            }
        }
    }
    Ok(grants)
}

fn seal_session(
    rpc: &mut Rpc,
    session_id: &str,
    exit_code: i32,
    duration_ms: u64,
    violations: u32,
    grants: u32,
    retried: bool,
) -> Result<(), String> {
    rpc.result::<sandbox_core::protocol::CloseSessionResult>(&Request::CloseSession(
        CloseSessionRequest {
            session_id: session_id.to_string(),
            exit_code,
            duration_ms,
            violations,
            grants,
            retried,
        },
    ))?;
    Ok(())
}

/// `sandbox-cli --probe`: can a Seatbelt profile be applied in this process
/// context? (Nested sandboxes cannot: `sandbox_apply: Operation not permitted`.)
fn probe() -> Result<i32, String> {
    use std::os::unix::fs::PermissionsExt;

    let tag = format!(
        "{}probe-{:08x}",
        sandbox_core::TAG_PREFIX,
        std::process::id()
    );
    let tmpdir = std::env::var("TMPDIR").unwrap_or_else(|_| "/tmp".to_string());
    let path = PathBuf::from(format!(
        "{}/sandbox-center-mvp-probe.sb",
        tmpdir.trim_end_matches('/')
    ));
    std::fs::write(&path, seatbelt::probe_profile(&tag))
        .map_err(|error| format!("cannot write probe profile: {error}"))?;
    let _ = std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600));

    let output = Command::new(SANDBOX_EXEC)
        .arg("-f")
        .arg(&path)
        .arg("/bin/echo")
        .arg("seatbelt-ok")
        .output();
    let _ = std::fs::remove_file(&path);

    match output {
        Ok(output) if output.status.success() => {
            let stdout = String::from_utf8_lossy(&output.stdout);
            if !stdout.contains("seatbelt-ok") {
                eprintln!(
                    "seatbelt-unavailable: unexpected output `{}`",
                    stdout.trim()
                );
                return Ok(1);
            }
            println!("seatbelt-ok");
            Ok(0)
        }
        Ok(output) => {
            eprintln!(
                "seatbelt-unavailable: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            );
            Ok(1)
        }
        Err(error) => {
            eprintln!("seatbelt-unavailable: {error}");
            Ok(1)
        }
    }
}
