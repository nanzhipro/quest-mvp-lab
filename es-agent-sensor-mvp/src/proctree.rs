//! 进程树：移植 Google Santa `Source/common/processtree` 的生产设计。
//!
//! 核心设计照搬：
//! - 键为 `(pid, pidversion)` 二元组，天然解决 pid 复用——同 pid 不同
//!   incarnation 是不同节点；
//! - 平铺 `HashMap<PidKey, Arc<Process>>` + 节点内单向 `parent: Arc<Process>`
//!   父链，不维护子链；Arc 保证已退出父节点的祖先链仍可安全遍历
//!   （Rust Arc 天然替代 Santa 的 shared_ptr + refcount/tombstone）；
//! - FORK 共享父的 `Arc<Program>`（零拷贝继承）；EXEC 新建节点而非原地更新，
//!   旧 key 进延迟回收堆；EXIT 不立即删，~5s grace 延迟回收（以已见最新
//!   事件 mach_time 为基准），避免滞后事件找不到进程；
//! - 完整事件身份 `(mach_time, kind, actor, other)` 的有界 LRU 去重
//!   （上限 16384），只丢精确重复，不丢乱序新事件；
//! - agent_id 注解：executable 或任一 argv 元素位于某 Agent 工作目录下即打标，
//!   FORK 继承；EXEC 时新 executable 命中则按新值打标（可换绑 agent），否则
//!   继承旧注解（对齐 Santa Originator——agent 派生进程 exec 到目录外程序
//!   仍是 agent 行为），成员判定 O(1)。
//!
//! 已知洞（Santa 同样承认）：parent 不在树里的 fork → 子树缺失；
//! 靠 seeding + 强制订阅 FORK/EXEC/EXIT 缓解。

use std::collections::hash_map::Entry;
use std::collections::{BinaryHeap, HashMap, HashSet, VecDeque};
use std::sync::Arc;

use crate::schema::{Cred, Origin, Program};

/// 树键：`(pid, pidversion)`。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord)]
pub struct PidKey {
    pub pid: i32,
    pub pidversion: u64,
}

/// 树变更事件种类（去重键的一部分）。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum EventKind {
    Fork,
    Exec,
    Exit,
}

/// 树变更事件的完整身份，用于跨 client 重投去重。
/// 不能只按 mach_time 去重：系统级计数器粒度粗（~41ns），不同核上的
/// 两个不同事件可能共享同一戳，只按 mach_time 会静默丢事件。
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub struct EventKey {
    pub mach_time: u64,
    pub kind: EventKind,
    /// parent (fork) / execing proc (exec) / exiting proc (exit)。
    pub actor: PidKey,
    /// child (fork) / target (exec) / 空 (exit)。
    pub other: PidKey,
}

/// 去重 LRU 上限。
pub const DEDUP_CAPACITY: usize = 16_384;
/// 默认延迟回收窗口（5s，单位与传入的 mach_time 刻度一致，由调用方按
/// mach_timebase 换算后设置）。
pub const DEFAULT_GRACE_TICKS: u64 = 5_000_000_000;

/// 进程节点。`parent`/`program` 共享所有权，节点从 map 移除后祖先链仍可遍历。
#[derive(Debug)]
pub struct Process {
    pub pid: PidKey,
    pub parent: Option<Arc<Process>>,
    pub program: Arc<Program>,
    pub cred: Cred,
    /// agent 注解：executable 位于某 agent 工作目录下即打标。
    pub agent_id: Option<String>,
}

impl Process {
    pub fn executable(&self) -> &str {
        &self.program.executable
    }
}

