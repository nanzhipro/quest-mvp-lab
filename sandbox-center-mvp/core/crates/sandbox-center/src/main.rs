//! `sandbox-center` — the resident policy authority of the tool-call sandbox.
//!
//! One process per app instance. It owns
//!
//! * the policy file → effective policy resolution (`${WORKSPACE}`, `${TMPDIR}`, …),
//! * the Unix-socket RPC every one-shot `sandbox-cli` talks to,
//! * session bookkeeping (id, profile tag, violations, grants),
//! * the tamper-evident audit trail (`audit-YYYY-MM-DD.jsonl`).
//!
//! It never executes user commands itself: enforcement happens in the child
//! `sandbox-exec` process owned by `sandbox-cli`.

mod audit;
mod center;
mod logging;

use center::Center;
use sandbox_core::ipc::Channel;
use sandbox_core::policy::{self, PolicyContext};
use sandbox_core::protocol::{Request, Response};
use sandbox_core::timefmt::{iso8601_utc, now_unix_ms};
use sandbox_core::VERSION;
use serde_json::json;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::Duration;

static SHUTDOWN: AtomicBool = AtomicBool::new(false);

extern "C" fn request_shutdown(_signal: libc::c_int) {
    SHUTDOWN.store(true, Ordering::SeqCst);
}

struct Args {
    socket: Option<PathBuf>,
    app_home: PathBuf,
    policy: PathBuf,
    workspace: PathBuf,
    auto_grant: bool,
    ready_file: Option<PathBuf>,
    log_file: Option<PathBuf>,
    quiet: bool,
    verify_audit: Option<PathBuf>,
}

impl Args {
    fn parse<I: Iterator<Item = String>>(mut argv: I) -> Result<Self, String> {
        let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".to_string());
        let mut args = Args {
            socket: None,
            app_home: PathBuf::from(format!("{home}/.sandbox-center-mvp")),
            policy: PathBuf::from("policy/default-policy.json"),
            workspace: std::env::current_dir().unwrap_or_else(|_| PathBuf::from("/")),
            auto_grant: true,
            ready_file: None,
            log_file: None,
            quiet: false,
            verify_audit: None,
        };
        while let Some(flag) = argv.next() {
            let mut value = |name: &str| -> Result<String, String> {
                argv.next()
                    .ok_or_else(|| format!("{name} requires a value"))
            };
            match flag.as_str() {
                "--socket" => args.socket = Some(PathBuf::from(value("--socket")?)),
                "--app-home" => args.app_home = PathBuf::from(value("--app-home")?),
                "--policy" => args.policy = PathBuf::from(value("--policy")?),
                "--workspace" => args.workspace = PathBuf::from(value("--workspace")?),
                "--ready-file" => args.ready_file = Some(PathBuf::from(value("--ready-file")?)),
                "--log" => args.log_file = Some(PathBuf::from(value("--log")?)),
                "--auto-grant" => {
                    let raw = value("--auto-grant")?;
                    args.auto_grant = matches!(raw.as_str(), "true" | "1" | "yes" | "on");
                }
                "--no-auto-grant" => args.auto_grant = false,
                "--quiet" => args.quiet = true,
                "--verify-audit" => {
                    args.verify_audit = Some(PathBuf::from(value("--verify-audit")?))
                }
                "--version" => {
                    println!("sandbox-center {VERSION}");
                    std::process::exit(0);
                }
                "--help" | "-h" => {
                    print!("{USAGE}");
                    std::process::exit(0);
                }
                other => return Err(format!("unknown argument `{other}` (try --help)")),
            }
        }
        Ok(args)
    }
}

const USAGE: &str = "\
sandbox-center — resident policy authority for the tool-call sandbox

USAGE:
  sandbox-center [OPTIONS]

OPTIONS:
  --socket <PATH>        Unix socket to listen on (default: /tmp/sandbox-center-mvp-<pid>.sock)
  --app-home <DIR>       runtime root for center.json / audit/ / logs/
                         (default: $HOME/.sandbox-center-mvp)
  --policy <FILE>        policy file to load (default: policy/default-policy.json)
  --workspace <DIR>      value substituted for ${WORKSPACE} (default: current directory)
  --auto-grant <BOOL>    enable auto-grant decisions (default: true)
  --no-auto-grant        shorthand for --auto-grant false
  --ready-file <FILE>    write the ready descriptor to this path as well as stdout
  --log <FILE>           text log path (default: <app-home>/logs/sandbox-center.log)
  --quiet                do not echo log lines to stderr
  --verify-audit <FILE>  verify an audit chain and exit
  --version              print the version
  --help                 print this help
