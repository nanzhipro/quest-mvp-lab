//! 事件管道：crossbeam 有界 channel + 单写线程。
//!
//! ES 回调（producer）只做 `try_send`：满则丢并计 `dropped` 指标，回调零阻塞。
//! 写线程（consumer）做 schema 组装 + serde_json 序列化 + JSONL 落盘。

use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::RwLock;

use crossbeam_channel::{Receiver, Sender, TrySendError};

use crate::decode::{self, TreeOp};
use crate::ffi::EsshMessage;
use crate::matcher::AgentSet;
use crate::metrics::Metrics;
use crate::netpoll::SocketKey;
use crate::proctree::{PidKey, ProcessTree};
use crate::schema::{
    self, Actor, AgentEvent, AgentRef, Category, Context, Cred, EsEventRef, MatchKind, Operation,
    Outcome, Program, Sensor, Seq, Target, Verb,
};
use crate::sink::JsonlSink;

/// 写线程工作项：原始 ES 事件 / net-poller 合成事件。
pub enum WorkItem {
    Raw(Box<EsshMessage>),
    Net(NetEvent),
}

/// net-poller diff 出的合成事件。
#[derive(Debug, Clone)]
pub struct NetEvent {
    pub pid: i32,
    pub agent_id: String,
    pub verb: Verb, // NetConnect | NetClose
    pub key: SocketKey,
    pub time_sec: i64,
    pub time_nsec: i64,
}

/// 生产端：ES 回调线程持有，满则丢 + 计数。
#[derive(Clone)]
pub struct Producer {
    tx: Sender<WorkItem>,
    dropped: Arc<AtomicU64>,
}

impl Producer {
    pub fn try_push_raw(&self, msg: EsshMessage) {
        self.push(WorkItem::Raw(Box::new(msg)));
    }

    pub fn try_push_net(&self, ev: NetEvent) {
        self.push(WorkItem::Net(ev));
    }

    fn push(&self, item: WorkItem) {
        match self.tx.try_send(item) {
            Ok(()) | Err(TrySendError::Disconnected(_)) => {}
            Err(TrySendError::Full(_)) => {
                self.dropped.fetch_add(1, Ordering::Relaxed);
            }
        }
    }

    pub fn dropped(&self) -> u64 {
        self.dropped.load(Ordering::Relaxed)
    }

    /// 丢弃计数的共享句柄（停机后 producer 已 drop，统计仍需读取）。
    pub fn dropped_counter(&self) -> Arc<AtomicU64> {
        self.dropped.clone()
    }
}

/// 建立有界管道。
pub fn channel(capacity: usize) -> (Producer, Receiver<WorkItem>) {
    let (tx, rx) = crossbeam_channel::bounded(capacity);
    (
        Producer {
            tx,
            dropped: Arc::new(AtomicU64::new(0)),
        },
        rx,
    )
}

/// 事件组装器：decode → 进程树变更 → 双向匹配过滤 → AgentEvent。
/// 持有进程树写权限的唯一线程（写线程）。
pub struct Assembler {
    pub tree: Arc<RwLock<ProcessTree>>,
    pub agents: Arc<AgentSet>,
    pub metrics: Arc<Metrics>,
    pub sensor_pid: u32,
}

impl Assembler {
    /// 处理一条原始 ES 事件；不命中过滤返回 None。
    pub fn handle_raw(&self, msg: &EsshMessage) -> Option<AgentEvent> {
        self.metrics.received.inc();
        // 顺带推进延迟回收（peek 堆顶，代价可忽略）。
        self.tree.write().unwrap().reap(msg.mach_time);

        let decoded = decode::decode(msg)?;
        if let Some(op) = &decoded.tree_op {
            let mut tree = self.tree.write().unwrap();
            match *op {
                TreeOp::Fork { parent, child } => {
                    tree.handle_fork(msg.mach_time, parent, child);
                }
                TreeOp::Exec {
                    old,
                    new,
                    ref program,
                } => {
                    tree.handle_exec(msg.mach_time, old, new, program.clone());
                }
                TreeOp::Exit { key } => {
                    tree.handle_exit(msg.mach_time, key);
                }
            }
        }

        let actor_key = PidKey {
            pid: msg.process.pid,
            pidversion: u64::from(msg.process.pidversion),
        };
        let (node, origin) = {
            let tree = self.tree.read().unwrap();
            let node = tree.get(&actor_key);
            let origin = tree.origin(&actor_key);
            (node, origin)
        };

        let actor_agent = node.as_ref().and_then(|n| n.agent_id.as_deref());
        let (agent, match_kind) = self
            .agents
            .combine(actor_agent, decoded.target.primary_path())?;
        self.metrics.matched.inc();

        Some(self.build_event(msg, &decoded, node, origin, agent, match_kind))
    }