/// agent 注解器：program（executable + argv）→ agent_id。由 matcher::AgentSet
/// 提供：executable 命中 agent 目录，或任一 argv 元素命中（解释型脚本场景，
/// 如 venv python 的真实可执行文件解析到 agent 目录外）。
pub type Annotator = Arc<dyn Fn(&Program) -> Option<String> + Send + Sync>;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ForkOutcome {
    Inserted,
    Duplicate,
    /// parent 不在树内，子树缺失（已知洞）。
    NoParent,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExecOutcome {
    Applied,
    Duplicate,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExitOutcome {
    Retired,
    Duplicate,
    Unknown,
}

/// 种子/回填所需进程信息的系统调用源。注入设计使单测不依赖真实系统调用。
pub trait ProcSource {
    fn all_pids(&self) -> Vec<i32>;
    fn ppid(&self, pid: i32) -> Option<i32>;
    /// audit token 提取（pidversion/cred）；失败（无权限/进程已退）返回 None。
    fn audit(&self, pid: i32) -> Option<AuditInfo>;
    fn path(&self, pid: i32) -> Option<String>;
    fn argv(&self, pid: i32) -> Vec<String>;
    /// csops 签名标志（CS_VALID 等）；失败返回 None。
    fn cs_flags(&self, pid: i32) -> Option<u32>;
}

#[derive(Debug, Clone, Copy)]
pub struct AuditInfo {
    pub pidversion: u64,
    pub ruid: u32,
    pub euid: u32,
    pub rgid: u32,
    pub egid: u32,
    pub auid: u32,
}

pub struct ProcessTree {
    map: HashMap<PidKey, Arc<Process>>,
    /// 延迟回收最小堆：`(截止 mach_time, key)`。
    retire: BinaryHeap<std::cmp::Reverse<(u64, PidKey)>>,
    /// 事件去重：set 查询 + 插入序老化队列。
    dedup_set: HashSet<EventKey>,
    dedup_order: VecDeque<EventKey>,
    /// 已见最新事件时间戳（延迟回收的基准）。
    latest_mach: u64,
    grace_ticks: u64,
    annotator: Annotator,
}

impl ProcessTree {
    pub fn new(annotator: Annotator) -> Self {
        Self::with_grace(annotator, DEFAULT_GRACE_TICKS)
    }

    pub fn with_grace(annotator: Annotator, grace_ticks: u64) -> Self {
        Self {
            map: HashMap::new(),
            retire: BinaryHeap::new(),
            dedup_set: HashSet::new(),
            dedup_order: VecDeque::new(),
            latest_mach: 0,
            grace_ticks,
            annotator,
        }
    }

    pub fn len(&self) -> usize {
        self.map.len()
    }

    pub fn is_empty(&self) -> bool {
        self.map.is_empty()
    }

    pub fn get(&self, key: &PidKey) -> Option<Arc<Process>> {
        self.map.get(key).cloned()
    }

    /// 按裸 pid 查找（忽略 pidversion，net-poller 用）。
    pub fn find_by_pid(&self, pid: i32) -> Option<Arc<Process>> {
        self.map
            .iter()
            .find(|(k, _)| k.pid == pid)
            .map(|(_, p)| p.clone())
    }

    /// 当前带 agent 注解的成员 pid 列表（net-poller 轮询集合）。
    pub fn agent_members(&self) -> Vec<(i32, String)> {
        self.map
            .values()
            .filter_map(|p| p.agent_id.as_ref().map(|id| (p.pid.pid, id.clone())))
            .collect()
    }

    fn advance_clock(&mut self, mach_time: u64) {
        self.latest_mach = self.latest_mach.max(mach_time);
    }

    /// 事件去重：首次见返回 false（并记录），精确重复返回 true。
    fn seen_or_insert(&mut self, key: EventKey) -> bool {
        if !self.dedup_set.insert(key) {
            return true;
        }
        self.dedup_order.push_back(key);
        if self.dedup_order.len() > DEDUP_CAPACITY {
            if let Some(old) = self.dedup_order.pop_front() {
                self.dedup_set.remove(&old);
            }
        }
        false
    }

    /// FORK：构造子节点（继承 parent 的 program/cred/agent 注解），
    /// 去重后插入。parent 缺失时不插入（子树缺失，Santa 同款已知洞）。
    pub fn handle_fork(&mut self, mach_time: u64, parent: PidKey, child: PidKey) -> ForkOutcome {
        self.advance_clock(mach_time);
        let ekey = EventKey {
            mach_time,
            kind: EventKind::Fork,
            actor: parent,
            other: child,
        };
        if self.seen_or_insert(ekey) {
            return ForkOutcome::Duplicate;
        }
        let Some(parent_node) = self.map.get(&parent).cloned() else {
            return ForkOutcome::NoParent;
        };
        // 写锁外构造（此处单线程锁内，构造本身不触碰 map）：
        // 共享父的 Arc<Program>，零拷贝继承；注解沿树传播。
        let node = Arc::new(Process {
            pid: child,
            parent: Some(parent_node.clone()),
            program: parent_node.program.clone(),
            cred: parent_node.cred.clone(),
            agent_id: parent_node.agent_id.clone(),
        });
        match self.map.entry(child) {
            Entry::Vacant(v) => {
                v.insert(node);
                ForkOutcome::Inserted
            }
            Entry::Occupied(_) => ForkOutcome::Duplicate,
        }
    }

    /// EXEC：新建节点而非原地更新（pidversion 变），继承旧节点 parent，
    /// 替换 program；注解按新 executable 重算（agent 目录外程序则摘除）。
    /// 旧 key 进延迟回收堆。旧 key 不在树内时以无父节点插入（兜底）。
    pub fn handle_exec(
        &mut self,
        mach_time: u64,
        old: PidKey,
        new: PidKey,
        program: Program,
    ) -> ExecOutcome {
        self.advance_clock(mach_time);
        let ekey = EventKey {
            mach_time,
            kind: EventKind::Exec,
            actor: old,
            other: new,
        };
        if self.seen_or_insert(ekey) {
            return ExecOutcome::Duplicate;
        }
        let old_node = self.map.get(&old).cloned();
        let parent = old_node.as_ref().and_then(|n| n.parent.clone());
        let cred = old_node.as_ref().map(|n| n.cred.clone()).unwrap_or(Cred {
            ruid: 0,
            euid: 0,
            rgid: 0,
            egid: 0,
            auid: 0,
        });
        // 注解语义（对齐 Santa Originator）：新 executable 命中则按新值打标
        // （支持换绑到另一个 agent），否则继承旧节点注解——agent 派生的进程
        // exec 到目录外程序（如 curl）仍是 agent 行为，归因不丢。
        let agent_id = (self.annotator)(&program)
            .or_else(|| old_node.as_ref().and_then(|n| n.agent_id.clone()));
        let node = Arc::new(Process {
            pid: new,
            parent,
            program: Arc::new(program),
            cred,
            agent_id,
        });
        self.map.insert(new, node);
        if old_node.is_some() {
            self.retire_at(self.latest_mach + self.grace_ticks, old);
        }
        ExecOutcome::Applied
    }

    /// EXIT：不立即删，进延迟回收堆。
    pub fn handle_exit(&mut self, mach_time: u64, key: PidKey) -> ExitOutcome {
        self.advance_clock(mach_time);
        let ekey = EventKey {
            mach_time,
            kind: EventKind::Exit,
            actor: key,
            other: PidKey {
                pid: 0,
                pidversion: 0,
            },
        };
        if self.seen_or_insert(ekey) {
            return ExitOutcome::Duplicate;
        }
        if !self.map.contains_key(&key) {
            return ExitOutcome::Unknown;
        }
        self.retire_at(self.latest_mach + self.grace_ticks, key);
        ExitOutcome::Retired
    }

    fn retire_at(&mut self, deadline: u64, key: PidKey) {
        self.retire.push(std::cmp::Reverse((deadline, key)));
    }

    /// 回收已过 grace 期的节点，返回回收数。
    pub fn reap(&mut self, now: u64) -> usize {
        self.advance_clock(now);
        let mut n = 0;
        while let Some(std::cmp::Reverse((deadline, key))) = self.retire.peek().copied() {
            if deadline > self.latest_mach {
                break;
            }
            self.retire.pop();
            if self.map.remove(&key).is_some() {
                n += 1;
            }
        }
        n
    }

    /// actor 归因链：沿父链回溯，截断到 agent 根（同 agent 注解的最远祖先）。
    /// 节点无注解时返回 None。
    pub fn origin(&self, key: &PidKey) -> Option<Origin> {
        let node = self.map.get(key)?;
        let agent_id = node.agent_id.as_ref()?;
        let mut chain: Vec<&Arc<Process>> = vec![node];
        let mut cur = node;
        while let Some(parent) = &cur.parent {
            if parent.agent_id.as_ref() != Some(agent_id) {
                break;
            }
            chain.push(parent);
            cur = parent;
        }
        chain.reverse();
        let root = chain[0];
        Some(Origin {
            agent_root_pid: root.pid.pid,
            agent_root_executable: root.executable().to_owned(),
            ancestry: chain.iter().map(|p| p.executable().to_owned()).collect(),
            depth_from_agent_root: (chain.len() - 1) as u32,
        })
    }

    /// 种子回填：枚举现存进程，从 ppid==0（或父缺失）的多根递归插入。
    /// program 值相等时复用同一 Arc（内存去重）；单 pid 失败跳过不致命。
    pub fn seed(&mut self, source: &dyn ProcSource) -> usize {
        struct SeedEntry {
            key: PidKey,
            ppid: i32,
            program: Program,
            cred: Cred,
        }
        let mut entries: Vec<SeedEntry> = Vec::new();
        for pid in source.all_pids() {
            if pid <= 0 {
                continue;
            }
            // audit token 是 pidversion 的唯一权威来源；失败跳过不致命。
            let Some(audit) = source.audit(pid) else {
                continue;
            };
            let Some(executable) = source.path(pid) else {
                continue;
            };
            let Some(ppid) = source.ppid(pid) else {
                continue;
            };
            let is_platform = source
                .cs_flags(pid)
                .map(|f| f & 0x0400_0000 != 0) // CS_PLATFORM_BINARY
                .unwrap_or(false);
            entries.push(SeedEntry {
                key: PidKey {
                    pid,
                    pidversion: audit.pidversion,
                },
                ppid,
                program: Program {
                    executable,
                    argv: source.argv(pid),
                    signing_id: None, // 回填不取签名身份（exec 事件会补齐）
                    team_id: None,
                    cdhash: None,
                    is_platform_binary: is_platform,
                },
                cred: Cred {
                    ruid: audit.ruid,
                    euid: audit.euid,
                    rgid: audit.rgid,
                    egid: audit.egid,
                    auid: audit.auid,
                },
            });
        }
        if entries.is_empty() {
            return 0;
        }

        // program 值相等时复用同一 Arc。
        let mut program_pool: HashMap<Program, Arc<Program>> = HashMap::new();
        let mut nodes: HashMap<i32, Arc<Process>> = HashMap::new(); // pid → node
        let mut children: HashMap<i32, Vec<usize>> = HashMap::new(); // ppid → entry idx
        let mut roots: Vec<usize> = Vec::new();
        let pids_in_set: HashSet<i32> = entries.iter().map(|e| e.key.pid).collect();
        for (i, e) in entries.iter().enumerate() {
            if e.ppid == 0 || !pids_in_set.contains(&e.ppid) {
                roots.push(i);
            } else {
                children.entry(e.ppid).or_default().push(i);
            }
        }

        let mut inserted = 0;
        let mut stack: Vec<(usize, Option<Arc<Process>>)> =
            roots.into_iter().map(|i| (i, None)).collect();
        while let Some((i, parent)) = stack.pop() {
            let e = &entries[i];
            // pid 复用防护：同一 pid 出现多个 incarnation 时以先见为准。
            if nodes.contains_key(&e.key.pid) {
                continue;
            }
            let program = program_pool
                .entry(e.program.clone())
                .or_insert_with(|| Arc::new(e.program.clone()))
                .clone();
            // 注解：自身命中优先，否则继承父节点（与 fork/exec 语义一致）。
            let agent_id = (self.annotator)(&program)
                .or_else(|| parent.as_ref().and_then(|p| p.agent_id.clone()));
            let node = Arc::new(Process {
                pid: e.key,
                parent,
                program,
                cred: e.cred.clone(),
                agent_id,
            });
            nodes.insert(e.key.pid, node.clone());
            self.map.insert(e.key, node.clone());
            inserted += 1;
            if let Some(kids) = children.get(&e.key.pid) {
                for &k in kids {
                    stack.push((k, Some(node.clone())));
                }
            }
        }
        inserted
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap as Map;

    fn no_agent() -> Annotator {
        Arc::new(|_| None)
    }

    /// agent 目录为 /opt/agent：其下 executable 或 argv 元素打标 "hermes"。
    fn hermes_annotator() -> Annotator {
        Arc::new(|p: &Program| {
            let hit = |s: &str| s == "/opt/agent" || s.starts_with("/opt/agent/");
            if hit(&p.executable) || p.argv.iter().any(|a| hit(a)) {
                Some("hermes".to_owned())
            } else {
                None
            }
        })
    }

    fn program(exe: &str) -> Program {
        Program {
            executable: exe.to_owned(),
            argv: vec![exe.to_owned()],
            signing_id: None,
            team_id: None,
            cdhash: None,
            is_platform_binary: false,
        }
    }

    fn cred() -> Cred {
        Cred {
            ruid: 501,
            euid: 501,
            rgid: 20,
            egid: 20,
            auid: 501,
        }
    }

    /// 手动插入一个节点（测试基建）。
    fn insert(tree: &mut ProcessTree, key: PidKey, parent: Option<Arc<Process>>, exe: &str) {
        let prog = program(exe);
        let agent_id = (tree.annotator)(&prog);
        tree.map.insert(
            key,
            Arc::new(Process {
                pid: key,
                parent,
                program: Arc::new(prog),
                cred: cred(),
                agent_id,
            }),
        );
    }

    fn k(pid: i32, v: u64) -> PidKey {
        PidKey { pid, pidversion: v }
    }

    #[test]
    fn fork_shares_parent_program_arc() {
        let mut tree = ProcessTree::new(no_agent());
        let parent = k(100, 1);
        insert(&mut tree, parent, None, "/opt/agent/bin/hermes");
        let child = k(200, 1);
        assert_eq!(tree.handle_fork(10, parent, child), ForkOutcome::Inserted);
        let p = tree.get(&parent).unwrap();
        let c = tree.get(&child).unwrap();
        assert!(Arc::ptr_eq(&p.program, &c.program)); // 零拷贝继承
        assert!(Arc::ptr_eq(&c.parent.clone().unwrap(), &p));
        assert_eq!(tree.len(), 2);
    }

    #[test]
    fn fork_without_parent_is_missing_subtree() {
        let mut tree = ProcessTree::new(no_agent());
        assert_eq!(
            tree.handle_fork(10, k(999, 1), k(200, 1)),
            ForkOutcome::NoParent
        );
        assert!(tree.is_empty());
    }

    #[test]
    fn fork_duplicate_dropped() {
        let mut tree = ProcessTree::new(no_agent());
        insert(&mut tree, k(100, 1), None, "/bin/a");
        assert_eq!(
            tree.handle_fork(10, k(100, 1), k(200, 1)),
            ForkOutcome::Inserted
        );
        // 精确重投（同 mach_time/kind/actor/other）被去重
        assert_eq!(
            tree.handle_fork(10, k(100, 1), k(200, 1)),
            ForkOutcome::Duplicate
        );
        assert_eq!(tree.len(), 2);
        // 不同 mach_time 的同 pid 事件不是重复（乱序新事件不丢）
        assert_eq!(
            tree.handle_fork(11, k(100, 1), k(201, 1)),
            ForkOutcome::Inserted
        );
    }

    #[test]
    fn exec_creates_new_node_and_retires_old() {
        let mut tree = ProcessTree::with_grace(no_agent(), 100);
        let root = k(1, 1);
        insert(&mut tree, root, None, "/sbin/launchd");
        let old = k(100, 1);
        let root_node = tree.get(&root).unwrap();
        insert(&mut tree, old, Some(root_node), "/bin/zsh");

        let new = k(100, 2); // 同 pid，pidversion 变 → 不同节点
        assert_eq!(
            tree.handle_exec(10, old, new, program("/opt/agent/bin/hermes")),
            ExecOutcome::Applied
        );
        // 新节点继承旧节点的 parent
        let n = tree.get(&new).unwrap();
        assert_eq!(n.parent.as_ref().unwrap().pid, root);
        // 旧 key 在 grace 期内仍可查（滞后事件能找到进程）
        assert!(tree.get(&old).is_some());
        // grace 期过后回收
        assert_eq!(tree.reap(50), 0);
        assert_eq!(tree.reap(200), 1);
        assert!(tree.get(&old).is_none());
        assert!(tree.get(&new).is_some());
    }

    #[test]
    fn exec_duplicate_dropped() {
        let mut tree = ProcessTree::new(no_agent());
        insert(&mut tree, k(100, 1), None, "/bin/zsh");
        assert_eq!(
            tree.handle_exec(10, k(100, 1), k(100, 2), program("/bin/bash")),
            ExecOutcome::Applied
        );
        assert_eq!(
            tree.handle_exec(10, k(100, 1), k(100, 2), program("/bin/bash")),
            ExecOutcome::Duplicate
        );
    }

    #[test]
    fn exit_delayed_reap_and_pid_reuse() {
        let mut tree = ProcessTree::with_grace(no_agent(), 100);
        insert(&mut tree, k(100, 1), None, "/bin/a");
        assert_eq!(tree.handle_exit(10, k(100, 1)), ExitOutcome::Retired);
        // grace 期内旧 incarnation 仍可查
        assert!(tree.get(&k(100, 1)).is_some());
        // pid 复用：同 pid 不同 pidversion 是新节点，互不干扰
        insert(&mut tree, k(100, 2), None, "/bin/b");
        assert_eq!(tree.reap(200), 1);
        assert!(tree.get(&k(100, 1)).is_none());
        assert!(tree.get(&k(100, 2)).is_some());
        assert_eq!(tree.handle_exit(20, k(100, 1)), ExitOutcome::Unknown);
    }

    #[test]
    fn annotation_propagates_through_exec_and_rebinds_on_inside_exec() {
        let mut tree = ProcessTree::new(hermes_annotator());
        let agent = k(100, 1);
        insert(&mut tree, agent, None, "/opt/agent/bin/hermes");
        assert_eq!(
            tree.get(&agent).unwrap().agent_id.as_deref(),
            Some("hermes")
        );

        // fork 继承注解
        let child = k(200, 1);
        tree.handle_fork(10, agent, child);
        assert_eq!(
            tree.get(&child).unwrap().agent_id.as_deref(),
            Some("hermes")
        );

        // exec 到目录外程序（agent 派生的 curl）：继承注解（Santa Originator 语义）
        tree.handle_exec(20, child, k(200, 2), program("/usr/bin/curl"));
        assert_eq!(
            tree.get(&k(200, 2)).unwrap().agent_id.as_deref(),
            Some("hermes")
        );

        // argv 命中 agent 目录（解释型脚本，executable 在目录外）：按新值打标
        let mut script_prog = program("/usr/bin/python3");
        script_prog.argv = vec!["python3".into(), "/opt/agent/tools/run.py".into()];
        tree.handle_exec(30, k(200, 2), k(200, 3), script_prog);
        assert_eq!(
            tree.get(&k(200, 3)).unwrap().agent_id.as_deref(),
            Some("hermes")
        );

        // 无注解进程 exec 到 agent 目录内：按新值打标
        let plain = k(300, 1);
        insert(&mut tree, plain, None, "/bin/zsh");
        tree.handle_exec(40, plain, k(300, 2), program("/opt/agent/bin/hermes"));
        assert_eq!(
            tree.get(&k(300, 2)).unwrap().agent_id.as_deref(),
            Some("hermes")
        );
    }

    #[test]
    fn origin_traces_ancestry_to_agent_root() {
        let mut tree = ProcessTree::new(hermes_annotator());
        let shell = k(1, 1);
        insert(&mut tree, shell, None, "/bin/zsh"); // 无注解
        let agent = k(100, 1);
        let shell_node = tree.get(&shell).unwrap();
        insert(&mut tree, agent, Some(shell_node), "/opt/agent/bin/hermes");
        let child = k(200, 1);
        tree.handle_fork(10, agent, child);
        tree.handle_exec(20, child, k(200, 2), program("/opt/agent/venv/bin/python3"));

        let origin = tree.origin(&k(200, 2)).unwrap();
        assert_eq!(origin.agent_root_pid, 100);
        assert_eq!(origin.agent_root_executable, "/opt/agent/bin/hermes");
        assert_eq!(
            origin.ancestry,
            vec!["/opt/agent/bin/hermes", "/opt/agent/venv/bin/python3"]
        );
        assert_eq!(origin.depth_from_agent_root, 1);

        // 无注解节点无 origin
        assert!(tree.origin(&shell).is_none());
    }

    #[derive(Default)]
    struct FakeSource {
        pids: Vec<i32>,
        ppids: Map<i32, i32>,
        paths: Map<i32, String>,
        argvs: Map<i32, Vec<String>>,
        pidversions: Map<i32, u64>,
        fail_audit: Vec<i32>,
    }

    impl ProcSource for FakeSource {
        fn all_pids(&self) -> Vec<i32> {
            self.pids.clone()
        }
        fn ppid(&self, pid: i32) -> Option<i32> {
            self.ppids.get(&pid).copied()
        }
        fn audit(&self, pid: i32) -> Option<AuditInfo> {
            if self.fail_audit.contains(&pid) {
                return None;
            }
            self.pidversions.get(&pid).map(|&v| AuditInfo {
                pidversion: v,
                ruid: 501,
                euid: 501,
                rgid: 20,
                egid: 20,
                auid: 501,
            })
        }
        fn path(&self, pid: i32) -> Option<String> {
            self.paths.get(&pid).cloned()
        }
        fn argv(&self, pid: i32) -> Vec<String> {
            self.argvs.get(&pid).cloned().unwrap_or_default()
        }
        fn cs_flags(&self, _pid: i32) -> Option<u32> {
            None
        }
    }

    #[test]
    fn seed_builds_tree_with_program_dedup_and_annotation() {
        let mut src = FakeSource {
            pids: vec![1, 100, 200, 300],
            ..FakeSource::default()
        };
        for (pid, pv) in [(1, 1), (100, 1), (200, 1), (300, 1)] {
            src.pidversions.insert(pid, pv);
        }
        src.ppids = Map::from([(1, 0), (100, 1), (200, 100), (300, 999)]);
        src.paths.insert(1, "/sbin/launchd".into());
        src.paths.insert(100, "/opt/agent/bin/hermes".into());
        // 200 与 100 program 完全相同（executable+argv）→ 复用同一 Arc
        src.paths.insert(200, "/opt/agent/bin/hermes".into());
        src.paths.insert(300, "/opt/agent/venv/bin/python3".into());
        src.argvs.insert(100, vec!["hermes".into(), "serve".into()]);
        src.argvs.insert(200, vec!["hermes".into(), "serve".into()]);

        let mut tree = ProcessTree::new(hermes_annotator());
        let n = tree.seed(&src);
        assert_eq!(n, 4);
        let p100 = tree.get(&k(100, 1)).unwrap();
        let p200 = tree.get(&k(200, 1)).unwrap();
        assert_eq!(p200.parent.as_ref().unwrap().pid, k(100, 1));
        assert_eq!(p100.agent_id.as_deref(), Some("hermes"));
        assert_eq!(p200.agent_id.as_deref(), Some("hermes"));
        assert!(Arc::ptr_eq(&p100.program, &p200.program)); // program 内存去重
                                                            // 300 的父不在集合 → 作为根插入
        let p300 = tree.get(&k(300, 1)).unwrap();
        assert!(p300.parent.is_none());
    }

    #[test]
    fn seed_skips_pid_when_audit_fails() {
        let mut src = FakeSource {
            pids: vec![1, 2],
            fail_audit: vec![2],
            ..FakeSource::default()
        };
        src.pidversions.insert(1, 1);
        src.ppids = Map::from([(1, 0), (2, 1)]);
        src.paths.insert(1, "/sbin/launchd".into());
        src.paths.insert(2, "/bin/sh".into());

        let mut tree = ProcessTree::new(no_agent());
        assert_eq!(tree.seed(&src), 1); // 单 pid 失败跳过不致命
        assert!(tree.get(&k(2, 1)).is_none());
    }
}