";

fn main() {
    let args = match Args::parse(std::env::args().skip(1)) {
        Ok(args) => args,
        Err(error) => {
            eprintln!("sandbox-center: {error}");
            std::process::exit(2);
        }
    };

    if let Some(path) = args.verify_audit {
        match audit::verify(&path) {
            Ok(count) => {
                println!(
                    "audit chain OK: {count} records verified in {}",
                    path.display()
                );
                std::process::exit(0);
            }
            Err(error) => {
                eprintln!("audit chain BROKEN in {}: {error}", path.display());
                std::process::exit(1);
            }
        }
    }

    if let Err(error) = run(args) {
        eprintln!("sandbox-center: {error}");
        std::process::exit(1);
    }
}

fn run(args: Args) -> Result<(), String> {
    let audit_dir = args.app_home.join("audit");
    let log_path = args
        .log_file
        .clone()
        .unwrap_or_else(|| args.app_home.join("logs/sandbox-center.log"));
    std::fs::create_dir_all(&audit_dir).map_err(|e| format!("cannot create audit dir: {e}"))?;

    let logger = logging::Logger::new("sandbox-center", Some(&log_path));
    let logger = if args.quiet {
        logger.without_stderr()
    } else {
        logger
    };

    // --- policy -----------------------------------------------------------
    let policy_file = policy::load_file(&args.policy)?;
    let context = PolicyContext {
        workspace: args.workspace.to_string_lossy().into_owned(),
        app_home: args.app_home.to_string_lossy().into_owned(),
        home: std::env::var("HOME").unwrap_or_default(),
        tmpdir: std::env::var("TMPDIR").unwrap_or_else(|_| "/tmp".to_string()),
    };
    let policy_id = format!("{}@{}", policy_file.name, policy_file.version);
    let effective = policy::resolve(&policy_file, &context, &policy_id)?;

    let audit = audit::AuditWriter::open(&audit_dir).map_err(|e| format!("audit: {e}"))?;
    let audit_path = audit.path().to_path_buf();
    let center = Arc::new(Center::new(effective, args.auto_grant, logger, audit));

    // --- socket -----------------------------------------------------------
    let socket_path = args.socket.clone().unwrap_or_else(|| {
        PathBuf::from(format!(
            "/tmp/sandbox-center-mvp-{}.sock",
            std::process::id()
        ))
    });
    bind_socket(&socket_path)?;

    // Bind before announcing readiness so a client that sees the descriptor
    // (or the ready line) can connect immediately.
    let listener = UnixListener::bind(&socket_path).map_err(|e| format!("bind: {e}"))?;
    std::fs::set_permissions(&socket_path, std::fs::Permissions::from_mode(0o600))
        .map_err(|e| format!("chmod socket: {e}"))?;
    listener
        .set_nonblocking(true)
        .map_err(|e| format!("nonblocking: {e}"))?;

    let started_ms = now_unix_ms();
    let descriptor = json!({
        "event": "ready",
        "version": VERSION,
        "pid": std::process::id(),
        "socket": socket_path.to_string_lossy(),
        "policy_id": center.policy.policy_id,
        "policy_file": args.policy.to_string_lossy(),
        "audit": audit_path.to_string_lossy(),
        "app_home": args.app_home.to_string_lossy(),
        "workspace": context.workspace,
        "auto_grant": args.auto_grant,
        "rules": center.policy.rule_count(),
        "started_ms": started_ms,
    });
    write_descriptor(&args.app_home.join("center.json"), &descriptor)?;
    if let Some(path) = &args.ready_file {
        write_descriptor(path, &descriptor)?;
    }

    center.record(
        "center.started",
        None,
        json!({
            "pid": std::process::id(),
            "socket": socket_path.to_string_lossy(),
            "policy_id": center.policy.policy_id,
            "policy_file": args.policy.to_string_lossy(),
            "workspace": context.workspace,
            "auto_grant": args.auto_grant,
            "rules": center.policy.rule_count(),
            "version": VERSION,
        }),
    );
    center.logger.info(format!(
        "CenterServer ready: socket={} policy={} rules={} auto_grant={} audit={}",
        socket_path.display(),
        center.policy.policy_id,
        center.policy.rule_count(),
        args.auto_grant,
        audit_path.display()
    ));

    println!("{}", serde_json::to_string(&descriptor).unwrap());
    let _ = std::io::stdout().flush();

    install_signal_handlers();

    while !SHUTDOWN.load(Ordering::SeqCst) {
        match listener.accept() {
            Ok((stream, _)) => {
                let center = Arc::clone(&center);
                std::thread::spawn(move || serve(stream, center));
            }
            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                std::thread::sleep(Duration::from_millis(40));
            }
            Err(error) => {
                center.logger.warn(format!("accept failed: {error}"));
                std::thread::sleep(Duration::from_millis(100));
            }
        }
    }

    center.record(
        "center.stopped",
        None,
        json!({"uptime_ms": now_unix_ms().saturating_sub(started_ms)}),
    );
    center.logger.info("CenterServer stopped");
    let _ = std::fs::remove_file(&socket_path);
    let _ = std::fs::remove_file(args.app_home.join("center.json"));
    println!(
        "{}",
        json!({"event": "stopped", "ts": iso8601_utc(now_unix_ms())})
    );
    Ok(())
}

