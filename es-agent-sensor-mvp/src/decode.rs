//! C 扁平结构 → schema 归一化转换（纯函数，可测）。
//!
//! 职责：ES 事件 → (verb, category, target) + 进程树变更操作（TreeOp）。
//! 不碰进程树与 matcher；AgentEvent 的最终组装在 pipeline 层完成。
//! CLOSE(modified=false) 等应丢弃的事件返回 None。

use crate::ffi::{es_type, EsshFile, EsshMessage, EsshProcess};
use crate::proctree::PidKey;
use crate::schema::{Category, FileStat, Program, Target, Verb};

/// 进程树变更操作，由 pipeline 应用到 ProcessTree。
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum TreeOp {
    /// FORK：子节点继承父的 program（共享 Arc）与 cred。
    Fork { parent: PidKey, child: PidKey },
    /// EXEC：新建节点（pidversion 变），替换 program/cred，旧 key 延迟回收。
    Exec {
        old: PidKey,
        new: PidKey,
        program: Program,
    },
    /// EXIT：延迟回收。
    Exit { key: PidKey },
}

/// 一条已归一化的事件（尚未做 agent 归因与 actor 富化）。
#[derive(Debug, Clone)]
pub struct Decoded {
    pub verb: Verb,
    pub category: Category,
    pub target: Target,
    /// 需要应用到进程树的变更（仅 fork/exec/exit 有）。
    pub tree_op: Option<TreeOp>,
}

fn pidkey(p: &EsshProcess) -> PidKey {
    PidKey {
        pid: p.pid,
        pidversion: u64::from(p.pidversion),
    }
}

fn file_stat(f: &EsshFile) -> FileStat {
    FileStat {
        ino: f.ino,
        mode: f.mode,
        size: f.size,
        uid: f.uid,
        gid: f.gid,
        mtime: f.mtime_sec,
    }
}

/// es_process_t → schema::Program（argv 仅 EXEC 事件由调用方补充）。
pub fn program_of(p: &EsshProcess, argv: Vec<String>) -> Program {
    Program {
        executable: p.executable_str().to_owned(),
        argv,
        signing_id: p.signing_id_str().map(str::to_owned),
        team_id: p.team_id_str().map(str::to_owned),
        cdhash: p.cdhash_hex(),
        is_platform_binary: p.is_platform_binary != 0,
    }
}

/// open(2) fflag → access 语义（O_ACCMODE 低两位：0=read 1=write 2=readwrite）。
fn access_of(fflag: i32) -> &'static str {
    match fflag & 3 {
        1 => "write",
        2 => "readwrite",
        _ => "read",
    }
}

const S_IFMT: u32 = 0o170000;
const S_IFDIR: u32 = 0o040000;

fn is_dir_mode(mode: u32) -> bool {
    mode & S_IFMT == S_IFDIR
}

/// setattrlist attrgroup 位图 → 属性名列表（常见子集，位常量见 sys/attr.h）。
fn attr_names(common: u32, file: u32) -> Vec<String> {
    const MAP: &[(u32, &str)] = &[
        (0x0000_0200, "crtime"),
        (0x0000_0400, "mtime"),
        (0x0000_0800, "chgtime"),
        (0x0000_1000, "atime"),
        (0x0000_8000, "uid"),
        (0x0001_0000, "gid"),
        (0x0002_0000, "mode"),
        (0x0004_0000, "flags"),
        (0x0040_0000, "acl"),
        (0x0200_0000, "fileid"),
    ];
    const FILE_MAP: &[(u32, &str)] = &[
        (0x0000_0001, "linkcount"),
        (0x0000_0002, "totalsize"),
        (0x0000_0004, "allocsize"),
        (0x0000_0200, "datalength"),
    ];
    let mut out: Vec<String> = MAP
        .iter()
        .filter(|(bit, _)| common & bit != 0)
        .map(|(_, name)| (*name).to_owned())
        .collect();
    out.extend(
        FILE_MAP
            .iter()
            .filter(|(bit, _)| file & bit != 0)
            .map(|(_, name)| (*name).to_owned()),
    );
    out
}

