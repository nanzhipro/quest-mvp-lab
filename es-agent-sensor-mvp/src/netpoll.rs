//! net-poller：ESF 无 TCP/UDP 事件的补偿（计划 §1.5）。
//!
//! 按 `--net-poll-interval` 对 agent 进程树成员枚举 socket
//! （libproc PROC_PIDFDSOCKETINFO，经 C shim），与上一快照 diff 出
//! `net_connect` / `net_close` 合成事件。诚实标注：这是轮询快照
//! 非实时事件，短生命周期连接可能漏。

use std::collections::{HashMap, HashSet};

use crate::ffi::EsshSocket;
use crate::schema::Verb;

const IPPROTO_TCP: i32 = 6;
const IPPROTO_UDP: i32 = 17;
const AF_INET: i32 = 2;
const AF_INET6: i32 = 30;

/// socket 身份：proto + 本地/远端五元组（fd 复用不影响 diff）。
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SocketKey {
    /// tcp4 | tcp6 | udp4 | udp6
    pub proto: String,
    pub local: String,
    pub remote: String,
}

impl SocketKey {
    pub fn from_socket(s: &EsshSocket) -> Option<Self> {
        let proto = match (s.protocol, s.family) {
            (IPPROTO_TCP, AF_INET) => "tcp4",
            (IPPROTO_TCP, AF_INET6) => "tcp6",
            (IPPROTO_UDP, AF_INET) => "udp4",
            (IPPROTO_UDP, AF_INET6) => "udp6",
            _ => return None,
        };
        Some(Self {
            proto: proto.to_owned(),
            local: format!("{}:{}", s.laddr_str(), s.lport),
            remote: format!("{}:{}", s.faddr_str(), s.fport),
        })
    }
}

/// 纯 diff：新增 = connect，消失 = close。
pub fn diff(
    old: &HashSet<SocketKey>,
    new: &HashSet<SocketKey>,
) -> (Vec<SocketKey>, Vec<SocketKey>) {
    let added = new.difference(old).cloned().collect();
    let removed = old.difference(new).cloned().collect();
    (added, removed)
}

/// 轮询事件。
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PollEvent {
    pub pid: i32,
    pub verb: Verb,
    pub key: SocketKey,
}

/// 每 pid 的 socket 快照缓存。fetch 注入使 diff 逻辑可无 root 单测。
#[derive(Default)]
pub struct NetPoller {
    prev: HashMap<i32, HashSet<SocketKey>>,
}

impl NetPoller {
    pub fn new() -> Self {
        Self::default()
    }