fn bind_socket(path: &Path) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    if path.exists() {
        match UnixStream::connect(path) {
            Ok(_) => {
                return Err(format!(
                    "another sandbox-center is already listening on {}",
                    path.display()
                ))
            }
            Err(_) => {
                std::fs::remove_file(path)
                    .map_err(|e| format!("cannot remove stale socket {}: {e}", path.display()))?;
            }
        }
    }
    Ok(())
}

fn write_descriptor(path: &Path, value: &serde_json::Value) -> Result<(), String> {
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)
            .map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    let temporary = path.with_extension("json.tmp");
    std::fs::write(&temporary, serde_json::to_string_pretty(value).unwrap())
        .map_err(|e| format!("cannot write descriptor: {e}"))?;
    std::fs::rename(&temporary, path).map_err(|e| format!("cannot publish descriptor: {e}"))
}

fn install_signal_handlers() {
    unsafe {
        libc::signal(
            libc::SIGTERM,
            request_shutdown as *const () as libc::sighandler_t,
        );
        libc::signal(
            libc::SIGINT,
            request_shutdown as *const () as libc::sighandler_t,
        );
        libc::signal(libc::SIGPIPE, libc::SIG_IGN);
    }
}

fn serve(stream: UnixStream, center: Arc<Center>) {
    let mut channel = match Channel::new(stream) {
        Ok(channel) => channel,
        Err(error) => {
            center.logger.warn(format!("channel setup failed: {error}"));
            return;
        }
    };
    loop {
        let request: Request = match channel.recv() {
            Ok(request) => request,
            Err(error) if error.kind() == std::io::ErrorKind::UnexpectedEof => return,
            Err(error) => {
                let _ = channel.send(&Response::err("bad_request", error.to_string()));
                return;
            }
        };
        let response = dispatch(&center, request);
        if let Err(error) = channel.send(&response) {
            center
                .logger
                .warn(format!("response write failed: {error}"));
            return;
        }
    }
}

fn dispatch(center: &Arc<Center>, request: Request) -> Response {
    match request {
        Request::Ping => Response::ok(center.ping()),
        Request::OpenSession(payload) => match center.open_session(payload) {
            Ok(result) => Response::ok(result),
            Err(error) => Response::err("open_session_failed", error),
        },
        Request::ReportViolation(payload) => match center.report_violation(payload) {
            Ok(result) => Response::ok(result),
            Err(error) => Response::err("report_violation_failed", error),
        },
        Request::CloseSession(payload) => match center.close_session(payload) {
            Ok(result) => Response::ok(result),
            Err(error) => Response::err("close_session_failed", error),
        },
        Request::ListSessions => Response::ok(center.list_sessions()),
        Request::RecentEvents { limit } => Response::ok(center.recent_events(limit.min(500))),
    }
}