/// 解码一条扁平事件。返回 None 表示应丢弃（CLOSE 未修改 / 未知事件）。
pub fn decode(msg: &EsshMessage) -> Option<Decoded> {
    match msg.event_type {
        es_type::NOTIFY_EXEC => {
            let ev = msg.exec()?;
            let program = program_of(&ev.target, ev.argv());
            Some(Decoded {
                verb: Verb::Exec,
                category: Category::Process,
                target: Target::Process {
                    pid: ev.target.pid,
                    pidversion: u64::from(ev.target.pidversion),
                    program: Some(program.clone()),
                    exit_stat: None,
                },
                tree_op: Some(TreeOp::Exec {
                    old: pidkey(&msg.process),
                    new: pidkey(&ev.target),
                    program,
                }),
            })
        }
        es_type::NOTIFY_FORK => {
            let ev = msg.fork()?;
            Some(Decoded {
                verb: Verb::Spawn,
                category: Category::Process,
                target: Target::Process {
                    pid: ev.child.pid,
                    pidversion: u64::from(ev.child.pidversion),
                    program: Some(program_of(&ev.child, Vec::new())),
                    exit_stat: None,
                },
                tree_op: Some(TreeOp::Fork {
                    parent: pidkey(&msg.process),
                    child: pidkey(&ev.child),
                }),
            })
        }
        es_type::NOTIFY_EXIT => {
            let stat = msg.exit()?.stat;
            Some(Decoded {
                verb: Verb::Exit,
                category: Category::Process,
                target: Target::Process {
                    pid: msg.process.pid,
                    pidversion: u64::from(msg.process.pidversion),
                    program: None, // 由 pipeline 用进程树节点回填
                    exit_stat: Some(stat),
                },
                tree_op: Some(TreeOp::Exit {
                    key: pidkey(&msg.process),
                }),
            })
        }
        es_type::NOTIFY_CREATE => {
            let ev = msg.create()?;
            Some(Decoded {
                verb: Verb::Create,
                category: Category::File,
                target: Target::File {
                    path: ev.file.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.file)),
                    existed: Some(ev.created_new == 0),
                    is_dir: None,
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_OPEN => {
            let ev = msg.open()?;
            Some(Decoded {
                verb: Verb::Open,
                category: Category::File,
                target: Target::File {
                    path: ev.file.path_str().to_owned(),
                    dst_path: None,
                    access: Some(access_of(ev.fflag).to_owned()),
                    stat: Some(file_stat(&ev.file)),
                    existed: None,
                    is_dir: None,
                    attrs: None,
                    fflag: Some(ev.fflag),
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_CLOSE => {
            let ev = msg.close()?;
            if ev.modified == 0 {
                return None; // 未修改的 close 降噪丢弃
            }
            Some(Decoded {
                verb: Verb::Write, // 写完成信号
                category: Category::File,
                target: Target::File {
                    path: ev.file.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.file)),
                    existed: None,
                    is_dir: None,
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_RENAME => {
            let ev = msg.rename()?;
            Some(Decoded {
                verb: Verb::Move,
                category: Category::File,
                target: Target::File {
                    path: ev.src.path_str().to_owned(),
                    dst_path: Some(ev.dst.path_str().to_owned()),
                    access: None,
                    stat: Some(file_stat(&ev.src)),
                    existed: Some(ev.dst_existed != 0),
                    is_dir: None,
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_UNLINK => {
            let ev = msg.unlink()?;
            Some(Decoded {
                verb: Verb::Delete,
                category: Category::File,
                target: Target::File {
                    path: ev.target.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.target)),
                    existed: None,
                    is_dir: Some(is_dir_mode(ev.target.mode)), // 目录即 rmdir
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_LINK => {
            let ev = msg.link()?;
            Some(Decoded {
                verb: Verb::Link,
                category: Category::File,
                target: Target::File {
                    path: ev.src.path_str().to_owned(),
                    dst_path: Some(ev.dst.path_str().to_owned()),
                    access: None,
                    stat: Some(file_stat(&ev.src)),
                    existed: None,
                    is_dir: Some(ev.is_dir != 0),
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_CLONE => {
            let ev = msg.clone_ev()?;
            Some(Decoded {
                verb: Verb::Clone,
                category: Category::File,
                target: Target::File {
                    path: ev.src.path_str().to_owned(),
                    dst_path: Some(ev.dst.path_str().to_owned()),
                    access: None,
                    stat: Some(file_stat(&ev.src)),
                    existed: None,
                    is_dir: Some(ev.is_dir != 0),
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_SETATTRLIST => {
            let ev = msg.setattrlist()?;
            Some(Decoded {
                verb: Verb::Setattr,
                category: Category::File,
                target: Target::File {
                    path: ev.target.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.target)),
                    existed: None,
                    is_dir: None,
                    attrs: Some(attr_names(ev.commonattr, ev.fileattr)),
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_TRUNCATE => {
            let ev = msg.truncate()?;
            Some(Decoded {
                verb: Verb::Truncate,
                category: Category::File,
                target: Target::File {
                    path: ev.target.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.target)),
                    existed: None,
                    is_dir: None,
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_UIPC_BIND => {
            let ev = msg.uipc_bind()?;
            Some(Decoded {
                verb: Verb::IpcBind,
                category: Category::Ipc,
                target: Target::Socket {
                    domain: "unix".into(),
                    path: Some(ev.sock.path_str().to_owned()),
                    socket_type: None,
                    protocol: None,
                    mode: Some(ev.mode),
                    peer_process: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_UIPC_CONNECT => {
            let ev = msg.uipc_connect()?;
            let path = ev.file.path_str();
            Some(Decoded {
                verb: Verb::IpcConnect,
                category: Category::Ipc,
                target: Target::Socket {
                    domain: if ev.domain == 1 { "unix" } else { "other" }.into(),
                    path: if path.is_empty() {
                        None
                    } else {
                        Some(path.to_owned())
                    },
                    socket_type: Some(ev.sock_type),
                    protocol: Some(ev.protocol),
                    mode: None,
                    peer_process: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_WRITE => {
            let ev = msg.write()?;
            Some(Decoded {
                verb: Verb::Write,
                category: Category::File,
                target: Target::File {
                    path: ev.target.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.target)),
                    existed: None,
                    is_dir: None,
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_READDIR => {
            let ev = msg.readdir()?;
            Some(Decoded {
                verb: Verb::Readdir,
                category: Category::File,
                target: Target::File {
                    path: ev.dir.path_str().to_owned(),
                    dst_path: None,
                    access: None,
                    stat: Some(file_stat(&ev.dir)),
                    existed: None,
                    is_dir: Some(true),
                    attrs: None,
                    fflag: None,
                },
                tree_op: None,
            })
        }
        es_type::NOTIFY_SIGNAL => {
            let ev = msg.signal()?;
            Some(Decoded {
                verb: Verb::Signal,
                category: Category::Process,
                target: Target::Process {
                    pid: ev.target.pid,
                    pidversion: u64::from(ev.target.pidversion),
                    program: Some(program_of(&ev.target, Vec::new())),
                    exit_stat: None,
                },
                tree_op: None,
            })
        }
        _ => None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::*;

    fn zeroed_msg() -> EsshMessage {
        testutil::zeroed_message()
    }

    fn proc(pid: i32, pidversion: u32, exe: &str) -> EsshProcess {
        let mut p = testutil::zeroed_process();
        p.pid = pid;
        p.pidversion = pidversion;
        p.ppid = 1;
        p.responsible_pid = pid;
        let b = exe.as_bytes();
        p.executable[..b.len()].copy_from_slice(b);
        p.executable_len = b.len() as u32;
        p
    }

    fn file(path: &str, mode: u32) -> EsshFile {
        let mut f = testutil::zeroed_file();
        let b = path.as_bytes();
        f.path[..b.len()].copy_from_slice(b);
        f.path_len = b.len() as u32;
        f.mode = mode;
        f.ino = 42;
        f.size = 100;
        f
    }

    fn target_file(t: &Target) -> (String, Option<String>, Option<String>, Option<bool>) {
        match t {
            Target::File {
                path,
                dst_path,
                access,
                existed,
                ..
            } => (path.clone(), dst_path.clone(), access.clone(), *existed),
            _ => panic!("expect file target"),
        }
    }

    #[test]
    fn exec_decodes_verb_target_and_tree_op() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_EXEC;
        msg.process = proc(100, 5, "/bin/zsh");
        let mut exec = testutil::zeroed_exec();
        exec.target = proc(100, 6, "/opt/agent/bin/hermes");
        let arg = b"hermes";
        exec.argv_buf[..6].copy_from_slice(arg);
        exec.argv_offsets[0] = 0;
        exec.argv_lens[0] = 6;
        exec.argc = 1;
        msg.event.exec = exec;

        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Exec);
        assert_eq!(d.category, Category::Process);
        match d.tree_op.unwrap() {
            TreeOp::Exec { old, new, program } => {
                assert_eq!(
                    old,
                    PidKey {
                        pid: 100,
                        pidversion: 5
                    }
                );
                assert_eq!(
                    new,
                    PidKey {
                        pid: 100,
                        pidversion: 6
                    }
                );
                assert_eq!(program.executable, "/opt/agent/bin/hermes");
                assert_eq!(program.argv, vec!["hermes"]);
            }
            _ => panic!("expect exec tree op"),
        }
        match d.target {
            Target::Process {
                pid, pidversion, ..
            } => {
                assert_eq!(pid, 100);
                assert_eq!(pidversion, 6);
            }
            _ => panic!("expect process target"),
        }
    }

    #[test]
    fn fork_decodes_spawn() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_FORK;
        msg.process = proc(100, 5, "/opt/agent/bin/hermes");
        msg.event.fork = EsshEvFork {
            child: proc(200, 1, "/opt/agent/bin/hermes"),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Spawn);
        match d.tree_op.unwrap() {
            TreeOp::Fork { parent, child } => {
                assert_eq!(parent.pid, 100);
                assert_eq!(child.pid, 200);
            }
            _ => panic!("expect fork tree op"),
        }
    }

    #[test]
    fn exit_decodes_exit_stat() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_EXIT;
        msg.process = proc(100, 5, "/opt/agent/bin/hermes");
        msg.event.exit = EsshEvExit { stat: 0x100 };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Exit);
        match d.target {
            Target::Process { exit_stat, .. } => assert_eq!(exit_stat, Some(0x100)),
            _ => panic!("expect process target"),
        }
        assert!(matches!(d.tree_op, Some(TreeOp::Exit { .. })));
    }

    #[test]
    fn create_distinguishes_new_vs_existing() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_CREATE;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.create = EsshEvCreate {
            file: file("/tmp/new.txt", 0o644),
            mode: 0o644,
            created_new: 1,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Create);
        let (path, _, _, existed) = target_file(&d.target);
        assert_eq!(path, "/tmp/new.txt");
        assert_eq!(existed, Some(false));

        msg.event.create.created_new = 0;
        let d = decode(&msg).unwrap();
        assert_eq!(target_file(&d.target).3, Some(true));
    }

    #[test]
    fn open_fflag_maps_to_access() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_OPEN;
        msg.process = proc(100, 5, "/bin/sh");
        for (fflag, expect) in [(0, "read"), (1, "write"), (2, "readwrite"), (0x200, "read")] {
            msg.event.open = EsshEvOpen {
                file: file("/tmp/a", 0o644),
                fflag,
            };
            let d = decode(&msg).unwrap();
            assert_eq!(d.verb, Verb::Open);
            assert_eq!(target_file(&d.target).2.as_deref(), Some(expect));
        }
    }

    #[test]
    fn close_only_emits_when_modified() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_CLOSE;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.close = EsshEvClose {
            file: file("/tmp/a", 0o644),
            modified: 0,
            was_mapped_writable: 0,
        };
        assert!(decode(&msg).is_none());
        msg.event.close.modified = 1;
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Write);
    }

    #[test]
    fn rename_decodes_src_dst() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_RENAME;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.rename = EsshEvRename {
            src: file("/tmp/config.yaml.tmp", 0o644),
            dst: file("/tmp/config.yaml", 0o644),
            dst_existed: 0,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Move);
        let (src, dst, _, existed) = target_file(&d.target);
        assert_eq!(src, "/tmp/config.yaml.tmp");
        assert_eq!(dst.as_deref(), Some("/tmp/config.yaml"));
        assert_eq!(existed, Some(false));
    }

    #[test]
    fn unlink_covers_rmdir_via_is_dir() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_UNLINK;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.unlink = EsshEvUnlink {
            target: file("/tmp/d", S_IFDIR | 0o755),
            parent_dir: file("/tmp", S_IFDIR | 0o755),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Delete);
        match d.target {
            Target::File { is_dir, .. } => assert_eq!(is_dir, Some(true)),
            _ => panic!("expect file target"),
        }
    }

    #[test]
    fn link_clone_decode_src_dst() {
        let mut msg = zeroed_msg();
        msg.process = proc(100, 5, "/bin/sh");

        msg.event_type = es_type::NOTIFY_LINK;
        msg.event.link = EsshEvLink {
            src: file("/tmp/a", 0o644),
            dst: file("/tmp/b", 0o644),
            is_dir: 0,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Link);
        assert_eq!(target_file(&d.target).1.as_deref(), Some("/tmp/b"));

        msg.event_type = es_type::NOTIFY_CLONE;
        msg.event.clone = EsshEvClone {
            src: file("/tmp/a", 0o644),
            dst: file("/tmp/c", 0o644),
            is_dir: 0,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Clone);
        assert_eq!(target_file(&d.target).1.as_deref(), Some("/tmp/c"));
    }

    #[test]
    fn setattrlist_decodes_attr_names() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_SETATTRLIST;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.setattrlist = EsshEvSetattrlist {
            target: file("/tmp/a", 0o644),
            commonattr: 0x0002_0000 | 0x0000_0400, // mode | mtime
            volattr: 0,
            dirattr: 0,
            fileattr: 0,
            forkattr: 0,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Setattr);
        match d.target {
            Target::File { attrs, .. } => {
                let attrs = attrs.unwrap();
                assert!(attrs.contains(&"mode".to_owned()));
                assert!(attrs.contains(&"mtime".to_owned()));
            }
            _ => panic!("expect file target"),
        }
    }

    #[test]
    fn truncate_decodes() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_TRUNCATE;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.truncate = EsshEvTruncate {
            target: file("/tmp/a", 0o644),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Truncate);
    }

    #[test]
    fn uipc_bind_decodes_socket_path() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_UIPC_BIND;
        msg.process = proc(100, 5, "/usr/bin/python3");
        msg.event.uipc_bind = EsshEvUipcBind {
            sock: file("/tmp/agent.sock", 0o755),
            mode: 0o755,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::IpcBind);
        assert_eq!(d.category, Category::Ipc);
        match d.target {
            Target::Socket { domain, path, .. } => {
                assert_eq!(domain, "unix");
                assert_eq!(path.as_deref(), Some("/tmp/agent.sock"));
            }
            _ => panic!("expect socket target"),
        }
    }

    #[test]
    fn uipc_connect_decodes_domain() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_UIPC_CONNECT;
        msg.process = proc(100, 5, "/usr/bin/python3");
        msg.event.uipc_connect = EsshEvUipcConnect {
            file: file("/tmp/agent.sock", 0o755),
            domain: 1, // AF_UNIX
            sock_type: 1,
            protocol: 0,
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::IpcConnect);
        match d.target {
            Target::Socket { domain, .. } => assert_eq!(domain, "unix"),
            _ => panic!("expect socket target"),
        }
    }

    #[test]
    fn unknown_event_dropped() {
        let msg = zeroed_msg(); // event_type = 0 (AUTH_EXEC，未订阅)
        assert!(decode(&msg).is_none());
    }

    #[test]
    fn extra_write_decodes() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_WRITE;
        msg.process = proc(100, 5, "/bin/sh");
        msg.event.write = EsshEvWrite {
            target: file("/tmp/a", 0o644),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Write);
        assert_eq!(d.category, Category::File);
        assert!(d.tree_op.is_none());
        assert_eq!(target_file(&d.target).0, "/tmp/a");
    }

    #[test]
    fn extra_readdir_decodes_as_dir() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_READDIR;
        msg.process = proc(100, 5, "/bin/ls");
        msg.event.readdir = EsshEvReaddir {
            dir: file("/tmp/d", S_IFDIR | 0o755),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Readdir);
        match d.target {
            Target::File { path, is_dir, .. } => {
                assert_eq!(path, "/tmp/d");
                assert_eq!(is_dir, Some(true));
            }
            _ => panic!("expect file target"),
        }
    }

    #[test]
    fn extra_signal_decodes_target_process() {
        let mut msg = zeroed_msg();
        msg.event_type = es_type::NOTIFY_SIGNAL;
        msg.process = proc(100, 5, "/bin/kill");
        msg.event.signal = EsshEvSignal {
            sig: 15,
            target: proc(200, 3, "/usr/bin/python3"),
        };
        let d = decode(&msg).unwrap();
        assert_eq!(d.verb, Verb::Signal);
        assert_eq!(d.category, Category::Process);
        match d.target {
            Target::Process {
                pid,
                pidversion,
                program,
                exit_stat,
            } => {
                assert_eq!(pid, 200);
                assert_eq!(pidversion, 3);
                assert_eq!(program.unwrap().executable, "/usr/bin/python3");
                assert_eq!(exit_stat, None);
            }
            _ => panic!("expect process target"),
        }
    }
}