    fn build_event(
        &self,
        msg: &EsshMessage,
        decoded: &decode::Decoded,
        node: Option<Arc<crate::proctree::Process>>,
        origin: Option<schema::Origin>,
        agent: crate::matcher::Agent,
        match_kind: MatchKind,
    ) -> AgentEvent {
        // program：优先进程树节点（含 exec/seed 时补查的 argv），
        // 否则退回事件自带的 executable（无 argv）。
        let program = node
            .as_ref()
            .map(|n| (*n.program).clone())
            .unwrap_or_else(|| decode::program_of(&msg.process, Vec::new()));
        // EXIT 的 target.process.program 用节点信息回填。
        let target = match (&decoded.target, &node) {
            (
                Target::Process {
                    pid,
                    pidversion,
                    program: None,
                    exit_stat,
                },
                Some(n),
            ) => Target::Process {
                pid: *pid,
                pidversion: *pidversion,
                program: Some((*n.program).clone()),
                exit_stat: *exit_stat,
            },
            _ => decoded.target.clone(),
        };
        let p = &msg.process;
        AgentEvent {
            schema_version: schema::SCHEMA_VERSION,
            event_id: ulid::Ulid::new().to_string(),
            time: schema::rfc3339_nanos(msg.time_sec, msg.time_nsec),
            seq: Seq {
                global: msg.global_seq_num,
                local: msg.seq_num,
            },
            sensor: Sensor {
                name: schema::SENSOR_NAME,
                version: env!("CARGO_PKG_VERSION"),
                pid: self.sensor_pid,
            },
            agent: Some(AgentRef {
                id: agent.id.clone(),
                workdir: agent.workdir.clone(),
                match_kind,
            }),
            actor: Actor {
                pid: p.pid,
                pidversion: u64::from(p.pidversion),
                ppid: p.ppid,
                program,
                cred: Cred {
                    ruid: p.ruid,
                    euid: p.euid,
                    rgid: p.rgid,
                    egid: p.egid,
                    auid: p.auid,
                },
                origin,
                responsible_pid: p.responsible_pid,
            },
            operation: Operation {
                category: decoded.category,
                verb: decoded.verb,
                outcome: Outcome::Completed,
                es_event: Some(EsEventRef {
                    type_name: crate::ffi::es_type::name(msg.event_type).to_owned(),
                    id: msg.event_type,
                    message_version: msg.message_version,
                }),
            },
            context: Context {
                agent_workdir_hit: decoded.target.primary_path().map(str::to_owned),
                mach_time: msg.mach_time,
                thread_id: msg.thread_id,
            },
            target,
        }
    }