    /// 对一组 pid 做一轮轮询。`fetch(pid)` 返回该 pid 当前 socket 集合。
    /// 首次见到某 pid 不产生 connect 事件（建立基线，避免启动风暴）。
    pub fn poll<F>(&mut self, pids: &[i32], mut fetch: F) -> Vec<PollEvent>
    where
        F: FnMut(i32) -> Vec<SocketKey>,
    {
        let mut events = Vec::new();
        let mut seen: HashSet<i32> = HashSet::new();
        for &pid in pids {
            seen.insert(pid);
            let current: HashSet<SocketKey> = fetch(pid).into_iter().collect();
            tracing::debug!(pid, sockets = current.len(), "net-poller 抓取快照");
            match self.prev.insert(pid, current) {
                None => continue, // 基线轮
                Some(old) => {
                    let (added, removed) = diff(&old, self.prev.get(&pid).unwrap());
                    events.extend(added.into_iter().map(|key| PollEvent {
                        pid,
                        verb: Verb::NetConnect,
                        key,
                    }));
                    events.extend(removed.into_iter().map(|key| PollEvent {
                        pid,
                        verb: Verb::NetClose,
                        key,
                    }));
                }
            }
        }
        // 已退出/移出 agent 树的 pid：残留 socket 视为 close。
        let stale: Vec<i32> = self
            .prev
            .keys()
            .filter(|pid| !seen.contains(pid))
            .copied()
            .collect();
        for pid in stale {
            if let Some(old) = self.prev.remove(&pid) {
                events.extend(old.into_iter().map(|key| PollEvent {
                    pid,
                    verb: Verb::NetClose,
                    key,
                }));
            }
        }
        events
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(proto: &str, local: &str, remote: &str) -> SocketKey {
        SocketKey {
            proto: proto.into(),
            local: local.into(),
            remote: remote.into(),
        }
    }

    #[test]
    fn socket_key_proto_mapping() {
        let mut s = crate::ffi::testutil::zeroed_socket();
        s.protocol = IPPROTO_TCP;
        s.family = AF_INET;
        s.lport = 8080;
        s.fport = 443;
        s.laddr[..9].copy_from_slice(b"127.0.0.1");
        s.faddr[..9].copy_from_slice(b"1.2.3.4\0\0");
        let k = SocketKey::from_socket(&s).unwrap();
        assert_eq!(k.proto, "tcp4");
        assert_eq!(k.local, "127.0.0.1:8080");
        assert_eq!(k.remote, "1.2.3.4:443");

        s.family = AF_INET6;
        assert_eq!(SocketKey::from_socket(&s).unwrap().proto, "tcp6");
        s.protocol = IPPROTO_UDP;
        s.family = AF_INET;
        assert_eq!(SocketKey::from_socket(&s).unwrap().proto, "udp4");
        s.protocol = 0;
        assert!(SocketKey::from_socket(&s).is_none());
    }

    #[test]
    fn diff_pure_logic() {
        let old: HashSet<_> = [key("tcp4", "l:1", "r:1"), key("tcp4", "l:2", "r:2")]
            .into_iter()
            .collect();
        let new: HashSet<_> = [key("tcp4", "l:2", "r:2"), key("tcp4", "l:3", "r:3")]
            .into_iter()
            .collect();
        let (added, removed) = diff(&old, &new);
        assert_eq!(added, vec![key("tcp4", "l:3", "r:3")]);
        assert_eq!(removed, vec![key("tcp4", "l:1", "r:1")]);
    }

    #[test]
    fn poller_baseline_then_diff() {
        let mut poller = NetPoller::new();
        let state =
            std::cell::RefCell::new(HashMap::from([(100, vec![key("tcp4", "l:1", "r:1")])]));
        let fetch = |pid: i32| state.borrow().get(&pid).cloned().unwrap_or_default();

        // 基线轮：无事件
        assert!(poller.poll(&[100], &fetch).is_empty());

        // 新连接
        state
            .borrow_mut()
            .get_mut(&100)
            .unwrap()
            .push(key("tcp4", "l:2", "r:2"));
        let events = poller.poll(&[100], &fetch);
        assert_eq!(
            events,
            vec![PollEvent {
                pid: 100,
                verb: Verb::NetConnect,
                key: key("tcp4", "l:2", "r:2"),
            }]
        );

        // 连接关闭
        state
            .borrow_mut()
            .insert(100, vec![key("tcp4", "l:1", "r:1")]);
        let events = poller.poll(&[100], &fetch);
        assert_eq!(
            events,
            vec![PollEvent {
                pid: 100,
                verb: Verb::NetClose,
                key: key("tcp4", "l:2", "r:2"),
            }]
        );
    }

    #[test]
    fn poller_stale_pid_emits_close() {
        let mut poller = NetPoller::new();
        let state: HashMap<i32, Vec<SocketKey>> =
            HashMap::from([(100, vec![key("tcp4", "l:1", "r:1")])]);
        let fetch = |pid: i32| state.get(&pid).cloned().unwrap_or_default();

        poller.poll(&[100], &fetch);
        // pid 100 移出轮询集合（进程退出）→ 残留 socket 记 close
        let events = poller.poll(&[], &fetch);
        assert_eq!(
            events,
            vec![PollEvent {
                pid: 100,
                verb: Verb::NetClose,
                key: key("tcp4", "l:1", "r:1"),
            }]
        );
    }
}