    /// 处理 net-poller 合成事件。
    pub fn handle_net(&self, ev: &NetEvent) -> Option<AgentEvent> {
        let node = self.tree.read().unwrap().find_by_pid(ev.pid);
        let agent = self.agents.agents().iter().find(|a| a.id == ev.agent_id)?;
        let program: Program = node
            .as_ref()
            .map(|n| (*n.program).clone())
            .unwrap_or(Program {
                executable: String::new(),
                argv: Vec::new(),
                signing_id: None,
                team_id: None,
                cdhash: None,
                is_platform_binary: false,
            });
        let (ppid, cred, pidversion) = node
            .as_ref()
            .map(|n| {
                (
                    n.parent.as_ref().map(|p| p.pid.pid).unwrap_or(0),
                    n.cred.clone(),
                    n.pid.pidversion,
                )
            })
            .unwrap_or((0, zero_cred(), 0));
        self.metrics.matched.inc();
        Some(AgentEvent {
            schema_version: schema::SCHEMA_VERSION,
            event_id: ulid::Ulid::new().to_string(),
            time: schema::rfc3339_nanos(ev.time_sec, ev.time_nsec),
            seq: Seq {
                global: 0,
                local: 0,
            }, // 合成事件无 ES 序号
            sensor: Sensor {
                name: schema::SENSOR_NAME,
                version: env!("CARGO_PKG_VERSION"),
                pid: self.sensor_pid,
            },
            agent: Some(AgentRef {
                id: agent.id.clone(),
                workdir: agent.workdir.clone(),
                match_kind: MatchKind::ActorProcess,
            }),
            actor: Actor {
                pid: ev.pid,
                pidversion,
                ppid,
                program,
                cred,
                origin: None,
                responsible_pid: ev.pid,
            },
            operation: Operation {
                category: Category::Net,
                verb: ev.verb,
                outcome: Outcome::Snapshot, // 轮询快照，区别于实时事件
                es_event: None,
            },
            target: Target::Endpoint {
                proto: ev.key.proto.clone(),
                local: ev.key.local.clone(),
                remote: ev.key.remote.clone(),
            },
            context: Context {
                agent_workdir_hit: None,
                mach_time: 0,
                thread_id: 0,
            },
        })
    }
}

fn zero_cred() -> Cred {
    Cred {
        ruid: 0,
        euid: 0,
        rgid: 0,
        egid: 0,
        auid: 0,
    }
}

/// 写线程主循环：消费 → 组装 → 序列化 → 落盘。所有 producer 断开、
/// channel 排空后返回。
pub fn run_writer(
    rx: Receiver<WorkItem>,
    assembler: Assembler,
    sink: &mut JsonlSink,
    metrics: Arc<Metrics>,
) {
    while let Ok(item) = rx.recv() {
        let event = match &item {
            WorkItem::Raw(msg) => assembler.handle_raw(msg),
            WorkItem::Net(ev) => assembler.handle_net(ev),
        };
        let Some(event) = event else {
            metrics.filtered.inc();
            continue;
        };
        match serde_json::to_string(&event) {
            Ok(line) => {
                if let Err(e) = sink.write_line(&line) {
                    metrics.errors.inc();
                    tracing::warn!(error = %e, "写入事件失败");
                } else {
                    metrics.written.inc();
                }
            }
            Err(e) => {
                metrics.errors.inc();
                tracing::warn!(error = %e, "序列化事件失败");
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::ffi::{self, es_type, testutil, EsshFile, EsshMessage, EsshProcess};
    use crate::matcher::AgentSet;
    use crate::proctree::Annotator;
    use std::collections::HashSet;

    #[test]
    fn backpressure_counts_drops() {
        let (producer, _rx) = channel(2);
        let msg = ffi::testutil::zeroed_message();
        for _ in 0..5 {
            producer.try_push_raw(msg); // 无消费者：2 入队，3 丢弃
        }
        assert_eq!(producer.dropped(), 3);
    }

    #[test]
    fn drained_channel_contains_all_accepted() {
        let (producer, rx) = channel(8);
        let mut msg = ffi::testutil::zeroed_message();
        msg.event_type = ffi::es_type::NOTIFY_OPEN;
        producer.try_push_raw(msg);
        producer.try_push_net(NetEvent {
            pid: 1,
            agent_id: "a".into(),
            verb: Verb::NetConnect,
            key: SocketKey {
                proto: "tcp4".into(),
                local: "127.0.0.1:1".into(),
                remote: "1.1.1.1:443".into(),
            },
            time_sec: 0,
            time_nsec: 0,
        });
        drop(producer);
        let mut kinds = HashSet::new();
        while let Ok(item) = rx.recv() {
            kinds.insert(match item {
                WorkItem::Raw(_) => "raw",
                WorkItem::Net(_) => "net",
            });
        }
        assert!(kinds.contains("raw") && kinds.contains("net"));
    }

    // ---- Assembler 链路测试基建 ----

    const AGENT_DIR: &str = "/opt/agent";
    const AGENT_EXE: &str = "/opt/agent/bin/hermes";

    fn proc(pid: i32, pidversion: u32, exe: &str) -> EsshProcess {
        let mut p = testutil::zeroed_process();
        p.pid = pid;
        p.pidversion = pidversion;
        p.ppid = 1;
        p.responsible_pid = pid;
        p.ruid = 501;
        p.euid = 501;
        p.rgid = 20;
        p.egid = 20;
        p.auid = 501;
        let b = exe.as_bytes();
        p.executable[..b.len()].copy_from_slice(b);
        p.executable_len = b.len() as u32;
        p
    }

    fn file(path: &str) -> EsshFile {
        let mut f = testutil::zeroed_file();
        let b = path.as_bytes();
        f.path[..b.len()].copy_from_slice(b);
        f.path_len = b.len() as u32;
        f.mode = 0o100644;
        f.ino = 7;
        f.size = 42;
        f
    }

    fn base_msg(event_type: u32, actor: EsshProcess, mach_time: u64) -> EsshMessage {
        let mut msg = testutil::zeroed_message();
        msg.event_type = event_type;
        msg.process = actor;
        msg.mach_time = mach_time;
        msg.time_sec = 1_788_220_800;
        msg.time_nsec = 123_456_789;
        msg.seq_num = 9;
        msg.global_seq_num = 100;
        msg.message_version = 10;
        msg.thread_id = 777;
        msg
    }

    fn exec_msg(
        mach: u64,
        old: (i32, u32),
        new: (i32, u32),
        exe: &str,
        argv: &[&str],
    ) -> EsshMessage {
        let mut msg = base_msg(es_type::NOTIFY_EXEC, proc(old.0, old.1, "/bin/zsh"), mach);
        let mut exec = testutil::zeroed_exec();
        exec.target = proc(new.0, new.1, exe);
        let mut pos = 0usize;
        for (i, a) in argv.iter().enumerate() {
            exec.argv_buf[pos..pos + a.len()].copy_from_slice(a.as_bytes());
            exec.argv_offsets[i] = pos as u32;
            exec.argv_lens[i] = a.len() as u16;
            pos += a.len();
        }
        exec.argc = argv.len() as u32;
        msg.event.exec = exec;
        msg
    }

    fn fork_msg(mach: u64, parent: (i32, u32), child: (i32, u32)) -> EsshMessage {
        let mut msg = base_msg(
            es_type::NOTIFY_FORK,
            proc(parent.0, parent.1, AGENT_EXE),
            mach,
        );
        msg.event.fork.child = proc(child.0, child.1, AGENT_EXE);
        msg
    }

    fn open_msg(mach: u64, actor: (i32, u32), exe: &str, path: &str) -> EsshMessage {
        let mut msg = base_msg(es_type::NOTIFY_OPEN, proc(actor.0, actor.1, exe), mach);
        msg.event.open.file = file(path);
        msg.event.open.fflag = 0;
        msg
    }

    fn make_assembler() -> (Assembler, Arc<RwLock<ProcessTree>>, Arc<Metrics>) {
        let agents = Arc::new(AgentSet::for_test("hermes", AGENT_DIR));
        let annotator: Annotator = {
            let agents = agents.clone();
            Arc::new(move |program: &Program| agents.agent_for_program(program))
        };
        let tree = Arc::new(RwLock::new(ProcessTree::with_grace(annotator, 1_000)));
        let metrics = Arc::new(Metrics::default());
        (
            Assembler {
                tree: tree.clone(),
                agents,
                metrics: metrics.clone(),
                sensor_pid: 4321,
            },
            tree,
            metrics,
        )
    }

    #[test]
    fn handle_raw_exec_inserts_annotated_node() {
        let (asm, tree, _) = make_assembler();
        // exec A：非 agent 父进程（zsh）exec 成 agent 二进制。
        // 树变更照常应用（新节点带注解），但事件本身被过滤：
        // actor 是 exec 前的 zsh（未注解），target 是进程（无路径）——
        // 即「agent 根启动事件」按 §1.3 双向匹配规则不上报。
        let msg_a = exec_msg(10, (100, 0), (100, 1), AGENT_EXE, &["hermes", "serve"]);
        assert!(asm.handle_raw(&msg_a).is_none());
        let node = tree
            .read()
            .unwrap()
            .get(&PidKey {
                pid: 100,
                pidversion: 1,
            })
            .expect("exec 后节点应存在");
        assert_eq!(node.agent_id.as_deref(), Some("hermes"));
        assert_eq!(node.program.argv, vec!["hermes", "serve"]);

        // exec B：agent 进程再 exec（hermes → venv python3），actor 已注解 → 命中。
        let msg = exec_msg(
            20,
            (100, 1),
            (100, 2),
            "/opt/agent/venv/bin/python3",
            &["python3", "-m", "tui.entry"],
        );
        let ev = asm.handle_raw(&msg).expect("exec 应命中");

        // AgentEvent 断言
        assert_eq!(ev.operation.verb, Verb::Exec);
        assert_eq!(ev.operation.category, Category::Process);
        assert_eq!(ev.operation.outcome, Outcome::Completed);
        assert_eq!(ev.agent.as_ref().unwrap().id, "hermes");
        assert_eq!(ev.agent.as_ref().unwrap().workdir, AGENT_DIR);
        assert_eq!(
            ev.agent.as_ref().unwrap().match_kind,
            MatchKind::ActorProcess
        );
        assert_eq!(ev.actor.pid, 100);
        // actor 是 exec 前的进程：program 取树节点（hermes）
        assert_eq!(ev.actor.program.executable, AGENT_EXE);
        assert_eq!(ev.actor.cred.ruid, 501);
        // target 是新进程：program 取 exec 事件（python3 + argv）
        match &ev.target {
            Target::Process {
                pid,
                pidversion,
                program,
                ..
            } => {
                assert_eq!(*pid, 100);
                assert_eq!(*pidversion, 2);
                let program = program.as_ref().unwrap();
                assert_eq!(program.executable, "/opt/agent/venv/bin/python3");
                assert_eq!(program.argv, vec!["python3", "-m", "tui.entry"]);
            }
            _ => panic!("expect process target"),
        }
        // origin：agent 根是 (100,1) hermes 节点
        let origin = ev.actor.origin.as_ref().expect("已注解节点应有 origin");
        assert_eq!(origin.agent_root_pid, 100);
        assert_eq!(origin.agent_root_executable, AGENT_EXE);
        assert_eq!(origin.ancestry, vec![AGENT_EXE]);
        assert_eq!(origin.depth_from_agent_root, 0);
        // 信封
        assert_eq!(ev.schema_version, "agent-event/1.0");
        assert_eq!(ev.seq.global, 100);
        assert_eq!(ev.seq.local, 9);
        assert_eq!(ev.sensor.pid, 4321);
        assert!(ev.time.ends_with('Z'));
        assert!(!ev.event_id.is_empty());
        let es = ev.operation.es_event.as_ref().unwrap();
        assert_eq!(es.id, es_type::NOTIFY_EXEC);
        assert_eq!(es.type_name, "ES_EVENT_TYPE_NOTIFY_EXEC");
        assert_eq!(es.message_version, 10);
        assert_eq!(ev.context.mach_time, 20);
        assert_eq!(ev.context.thread_id, 777);
    }

    #[test]
    fn handle_raw_fork_file_events_and_match_kinds() {
        let (asm, _, _) = make_assembler();
        asm.handle_raw(&exec_msg(10, (100, 1), (100, 2), AGENT_EXE, &["hermes"]));
        let fork = asm
            .handle_raw(&fork_msg(20, (100, 2), (200, 1)))
            .expect("fork 应命中");
        assert_eq!(fork.operation.verb, Verb::Spawn);
        match &fork.target {
            Target::Process {
                pid, pidversion, ..
            } => {
                assert_eq!(*pid, 200);
                assert_eq!(*pidversion, 1);
            }
            _ => panic!("expect process target"),
        }

        // actor_process：agent 成员读任意路径
        let ev = asm
            .handle_raw(&open_msg(30, (200, 1), AGENT_EXE, "/etc/hosts"))
            .expect("actor 命中");
        assert_eq!(
            ev.agent.as_ref().unwrap().match_kind,
            MatchKind::ActorProcess
        );
        assert_eq!(ev.context.agent_workdir_hit.as_deref(), Some("/etc/hosts"));
        // fork 子节点继承注解与 program，origin 到 agent 根
        let origin = ev.actor.origin.as_ref().unwrap();
        assert_eq!(origin.agent_root_pid, 100);
        assert_eq!(origin.depth_from_agent_root, 1);
        assert_eq!(origin.ancestry, vec![AGENT_EXE, AGENT_EXE]);

        // target_path：外部进程触碰 agent 数据
        let ev = asm
            .handle_raw(&open_msg(
                40,
                (999, 1),
                "/usr/bin/vim",
                "/opt/agent/config.yaml",
            ))
            .expect("path 命中");
        assert_eq!(ev.agent.as_ref().unwrap().match_kind, MatchKind::TargetPath);
        assert_eq!(ev.actor.program.executable, "/usr/bin/vim"); // 无树节点 → 事件自带
        assert!(ev.actor.origin.is_none());

        // both：agent 成员触碰 agent 数据
        let ev = asm
            .handle_raw(&open_msg(50, (200, 1), AGENT_EXE, "/opt/agent/db.sqlite"))
            .expect("both 命中");
        assert_eq!(ev.agent.as_ref().unwrap().match_kind, MatchKind::Both);

        // 不命中：外部进程 + 无关路径 → None
        assert!(asm
            .handle_raw(&open_msg(60, (999, 1), "/usr/bin/vim", "/var/log/x"))
            .is_none());
    }

    #[test]
    fn handle_raw_close_unmodified_filtered() {
        let (asm, _, metrics) = make_assembler();
        asm.handle_raw(&exec_msg(10, (100, 1), (100, 2), AGENT_EXE, &["hermes"]));
        let mut msg = base_msg(es_type::NOTIFY_CLOSE, proc(100, 2, AGENT_EXE), 20);
        msg.event.close.file = file("/opt/agent/x.log");
        msg.event.close.modified = 0;
        assert!(asm.handle_raw(&msg).is_none()); // 未修改的 close 降噪
        msg.event.close.modified = 1;
        let ev = asm.handle_raw(&msg).expect("modified close 应发 write");
        assert_eq!(ev.operation.verb, Verb::Write);
        assert_eq!(metrics.received.get(), 3);
        assert_eq!(metrics.matched.get(), 1); // 仅 modified close（exec 根启动被过滤）
    }

    #[test]
    fn handle_raw_exit_backfills_target_program() {
        let (asm, tree, _) = make_assembler();
        asm.handle_raw(&exec_msg(10, (100, 1), (100, 2), AGENT_EXE, &["hermes"]));
        let mut msg = base_msg(es_type::NOTIFY_EXIT, proc(100, 2, AGENT_EXE), 20);
        msg.event.exit.stat = 0;
        let ev = asm.handle_raw(&msg).expect("exit 应命中");
        assert_eq!(ev.operation.verb, Verb::Exit);
        match &ev.target {
            Target::Process {
                pid,
                program,
                exit_stat,
                ..
            } => {
                assert_eq!(*pid, 100);
                assert_eq!(program.as_ref().unwrap().executable, AGENT_EXE); // 节点回填
                assert_eq!(*exit_stat, Some(0));
            }
            _ => panic!("expect process target"),
        }
        // grace 期内节点仍在（滞后事件可查到）
        assert!(tree
            .read()
            .unwrap()
            .get(&PidKey {
                pid: 100,
                pidversion: 2
            })
            .is_some());
    }

    #[test]
    fn handle_net_variants() {
        let (asm, _, _) = make_assembler();
        asm.handle_raw(&exec_msg(10, (100, 1), (100, 2), AGENT_EXE, &["hermes"]));

        let net = NetEvent {
            pid: 100,
            agent_id: "hermes".into(),
            verb: Verb::NetConnect,
            key: SocketKey {
                proto: "tcp4".into(),
                local: "10.0.0.1:51000".into(),
                remote: "1.2.3.4:443".into(),
            },
            time_sec: 1_788_220_800,
            time_nsec: 0,
        };
        let ev = asm.handle_net(&net).expect("net 命中");
        assert_eq!(ev.operation.category, Category::Net);
        assert_eq!(ev.operation.verb, Verb::NetConnect);
        assert_eq!(ev.operation.outcome, Outcome::Snapshot);
        assert!(ev.operation.es_event.is_none());
        assert_eq!(
            ev.agent.as_ref().unwrap().match_kind,
            MatchKind::ActorProcess
        );
        assert_eq!(ev.actor.pidversion, 2); // 树节点回填
        assert_eq!(ev.actor.program.executable, AGENT_EXE);
        match &ev.target {
            Target::Endpoint {
                proto,
                local,
                remote,
            } => {
                assert_eq!(proto, "tcp4");
                assert_eq!(local, "10.0.0.1:51000");
                assert_eq!(remote, "1.2.3.4:443");
            }
            _ => panic!("expect endpoint target"),
        }

        // pid 不在树内：空 program + pidversion 0 兜底
        let ev = asm
            .handle_net(&NetEvent {
                pid: 555,
                ..net.clone()
            })
            .expect("agent_id 有效即命中");
        assert_eq!(ev.actor.pidversion, 0);
        assert_eq!(ev.actor.program.executable, "");

        // 未知 agent_id → None
        assert!(asm
            .handle_net(&NetEvent {
                agent_id: "ghost".into(),
                ..net
            })
            .is_none());
    }

    #[test]
    fn run_writer_end_to_end() {
        let dir = std::env::temp_dir().join(format!("es-sensor-writer-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join("events.jsonl");

        let (asm, _, metrics) = make_assembler();
        let (producer, rx) = channel(64);
        let mut sink = JsonlSink::file(&path).unwrap();
        let writer_metrics = metrics.clone();
        let writer = std::thread::spawn(move || run_writer(rx, asm, &mut sink, writer_metrics));

        // 事件流：exec(根启动,过滤) + exec(命中) + fork + open(命中) + open(不命中)
        //         + close(未修改) + net + net(未知 agent)
        producer.try_push_raw(exec_msg(10, (100, 0), (100, 1), AGENT_EXE, &["hermes"]));
        producer.try_push_raw(exec_msg(
            15,
            (100, 1),
            (100, 2),
            "/opt/agent/venv/bin/python3",
            &["python3"],
        ));
        producer.try_push_raw(fork_msg(20, (100, 2), (200, 1)));
        producer.try_push_raw(open_msg(30, (200, 1), AGENT_EXE, "/opt/agent/db"));
        producer.try_push_raw(open_msg(40, (999, 1), "/usr/bin/vim", "/var/log/x"));
        let mut close = base_msg(es_type::NOTIFY_CLOSE, proc(200, 1, AGENT_EXE), 50);
        close.event.close.file = file("/opt/agent/x");
        close.event.close.modified = 0;
        producer.try_push_raw(close);
        producer.try_push_net(NetEvent {
            pid: 200,
            agent_id: "hermes".into(),
            verb: Verb::NetConnect,
            key: SocketKey {
                proto: "tcp6".into(),
                local: "[::1]:1".into(),
                remote: "[::1]:2".into(),
            },
            time_sec: 1_788_220_800,
            time_nsec: 1,
        });
        producer.try_push_net(NetEvent {
            pid: 200,
            agent_id: "ghost".into(),
            verb: Verb::NetClose,
            key: SocketKey {
                proto: "tcp6".into(),
                local: "[::1]:1".into(),
                remote: "[::1]:2".into(),
            },
            time_sec: 1_788_220_800,
            time_nsec: 2,
        });
        drop(producer);
        writer.join().unwrap();

        let content = std::fs::read_to_string(&path).unwrap();
        let lines: Vec<&str> = content.lines().collect();
        assert_eq!(lines.len(), 4, "exec+fork+open+net 四条命中");
        let verbs: Vec<String> = lines
            .iter()
            .map(|l| {
                let v: serde_json::Value = serde_json::from_str(l).expect("每行须为合法 JSON");
                assert_eq!(v["schema_version"], "agent-event/1.0");
                v["operation"]["verb"].as_str().unwrap().to_owned()
            })
            .collect();
        assert_eq!(verbs, vec!["exec", "spawn", "open", "net_connect"]);
        assert_eq!(metrics.written.get(), 4);
        // 根启动 exec + 不命中 open + 未修改 close + ghost net
        assert_eq!(metrics.filtered.get(), 4);
        assert_eq!(metrics.errors.get(), 0);
        std::fs::remove_dir_all(&dir).ok();
    }
}
